# -*- coding: utf-8 -*-
"""Combot Anti-Spam API: проверка пользователей по глобальному блэклисту."""
from __future__ import annotations

import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

CAS_URL = "https://api.cas.chat/check"


async def cas_check(user_id: int, timeout: float = 5.0) -> Optional[bool]:
    """
    True  — юзер в блэклисте (бан рекомендован)
    False — чист
    None  — не удалось проверить (ошибка/таймаут), не блокируем
    """
    try:
        timeout_obj = aiohttp.ClientTimeout(total=timeout)
        async with aiohttp.ClientSession(timeout=timeout_obj) as session:
            async with session.get(CAS_URL, params={"user_id": user_id}) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return bool(data.get("ok"))
    except Exception as e:
        logger.warning("CAS check failed for %s: %s", user_id, e)
        return None
