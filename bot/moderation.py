# -*- coding: utf-8 -*-
"""Общие helper-функции для модерации: отправка на ревью, наказания, audit-лог."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Iterable, Optional

from aiogram import Bot
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.exceptions import TelegramBadRequest

from .config import settings
from .db.repositories import AuditRepo, PendingRepo, UserRepo

logger = logging.getLogger(__name__)

# Если admin/log чат — это форум-группа с темами, в .env можно указать
# ADMIN_CHAT_THREAD_ID/LOG_CHAT_THREAD_ID. Тогда сообщения уйдут именно
# в эту тему. Подкладываем как kwargs во все Bot API вызовы.
_ADMIN_KW: dict = (
    {"message_thread_id": settings.admin_chat_thread_id}
    if settings.admin_chat_thread_id
    else {}
)
_LOG_KW: dict = (
    {"message_thread_id": settings.log_chat_thread_id}
    if settings.log_chat_thread_id
    else {}
)


def fmt_user(user_id: int, full_name: Optional[str], username: Optional[str]) -> str:
    name = escape(full_name or "—")
    handle = f"@{username}" if username else f"id{user_id}"
    return f'<a href="tg://user?id={user_id}">{name}</a> ({handle})'


def review_keyboard(review_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для админ-чата."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Разрешить URL", callback_data=f"mod:allow:{review_id}"),
                InlineKeyboardButton(text="🚫 Запретить URL", callback_data=f"mod:block:{review_id}"),
            ],
            [
                InlineKeyboardButton(text="🔇 Игнорировать", callback_data=f"mod:ignore:{review_id}"),
                InlineKeyboardButton(text="🔨 Бан + удалить", callback_data=f"mod:ban:{review_id}"),
            ],
        ]
    )


async def send_to_review(
    bot: Bot,
    message: Message,
    domains: Iterable[str],
    words: Iterable[str],
    delete_original: bool,
    is_trusted: bool,
    user_record,
) -> int:
    """
    Пересылает сообщение в админ-чат, опционально удаляет оригинал и
    создаёт pending_review с инлайн-кнопками. Возвращает id review.

    Порядок действий важен:
      1. forward в админ-чат (пока оригинал ещё на месте)
      2. (если delete_original=True) удалить оригинал в чате
      3. отправить info-сообщение с кнопками реплаем на пересланное

    Без шага 1 до шага 2 Telegram вернёт "message to forward not found".
    """
    domains_list = list(domains)
    words_list = list(words)
    text = message.text or message.caption or ""

    # ── 1. Пересылаем оригинал ──
    fwd: Optional[Message] = None
    try:
        fwd = await bot.forward_message(
            chat_id=settings.admin_chat_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
            **_ADMIN_KW,
        )
    except TelegramBadRequest as e:
        # Forward может не работать если включена защита контента или
        # сообщение специфическое. Попробуем copy_message как фолбэк.
        logger.warning("forward failed (%s), trying copy_message", e)
        try:
            copied = await bot.copy_message(
                chat_id=settings.admin_chat_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
                **_ADMIN_KW,
            )
            # copy_message возвращает MessageId, а не Message — оборачиваем
            class _Stub:
                message_id = copied.message_id
            fwd = _Stub()  # type: ignore
        except TelegramBadRequest as e2:
            logger.warning("copy_message also failed (%s), sending text copy", e2)
            try:
                fwd = await bot.send_message(
                    settings.admin_chat_id,
                    f"(не удалось переслать оригинал)\n\n{escape(text)[:3500]}",
                    **_ADMIN_KW,
                )
            except TelegramBadRequest as e3:
                # admin_chat_id скорее всего невалидный
                logger.error(
                    "send_message в admin_chat_id=%s провалился: %s. "
                    "Проверь ADMIN_CHAT_ID в .env и что бот добавлен в этот чат.",
                    settings.admin_chat_id, e3,
                )
                fwd = None

    # ── 2. Удаляем оригинал если нужно ──
    deleted = False
    if delete_original:
        deleted = await safe_delete(bot, message.chat.id, message.message_id)

    # ── 3. Создаём запись pending_review ──
    review_id = await PendingRepo.create(
        chat_id=message.chat.id,
        message_id=message.message_id,
        user_id=user_record.user_id,
        text=text,
        domains=domains_list,
        words=words_list,
        deleted=deleted,
        chat_thread_id=message.message_thread_id,
    )

    # ── 4. Отправляем info с кнопками ──
    status = (
        "🟡 <b>На модерации (доверенный, не удалено)</b>"
        if (is_trusted and not deleted)
        else "🔴 <b>На модерации (удалено)</b>"
    )
    user_str = fmt_user(user_record.user_id, user_record.full_name, user_record.username)
    trust_html = user_record.trust_status_human(
        settings.trust_min_hours,
        settings.trust_min_messages,
        settings.trust_min_interval_minutes * 60,
    )
    info = (
        f"{status}\n"
        f"👤 {user_str}\n"
        f"📊 Сообщений: <b>{user_record.message_count}</b>\n"
        f"⚠️ Предупреждений: <b>{user_record.warns}/{settings.warn_ban_at}</b>\n"
        f"🛡 Доверие: {trust_html}\n"
    )
    if domains_list:
        info += f"🔗 Домены: <code>{escape(', '.join(domains_list))}</code>\n"
    if words_list:
        info += f"🚫 Слова: <code>{escape(', '.join(words_list))}</code>\n"
    info += f"💬 Чат: <code>{escape(message.chat.title or str(message.chat.id))}</code>"

    try:
        admin_msg = await bot.send_message(
            chat_id=settings.admin_chat_id,
            text=info,
            reply_markup=review_keyboard(review_id),
            reply_to_message_id=fwd.message_id if fwd else None,
            parse_mode="HTML",
            **_ADMIN_KW,
        )
        await PendingRepo.set_admin_msg_id(review_id, admin_msg.message_id)
    except TelegramBadRequest as e:
        logger.error(
            "Не удалось отправить info в admin_chat_id=%s: %s. "
            "Запись %s создана, но кнопки модерации недоступны.",
            settings.admin_chat_id, e, review_id,
        )
    return review_id


async def punish_new_user(bot: Bot, chat_id: int, user_id: int, reason: str) -> str:
    """
    Применяет к новому пользователю наказание из конфига (ban или mute).
    Возвращает применённое действие.
    """
    if settings.new_user_punishment == "ban":
        try:
            await bot.ban_chat_member(chat_id, user_id)
            await UserRepo.set_banned(user_id, True)
            await AuditRepo.log(None, user_id, "ban", reason)
            return "ban"
        except Exception as e:
            logger.error("ban failed: %s", e)
            return "ban_failed"
    else:
        until = datetime.now(timezone.utc) + timedelta(minutes=settings.mute_duration_minutes)
        from aiogram.types import ChatPermissions
        try:
            await bot.restrict_chat_member(
                chat_id,
                user_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until,
            )
            await AuditRepo.log(None, user_id, "mute", f"{reason} ({settings.mute_duration_minutes}m)")
            return "mute"
        except Exception as e:
            logger.error("mute failed: %s", e)
            return "mute_failed"


async def safe_delete(bot: Bot, chat_id: int, message_id: int) -> bool:
    """Удаляет сообщение, не падая на ошибках."""
    try:
        await bot.delete_message(chat_id, message_id)
        return True
    except TelegramBadRequest as e:
        logger.warning("delete failed: %s", e)
        return False


async def _delete_after(bot: Bot, chat_id: int, message_id: int, delay: int,
                        thread_id: Optional[int] = None) -> None:
    """Удаляет сообщение через delay секунд (для TTL-уведомлений)."""
    import asyncio
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass


async def apply_warn(
    bot: Bot,
    chat_id: int,
    chat_thread_id: Optional[int],
    user_id: int,
    full_name: Optional[str],
    username: Optional[str],
    reason: str,
) -> tuple[int, str]:
    """
    Унифицированная выдача предупреждения с эскалацией:
      * При каждом варне >= WARN_RESET_TRUST_AT (3 по умолчанию) — сброс доверия
      * WARN_MUTE_AT (5 по умолчанию) → авто-мут на WARN_MUTE_HOURS
      * WARN_BAN_AT (7 по умолчанию) → бан

    Каждое предупреждение живёт WARN_TTL_DAYS дней и потом списывается.

    Возвращает (warns, action) где action — основное действие в чате:
      'warn'        — только предупреждение (доверие могло быть сброшено)
      'reset_trust' — сброс доверия без других действий
      'mute'        — мут (+ сброс доверия выполнен)
      'ban'         — бан (+ сброс доверия выполнен)
    """
    import asyncio
    from datetime import datetime, timedelta, timezone

    from .db.repositories import WarnRepo

    # 1. Записываем предупреждение
    warns = await WarnRepo.add(user_id, reason, chat_id, settings.warn_ttl_days)

    # 2. Сброс доверия — выполняется ВСЕГДА при варне ≥ порога,
    # независимо от мута/бана. То есть при 3, 4, 5, 6, 7 варнах сброс выполнится.
    trust_was_reset = False
    if warns >= settings.warn_reset_trust_at:
        try:
            await UserRepo.reset_trust(user_id)
            await AuditRepo.log(None, user_id, "trust_reset",
                                f"warns:{warns}/{settings.warn_reset_trust_at}")
            trust_was_reset = True
        except Exception as e:
            logger.error("trust reset failed: %s", e)

    # 3. Эскалация физических наказаний (мут / бан)
    action = "warn"
    if warns >= settings.warn_ban_at:
        try:
            await bot.ban_chat_member(chat_id, user_id)
            await UserRepo.set_banned(user_id, True)
            await AuditRepo.log(None, user_id, "auto_ban",
                                f"warns_exceeded:{warns}/{settings.warn_ban_at}")
            action = "ban"
        except Exception as e:
            logger.error("auto-ban failed: %s", e)

    elif warns >= settings.warn_mute_at:
        try:
            from aiogram.types import ChatPermissions
            until = datetime.now(timezone.utc) + timedelta(hours=settings.warn_mute_hours)
            await bot.restrict_chat_member(
                chat_id, user_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until,
            )
            await AuditRepo.log(None, user_id, "auto_mute",
                                f"warns:{warns}/{settings.warn_mute_at}, "
                                f"{settings.warn_mute_hours}h")
            action = "mute"
        except Exception as e:
            logger.error("auto-mute failed: %s", e)

    elif trust_was_reset:
        action = "reset_trust"

    # 4. Уведомление юзера в чат
    if settings.notify_on_warn:
        name_html = escape(full_name or str(user_id))
        handle = f" (@{username})" if username else ""
        user_mention = (
            f'<a href="tg://user?id={user_id}">{name_html}</a>{escape(handle)}'
        )
        # Хвост про сброс доверия — добавляем если случился вместе с мутом/баном
        trust_note = ""
        if trust_was_reset and action in {"mute", "ban"}:
            trust_note = "\nДоверие сброшено."

        if action == "ban":
            text = (
                f"🔨 {user_mention}, набрано <b>{warns}</b> предупреждений "
                f"(порог {settings.warn_ban_at}) — <b>бан</b>.\n"
                f"Причина: <i>{escape(reason)}</i>{trust_note}"
            )
        elif action == "mute":
            text = (
                f"🔇 {user_mention}, набрано <b>{warns}</b> предупреждений "
                f"(порог {settings.warn_mute_at}) — <b>мут на "
                f"{settings.warn_mute_hours} ч</b>.\n"
                f"Причина: <i>{escape(reason)}</i>{trust_note}"
            )
        elif action == "reset_trust":
            text = (
                f"⚠️ {user_mention}, твоё сообщение удалено.\n"
                f"Причина: <i>{escape(reason)}</i>\n"
                f"Предупреждений: <b>{warns}/{settings.warn_ban_at}</b> — "
                f"<b>доверие сброшено</b>. Дальше {settings.warn_mute_at} = мут, "
                f"{settings.warn_ban_at} = бан."
            )
        else:
            text = (
                f"⚠️ {user_mention}, твоё сообщение удалено.\n"
                f"Причина: <i>{escape(reason)}</i>\n"
                f"Предупреждений: <b>{warns}/{settings.warn_ban_at}</b> "
                f"(каждое на {settings.warn_ttl_days} дней)"
            )

        notify_kwargs: dict = {"parse_mode": "HTML"}
        if chat_thread_id:
            notify_kwargs["message_thread_id"] = chat_thread_id

        try:
            msg = await bot.send_message(chat_id, text, **notify_kwargs)
            if settings.warn_notification_ttl_seconds > 0:
                asyncio.create_task(
                    _delete_after(bot, chat_id, msg.message_id,
                                  settings.warn_notification_ttl_seconds)
                )
        except Exception as e:
            logger.warning("notify_warned_user failed: %s", e)

    return warns, action


async def log_to_channel(bot: Bot, text: str) -> None:
    """Пишет в LOG_CHAT_ID. Падать не должны если канал недоступен."""
    try:
        await bot.send_message(settings.log_chat_id, text, parse_mode="HTML", **_LOG_KW)
    except Exception as e:
        logger.warning("log_to_channel failed: %s", e)