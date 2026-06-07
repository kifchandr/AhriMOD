# -*- coding: utf-8 -*-
"""Обработка вступления в чат: CAS-проверка, проверка имени на ссылки."""
from __future__ import annotations

import logging
import time
from html import escape
from typing import Optional, Tuple

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    User,
)

from ..config import settings
from ..db.repositories import AuditRepo, UserRepo
from ..moderation import fmt_user, log_to_channel
from ..services.cas import cas_check
from ..services.link_extractor import _URL_RE, _USERNAME_RE  # type: ignore
from ..services.text_normalizer import normalize_for_compare

logger = logging.getLogger(__name__)
router = Router(name="new_member")


def _name_looks_like_spam(*parts: str) -> bool:
    """Проверка имени/био на типичный спам: ссылки, упоминания каналов."""
    joined = " ".join(p for p in parts if p)
    if not joined:
        return False
    normalized = normalize_for_compare(joined)
    if _URL_RE.search(normalized):
        return True
    if _USERNAME_RE.search(normalized):
        return True
    return False


async def _collect_user_details(bot: Bot, user: User) -> Tuple[bool, Optional[str]]:
    """
    Возвращает (has_avatar, bio).
    bio доступен не всегда — Telegram API может вернуть None или ошибку
    в зависимости от настроек приватности и того, общался ли юзер с ботом.
    """
    has_avatar = False
    try:
        photos = await bot.get_user_profile_photos(user.id, limit=1)
        has_avatar = (photos.total_count or 0) > 0
    except Exception as e:
        logger.debug("get_user_profile_photos failed for %s: %s", user.id, e)

    bio: Optional[str] = None
    try:
        chat = await bot.get_chat(user.id)
        bio = getattr(chat, "bio", None)
    except Exception as e:
        # обычно "chat not found" если юзер не общался с ботом — это нормально
        logger.debug("get_chat for user %s failed: %s", user.id, e)

    return has_avatar, bio


async def _notify_new_member(bot: Bot, user: User) -> None:
    """Шлёт расширенное уведомление о новом участнике в выбранную тему."""
    if not settings.notify_on_new_member:
        return

    has_avatar, bio = await _collect_user_details(bot, user)
    is_premium = bool(getattr(user, "is_premium", False))

    # Красные флаги — обычно у спам-аккаунтов
    flags = []
    if not user.username:
        flags.append("без @username")
    if not has_avatar:
        flags.append("без аватара")
    if not bio:
        flags.append("без bio")

    user_str = fmt_user(user.id, user.full_name, user.username)

    lines = [
        f"➕ <b>Новый участник</b>",
        f"👤 {user_str}",
        f"🆔 <code>{user.id}</code>",
        f"✨ Premium: {'✅' if is_premium else '❌'}    "
        f"🖼 Аватар: {'✅' if has_avatar else '❌'}    "
        f"📝 Bio: {'✅' if bio else '❌'}",
    ]
    if bio:
        # обрезаем bio до 200 символов чтобы не растягивать сообщение
        bio_short = bio[:200] + ("…" if len(bio) > 200 else "")
        lines.append(f"   <i>{escape(bio_short)}</i>")
    if flags:
        lines.append(f"⚠️ Признаки: {', '.join(flags)}")

    text = "\n".join(lines)

    # Куда слать: NEW_MEMBER_THREAD_ID если задан, иначе LOG_CHAT_THREAD_ID
    target_thread = settings.new_member_thread_id or settings.log_chat_thread_id
    kwargs: dict = {"parse_mode": "HTML"}
    if target_thread:
        kwargs["message_thread_id"] = target_thread

    try:
        await bot.send_message(settings.log_chat_id, text, **kwargs)
    except Exception as e:
        logger.warning("notify_new_member send failed: %s", e)


async def _send_name_review(bot: Bot, event: ChatMemberUpdated, user: User) -> None:
    """
    Вместо авто-бана за ссылку в имени — отправляет карточку в админ-чат
    с кнопками «Забанить» / «Оставить». Пользователь остаётся в чате до решения.
    """
    user_str = fmt_user(user.id, user.full_name, user.username)
    text = (
        f"⚠️ <b>Ссылка в имени нового участника</b>\n"
        f"👤 {user_str}\n"
        f"🆔 <code>{user.id}</code>\n"
        f"💬 Чат: <code>{escape(event.chat.title or str(event.chat.id))}</code>\n"
        f"\nПользователь оставлен в чате до решения модератора."
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔨 Забанить",
                    callback_data=f"joinmod:ban:{user.id}:{event.chat.id}",
                ),
                InlineKeyboardButton(
                    text="✅ Оставить",
                    callback_data=f"joinmod:keep:{user.id}:{event.chat.id}",
                ),
            ]
        ]
    )
    kwargs: dict = {"parse_mode": "HTML", "reply_markup": keyboard}
    if settings.admin_chat_thread_id:
        kwargs["message_thread_id"] = settings.admin_chat_thread_id

    try:
        await bot.send_message(settings.admin_chat_id, text, **kwargs)
        await AuditRepo.log(None, user.id, "review", "spam_in_name → ручная модерация")
    except Exception as e:
        logger.error("name-review send failed: %s", e)


@router.chat_member(F.chat.id.in_(settings.protected_chat_ids))
async def on_chat_member(event: ChatMemberUpdated, bot: Bot) -> None:
    # Интересуют только переходы в "пришёл в чат"
    old = event.old_chat_member.status
    new = event.new_chat_member.status
    if not (old in {"left", "kicked"} and new in {"member", "restricted"}):
        return

    user = event.new_chat_member.user
    if user.is_bot:
        return

    # Зарегистрируем юзера в БД с актуальным join_at
    await UserRepo.get_or_create(
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
        join_at_if_new=int(time.time()),
    )

    # 1) CAS проверка
    if settings.use_cas:
        cas_result = await cas_check(user.id)
        if cas_result is True:
            try:
                await bot.ban_chat_member(event.chat.id, user.id)
                await UserRepo.set_banned(user.id, True)
                await AuditRepo.log(None, user.id, "ban", "cas_blacklist")
                await log_to_channel(
                    bot,
                    f"🛡 CAS-бан при входе: <code>{user.id}</code> ({user.full_name})",
                )
                return
            except Exception as e:
                logger.error("CAS ban failed: %s", e)

    # 2) Проверка имени на ссылки — НЕ баним, отправляем на ручную модерацию.
    if _name_looks_like_spam(user.first_name or "", user.last_name or "", user.username or ""):
        await _send_name_review(bot, event, user)
        return

    # 3) Уведомление о новом участнике — один раз на аккаунт.
    #    try_mark_welcomed атомарно вернёт True лишь при первом вступлении, поэтому
    #    повторные входы и дубли chat_member-апдейтов больше не плодят сообщений.
    #    Флаг welcomed постоянный и при перезаходе не сбрасывается (запись юзера
    #    не удаляется), так что одного и того же человека не приветствуем дважды.
    if settings.notify_on_new_member and await UserRepo.try_mark_welcomed(user.id):
        await _notify_new_member(bot, user)


@router.callback_query(F.data.startswith("joinmod:"))
async def on_joinmod_callback(cb: CallbackQuery, bot: Bot) -> None:
    """Кнопки из карточки «ссылка в имени»: ban / keep. Только для модераторов."""
    if cb.data is None or cb.from_user is None:
        return
    if cb.from_user.id not in settings.admin_user_ids:
        await cb.answer("Только для модераторов", show_alert=True)
        return

    try:
        _, action, uid_s, cid_s = cb.data.split(":", 3)
        target_user_id = int(uid_s)
        chat_id = int(cid_s)
    except ValueError:
        await cb.answer("Битый callback")
        return

    if action == "ban":
        try:
            await bot.ban_chat_member(chat_id, target_user_id)
            await UserRepo.set_banned(target_user_id, True)
            await AuditRepo.log(cb.from_user.id, target_user_id, "ban", "spam_in_name (ручной)")
            summary = f"🔨 Забанен <code>{target_user_id}</code>"
        except Exception as e:
            logger.error("joinmod ban failed: %s", e)
            await cb.answer("Не удалось забанить (проверь права бота)", show_alert=True)
            return
    elif action == "keep":
        await AuditRepo.log(cb.from_user.id, target_user_id, "review_ignore", "spam_in_name оставлен")
        summary = f"✅ Оставлен <code>{target_user_id}</code>"
    else:
        await cb.answer("Неизвестное действие")
        return

    # Обновляем карточку: убираем кнопки, дописываем итог и кто решил.
    try:
        if cb.message:
            new_text = (
                (cb.message.html_text or "")
                + f"\n\n<b>{summary}</b>\n👮 {escape(cb.from_user.full_name)}"
            )
            await cb.message.edit_text(new_text, reply_markup=None, parse_mode="HTML")
    except TelegramBadRequest:
        pass

    await cb.answer("Готово")