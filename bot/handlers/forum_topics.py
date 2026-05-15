# -*- coding: utf-8 -*-
"""Слушаем события форум-чата чтобы знать имена тем (для рейтинга разделов)."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import Message

from ..config import settings
from ..db.repositories import ForumTopicRepo

logger = logging.getLogger(__name__)
router = Router(name="forum_topics")


@router.message(F.chat.id.in_(settings.protected_chat_ids), F.forum_topic_created)
async def on_topic_created(message: Message) -> None:
    if not message.forum_topic_created or not message.message_thread_id:
        return
    name = message.forum_topic_created.name
    await ForumTopicRepo.set_name(message.chat.id, message.message_thread_id, name)
    logger.info("Тема %s в чате %s: %s", message.message_thread_id, message.chat.id, name)


@router.message(F.chat.id.in_(settings.protected_chat_ids), F.forum_topic_edited)
async def on_topic_edited(message: Message) -> None:
    if not message.forum_topic_edited or not message.message_thread_id:
        return
    name = message.forum_topic_edited.name
    if name:
        await ForumTopicRepo.set_name(message.chat.id, message.message_thread_id, name)
        logger.info("Тема %s переименована в %s", message.message_thread_id, name)
