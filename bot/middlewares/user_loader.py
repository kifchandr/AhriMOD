# -*- coding: utf-8 -*-
"""Middleware для подгрузки UserRecord в data хендлеров."""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject, ChatMemberUpdated

from ..config import settings
from ..db.repositories import UserRepo


class UserLoaderMiddleware(BaseMiddleware):
    """
    Загружает запись пользователя из БД и кладёт её в data['user_record'].
    Также определяет is_admin (по списку из конфига).
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        if tg_user is None:
            return await handler(event, data)

        full_name = tg_user.full_name if hasattr(tg_user, "full_name") else None
        record = await UserRepo.get_or_create(
            user_id=tg_user.id,
            username=tg_user.username,
            full_name=full_name,
        )
        data["user_record"] = record
        data["is_admin"] = tg_user.id in settings.admin_user_ids

        return await handler(event, data)
