# -*- coding: utf-8 -*-
"""Обработка нажатий на инлайн-кнопки в сообщениях модерации."""
from __future__ import annotations

import json
import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery
from aiogram.exceptions import TelegramBadRequest

from ..config import settings
from ..db.repositories import (
    AuditRepo,
    DomainRepo,
    PendingRepo,
    UserRepo,
    WordRepo,
)
from ..moderation import apply_warn, safe_delete
from ..services.content_filter import word_filter
from ..services.signature import signature_service

logger = logging.getLogger(__name__)
router = Router(name="moderation_callbacks")


def _is_admin(user_id: int) -> bool:
    return user_id in settings.admin_user_ids


def _thread_id_of(review: dict) -> int | None:
    """Возвращает chat_thread_id оригинала (для постинга уведомления в ту же тему)."""
    return review.get("chat_thread_id")


@router.callback_query(F.data.startswith("mod:"))
async def on_mod_callback(cb: CallbackQuery, bot: Bot) -> None:
    if cb.data is None or cb.from_user is None:
        return
    if not _is_admin(cb.from_user.id):
        await cb.answer("Только для модераторов", show_alert=True)
        return

    try:
        _, action, review_id_s = cb.data.split(":", 2)
        review_id = int(review_id_s)
    except ValueError:
        await cb.answer("Битый callback")
        return

    review = await PendingRepo.get(review_id)
    if review is None:
        await cb.answer("Запись не найдена")
        return
    if review.get("resolved_at"):
        await cb.answer("Уже обработано")
        return

    domains: list[str] = json.loads(review.get("domains") or "[]")
    words: list[str] = json.loads(review.get("words") or "[]")
    chat_id = review["chat_id"]
    msg_id = review["message_id"]
    target_user_id = review["user_id"]
    text = review.get("text") or ""
    actor_id = cb.from_user.id

    summary = ""

    if action == "allow":
        # все домены → whitelist, все слова → удалить из blacklist (whitelist для слов
        # не имеет смысла — мы и так ловим только blocked)
        for d in domains:
            await DomainRepo.set_status(d, "allowed", actor_id)
        for w in words:
            await WordRepo.remove(w)
        await word_filter.reload()
        # сообщение в чате не трогаем — если было удалено, восстановить можно только
        # пересылкой (но это испортит контекст). Оставляем как есть.
        summary = f"✅ Разрешено: <code>{escape(', '.join(domains + words))}</code>"
        await AuditRepo.log(actor_id, target_user_id, "review_allow",
                            f"domains={domains};words={words}")

    elif action == "block":
        for d in domains:
            await DomainRepo.set_status(d, "blocked", actor_id)
        for w in words:
            await WordRepo.set_status(w, "blocked", actor_id)
        await word_filter.reload()
        # удаляем сообщение если ещё висит
        await safe_delete(bot, chat_id, msg_id)
        summary = f"❌ Заблокировано: <code>{escape(', '.join(domains + words))}</code>"
        await AuditRepo.log(actor_id, target_user_id, "review_block",
                            f"domains={domains};words={words}")

    elif action == "ban":
        # бан + удаление + добавление сигнатуры
        await safe_delete(bot, chat_id, msg_id)
        try:
            await bot.ban_chat_member(chat_id, target_user_id)
        except TelegramBadRequest as e:
            logger.warning("ban via callback failed: %s", e)
        await UserRepo.set_banned(target_user_id, True)
        # домены — в blacklist, чтобы и от других ловилось
        for d in domains:
            await DomainRepo.set_status(d, "blocked", actor_id)
        for w in words:
            await WordRepo.set_status(w, "blocked", actor_id)
        await word_filter.reload()
        # сигнатура от текста сообщения
        if signature_service and text:
            await signature_service.add(text, actor_id)
        summary = f"🔨 Забанен <code>{target_user_id}</code> + сигнатура добавлена"
        await AuditRepo.log(actor_id, target_user_id, "review_ban", text[:200])

    elif action == "warn":
        await safe_delete(bot, chat_id, msg_id)
        user_rec = await UserRepo.get_or_create(target_user_id, None, None)
        warns, act = await apply_warn(
            bot, chat_id, _thread_id_of(review), target_user_id,
            user_rec.full_name, user_rec.username, "нарушение правил чата",
        )
        suffix = {
            "ban": " → авто-бан",
            "mute": f" → мут {settings.warn_mute_hours}ч",
            "reset_trust": " → доверие сброшено",
            "warn": "",
        }.get(act, "")
        summary = f"⚠️ Предупреждение ({warns}/{settings.warn_ban_at}){suffix}"
        await AuditRepo.log(actor_id, target_user_id, "review_warn", str(warns))

    elif action == "block_warn":
        for d in domains:
            await DomainRepo.set_status(d, "blocked", actor_id)
        for w in words:
            await WordRepo.set_status(w, "blocked", actor_id)
        await word_filter.reload()
        await safe_delete(bot, chat_id, msg_id)
        user_rec = await UserRepo.get_or_create(target_user_id, None, None)
        reason_label = ", ".join(domains + words) or "запрещённый контент"
        warns, act = await apply_warn(
            bot, chat_id, _thread_id_of(review), target_user_id,
            user_rec.full_name, user_rec.username, reason_label,
        )
        suffix = {
            "ban": " → авто-бан",
            "mute": f" → мут {settings.warn_mute_hours}ч",
            "reset_trust": " → доверие сброшено",
            "warn": "",
        }.get(act, "")
        summary = (f"❌ Блок: <code>{escape(', '.join(domains + words))}</code>"
                   f"\n⚠️ Предупреждение ({warns}/{settings.warn_ban_at}){suffix}")
        await AuditRepo.log(actor_id, target_user_id, "review_block_warn",
                            f"domains={domains};words={words};warns={warns}")

    elif action == "ignore":
        summary = "🔇 Проигнорировано (без действий)"
        await AuditRepo.log(actor_id, target_user_id, "review_ignore", "")

    else:
        await cb.answer("Неизвестное действие")
        return

    await PendingRepo.resolve(review_id, action)

    # Обновляем сообщение в админ-чате: убираем кнопки, дописываем результат
    try:
        if cb.message:
            new_text = (cb.message.html_text or "") + f"\n\n<b>{summary}</b>\n👮 {cb.from_user.full_name}"
            await cb.message.edit_text(new_text, reply_markup=None, parse_mode="HTML")
    except TelegramBadRequest:
        pass

    await cb.answer("Готово")
