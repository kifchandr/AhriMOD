# -*- coding: utf-8 -*-
"""FAQ-автоответчик.

Загружает все триггеры в память. На каждое сообщение проверяет, содержит ли
оно один из триггеров (substring в нижнем регистре). При срабатывании
возвращает (id, answer). Для одного и того же FAQ в одном чате не отвечает
чаще раза в COOLDOWN_SECONDS.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from .text_normalizer import normalize_for_compare
from ..db.repositories import FaqRepo

logger = logging.getLogger(__name__)


class FaqService:
    def __init__(self, cooldown_seconds: int = 600):
        self._items: list[tuple[int, list[str], str]] = []
        self._last_match: dict[tuple[int, int], float] = {}
        self.cooldown_seconds = cooldown_seconds

    async def reload(self) -> int:
        rows = await FaqRepo.list_all()
        self._items = []
        for row in rows:
            triggers_raw = row.get("triggers_list") or []
            # Нормализуем триггеры так же как нормализуем входной текст —
            # иначе кириллические триггеры не будут совпадать с гомоглифами.
            triggers = [normalize_for_compare(t) for t in triggers_raw if t]
            triggers = [t for t in triggers if t]
            if triggers and row.get("answer"):
                self._items.append((row["id"], triggers, row["answer"]))
        logger.info("FaqService: загружено %d записей", len(self._items))
        return len(self._items)

    def find(self, text: str, chat_id: int) -> Optional[tuple[int, str]]:
        if not text or not self._items:
            return None
        normalized = normalize_for_compare(text)
        now = time.time()
        for faq_id, triggers, answer in self._items:
            for trig in triggers:
                if trig in normalized:
                    last = self._last_match.get((chat_id, faq_id), 0.0)
                    if now - last < self.cooldown_seconds:
                        return None
                    self._last_match[(chat_id, faq_id)] = now
                    return (faq_id, answer)
        return None


faq_service: Optional[FaqService] = None


def init_faq_service(cooldown_minutes: int) -> FaqService:
    global faq_service
    faq_service = FaqService(cooldown_seconds=cooldown_minutes * 60)
    return faq_service
