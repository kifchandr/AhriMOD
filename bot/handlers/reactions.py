# -*- coding: utf-8 -*-
"""
Модерация через реакции.

Когда админ ставит на сообщение реакцию из списка — бот выполняет действие:
  🚫 — удалить сообщение
  ❌ — удалить + предупреждение (через apply_warn → эскалация)
  🔨 — удалить + бан + добавить сигнатуру (для авто-бана похожих)

Условия:
1. Бот должен быть админом в защищаемом чате (Delete Messages + Ban Users)
2. message_reaction обязательно в allowed_updates у Dispatcher.start_polling
3. Группа не должна быть анонимной — иначе user_id ставящего не приходит
"""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.types import MessageReactionUpdated

from ..config import settings
from ..db.repositories import (
    AuditRepo,
    RecentMessagesRepo,
    UserRepo,
)
from ..moderation import apply_warn, log_to_channel, safe_delete
from ..services.signature import signature_service

logger = logging.getLogger(__name__)
router = Router(name="reactions")

REACTION_DELETE = {"🚫"}
REACTION_DELETE_AND_WARN = {"❌"}
REACTION_BAN = {"🔨"}


def _extract_emojis(reactions: list) -> set[str]:
    out: set[str] = set()
    for r in reactions or []:
        emoji = getattr(r, "emoji", None)
        if emoji:
            out.add(emoji)
    return out


@router.message_reaction(F.chat.id.in_(settings.protected_chat_ids))
async def on_reaction(event: MessageReactionUpdated, bot: Bot) -> None:
    if not event.user or event.user.id not in settings.admin_user_ids:
        return

    new_emojis = _extract_emojis(event.new_reaction)
    if not new_emojis:
        return

    record = await RecentMessagesRepo.get(event.chat.id, event.message_id)
    if not record:
        return
    target_user_id = record["user_id"]
    text = record.get("text") or ""

    if target_user_id in settings.admin_user_ids:
        return
    bot_me = await bot.get_me()
    if target_user_id == bot_me.id:
        return

    if new_emojis & REACTION_BAN:
        action = "ban"
    elif new_emojis & REACTION_DELETE_AND_WARN:
        action = "delete_warn"
    elif new_emojis & REACTION_DELETE:
        action = "delete"
    else:
        return

    user_rec = await UserRepo.get_or_create(target_user_id, None, None)

    if action == "delete":
        await safe_delete(bot, event.chat.id, event.message_id)
        await AuditRepo.log(event.user.id, target_user_id, "react_delete", "")
        await log_to_channel(
            bot,
            f"🗑 (реакция от админа) удалено сообщение от <code>{target_user_id}</code>",
        )

    elif action == "delete_warn":
        await safe_delete(bot, event.chat.id, event.message_id)
        warns, act = await apply_warn(
            bot, event.chat.id, None,
            target_user_id, user_rec.full_name, user_rec.username,
            "решение модератора (реакция)",
        )
        await AuditRepo.log(event.user.id, target_user_id, "react_warn", str(warns))
        tag = {"ban": "🔨 авто-бан", "mute": "🔇 авто-мут",
               "reset_trust": "🔄 сброс доверия", "warn": "⚠️ предупр."}.get(act, "⚠️")
        await log_to_channel(
            bot,
            f"{tag} (реакция от админа) <code>{target_user_id}</code> "
            f"({warns}/{settings.warn_ban_at})",
        )

    elif action == "ban":
        await safe_delete(bot, event.chat.id, event.message_id)
        try:
            await bot.ban_chat_member(event.chat.id, target_user_id)
            await UserRepo.set_banned(target_user_id, True)
        except Exception as e:
            logger.warning("ban via reaction failed: %s", e)
        if text:
            try:
                await signature_service.add_signature(text, event.user.id)
            except Exception as e:
                logger.warning("signature add failed: %s", e)
        await AuditRepo.log(event.user.id, target_user_id, "react_ban", "")
        await log_to_channel(
            bot,
            f"🔨 (реакция от админа) <b>бан</b> <code>{target_user_id}</code>"
            + (" + сигнатура" if text else ""),
        )
