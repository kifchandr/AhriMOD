# -*- coding: utf-8 -*-
"""Главный обработчик сообщений: проверка и модерация."""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.types import Message

from ..config import settings
from ..db.repositories import (
    AuditRepo,
    DomainRepo,
    FaqRepo,
    MessageStatsRepo,
    PendingRepo,
    RecentMessagesRepo,
    UserRepo,
)
from ..moderation import apply_warn, log_to_channel, punish_new_user, safe_delete, send_to_review
from ..services import faq as faq_module
from ..services.content_filter import word_filter
from ..services.link_extractor import extract_links
from ..services.signature import signature_service

logger = logging.getLogger(__name__)
router = Router(name="messages")


@router.message(F.chat.id.in_(settings.protected_chat_ids))
async def on_message(message: Message, bot: Bot, **data) -> None:
    await _moderate(message, bot, is_edit=False, **data)


@router.edited_message(F.chat.id.in_(settings.protected_chat_ids))
async def on_edited_message(message: Message, bot: Bot, **data) -> None:
    """
    Обработка отредактированных сообщений.

    Иначе спамер может отправить чистое сообщение, а ПОТОМ дописать в него
    ссылку/стоп-слово редактированием — и без этого хендлера бот бы ничего
    не заметил. Прогоняем те же проверки модерации, но НЕ инкрементим
    счётчики доверия/статистики (сообщение уже было засчитано при отправке).
    """
    await _moderate(message, bot, is_edit=True, **data)


async def _moderate(message: Message, bot: Bot, is_edit: bool, **data) -> None:
    user_record = data["user_record"]
    is_admin = data["is_admin"]

    # Админов не модерируем
    if is_admin:
        return

    # Заблокированных юзеров (manual block) — удаляем всё подряд
    if user_record.is_blocked or user_record.is_banned:
        await safe_delete(bot, message.chat.id, message.message_id)
        return

    text = message.text or message.caption or ""
    is_trusted = user_record.is_trusted(
        settings.trust_min_hours, settings.trust_min_messages
    )

    # ── Блокировка медиа от недоверенных ──
    # До любых других проверок: если RESTRICT_MEDIA_FOR_UNTRUSTED включена и
    # юзер ещё не доверенный, удаляем сообщение с фото/видео/кружком/гифкой
    # и выдаём предупреждение.
    if settings.restrict_media_for_untrusted and not is_trusted:
        is_blocked_media = bool(
            message.photo or message.video or message.video_note or message.animation
        )
        if is_blocked_media:
            await safe_delete(bot, message.chat.id, message.message_id)
            warns, act = await apply_warn(
                bot, message.chat.id, message.message_thread_id,
                user_record.user_id, user_record.full_name, user_record.username,
                "медиа от недоверенного пользователя",
            )
            tag = {"ban": "🔨 авто-бан", "mute": "🔇 авто-мут",
                   "reset_trust": "🔄 сброс доверия", "warn": "⚠️ предупр."}.get(act, "⚠️")
            await log_to_channel(
                bot,
                f"{tag} (медиа от недоверенного) <code>{user_record.user_id}</code> "
                f"({warns}/{settings.warn_ban_at})",
            )
            return

    # 1. Проверка по сигнатурам забаненных сообщений
    if signature_service and text:
        match = await signature_service.check(text)
        if match:
            await safe_delete(bot, message.chat.id, message.message_id)
            await punish_new_user(bot, message.chat.id, user_record.user_id, f"signature:{match}")
            await log_to_channel(
                bot,
                f"🔨 Авто-бан по сигнатуре ({match}): "
                f"<code>{user_record.user_id}</code>",
            )
            return

    # 2. Извлекаем ссылки и стоп-слова
    links = extract_links(message)
    bad_words = await word_filter.find(text) if text else []

    # 3. Если нет ни ссылок, ни стоп-слов — обычное сообщение
    if not links and not bad_words:
        # На редактировании ничего не начисляем повторно и FAQ не дублируем —
        # сообщение уже было обработано при отправке.
        if is_edit:
            return
        await UserRepo.increment_messages(
            user_record.user_id,
            trust_min_messages=settings.trust_min_messages,
            trust_min_interval_seconds=settings.trust_min_interval_minutes * 60,
        )
        await MessageStatsRepo.increment(
            user_record.user_id, message.chat.id, message.message_thread_id,
        )
        # Кэш для модерации через реакции
        await RecentMessagesRepo.add(
            message.chat.id, message.message_id, user_record.user_id, text,
        )
        # FAQ-автоответ
        if faq_module.faq_service and text:
            match = faq_module.faq_service.find(text, message.chat.id)
            if match:
                faq_id, answer = match
                try:
                    await message.reply(answer)
                    await FaqRepo.mark_used(faq_id)
                except Exception as e:
                    logger.warning("faq reply failed: %s", e)
        return

    # 4. Анализируем что у нас за ссылки/слова
    blocked_domains: list[str] = []
    pending_domains: list[str] = []      # неизвестные домены — на модерацию
    allowed_count = 0
    for link in links:
        status = await DomainRepo.get_status(link.domain)
        if status == "blocked":
            blocked_domains.append(link.domain)
        elif status == "allowed":
            allowed_count += 1
        else:
            pending_domains.append(link.domain)

    # Стоп-слова: blocked всегда плохо, allowed для слов не предусмотрено
    # (если слово не в blocked, оно не попадёт в bad_words вообще).
    # Но мы оставляем bad_words как есть — модератор решит что с ними.

    has_blocked = bool(blocked_domains) or bool(bad_words and not is_trusted)
    # Доверенные с явно забаненным доменом — варн + удаление, но не бан
    has_blocked_domain = bool(blocked_domains)

    # ───── Кейс A: явно запрещённый домен — предупреждение всем ─────
    if has_blocked_domain:
        await safe_delete(bot, message.chat.id, message.message_id)
        warns, act = await apply_warn(
            bot, message.chat.id, message.message_thread_id,
            user_record.user_id, user_record.full_name, user_record.username,
            f"запрещённый домен ({', '.join(blocked_domains)})",
        )
        await AuditRepo.log(None, user_record.user_id, "warn",
                            f"blacklisted_domain:{','.join(blocked_domains)}")
        tag = {"ban": "🔨 авто-бан", "mute": "🔇 авто-мут",
               "reset_trust": "🔄 сброс доверия", "warn": "⚠️ предупр."}.get(act, "⚠️")
        await log_to_channel(
            bot,
            f"{tag} <code>{user_record.user_id}</code> "
            f"({warns}/{settings.warn_ban_at}) за "
            f"<code>{','.join(blocked_domains)}</code>",
        )
        return

    # ───── Кейс B: стоп-слово у НЕ-доверенного → удалить + предупреждение ─────
    if bad_words and not is_trusted:
        await safe_delete(bot, message.chat.id, message.message_id)
        warns, act = await apply_warn(
            bot, message.chat.id, message.message_thread_id,
            user_record.user_id, user_record.full_name, user_record.username,
            f"стоп-слово ({', '.join(bad_words)})",
        )
        await AuditRepo.log(None, user_record.user_id, "warn",
                            f"stopword:{','.join(bad_words)}")
        tag = {"ban": "🔨 авто-бан", "mute": "🔇 авто-мут",
               "reset_trust": "🔄 сброс доверия", "warn": "⚠️ предупр."}.get(act, "⚠️")
        await log_to_channel(
            bot,
            f"{tag} <code>{user_record.user_id}</code> "
            f"({warns}/{settings.warn_ban_at}) за "
            f"<code>{','.join(bad_words)}</code>",
        )
        return

    # ───── Кейс C: всё известно (домены в whitelist) и нет стоп-слов ─────
    if not pending_domains and not bad_words:
        # все ссылки в whitelist — пропускаем
        if is_edit:
            return
        await UserRepo.increment_messages(
            user_record.user_id,
            trust_min_messages=settings.trust_min_messages,
            trust_min_interval_seconds=settings.trust_min_interval_minutes * 60,
        )
        await MessageStatsRepo.increment(
            user_record.user_id, message.chat.id, message.message_thread_id,
        )
        await RecentMessagesRepo.add(
            message.chat.id, message.message_id, user_record.user_id, text,
        )
        return

    # ───── Кейс D: что-то неизвестное — на ручную модерацию ─────
    # Если это правка и по сообщению уже есть необработанная заявка — не дублируем.
    if is_edit and await PendingRepo.has_unresolved_for_message(
        message.chat.id, message.message_id
    ):
        return

    if is_trusted:
        # Доверенный юзер: НЕ удаляем, только уведомляем модератора.
        # Модератор сам решит — оставить, удалить или забанить домен/слово.
        await send_to_review(
            bot, message,
            domains=pending_domains,
            words=bad_words,
            delete_original=False,
            is_trusted=True,
            user_record=user_record,
        )
    else:
        # Новый юзер: пересылаем в админ-чат и удаляем оригинал внутри send_to_review.
        # Порядок (forward → delete) важен: иначе оригинал уже удалён к моменту forward.
        await send_to_review(
            bot, message,
            domains=pending_domains,
            words=bad_words,
            delete_original=True,
            is_trusted=False,
            user_record=user_record,
        )

    # Сообщение засчитываем — иначе спамеры никогда не "перерастут" в trusted
    # (на самом деле спорно, но иначе доверенный, отправивший хоть одну ссылку,
    # никогда не получит +1 к счётчику; на trust это всё равно не повлияет
    # для уже-доверенного, а для нового — счётчик считается только при чистых
    # сообщениях, иначе можно накрутить через ссылки в whitelist).
    # Решение: НЕ инкрементим если что-то ушло на модерацию или забанено.