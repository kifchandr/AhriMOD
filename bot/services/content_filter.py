# -*- coding: utf-8 -*-
"""Поиск стоп-слов в тексте по базе из БД."""
from __future__ import annotations

import re
from typing import Iterable

from ..db.repositories import WordRepo
from .text_normalizer import aggressive_translit, normalize_for_compare


_LATIN_ONLY = re.compile(r"^[a-z0-9 .\-_]+$")


class WordFilter:
    """
    Кешируется в памяти, обновляется по запросу.
    Для каждого стоп-слова формирует набор паттернов и проверяет их против:
      1. Обычной нормализованной формы текста
      2. "Сжатой" формы (без пробелов/разделителей) — ловит "v p n" вместо "vpn"
      3. Если стоп-слово только из латиницы — ещё и против транслитерированной формы
         (ловит обходы типа "nordvпн" → "nordvpn")
    """

    def __init__(self) -> None:
        self._originals: list[str] = []     # как было сохранено в БД (для отображения)
        self._normalized: list[str] = []    # для матчинга
        self._patterns: list[re.Pattern] = []
        self._is_latin: list[bool] = []
        self._loaded = False

    async def reload(self) -> None:
        words = await WordRepo.all_blocked()
        self._originals = []
        self._normalized = []
        self._patterns = []
        self._is_latin = []
        for original in words:
            if not original.strip():
                continue
            norm = normalize_for_compare(original)
            if not norm:
                continue
            self._originals.append(original)
            self._normalized.append(norm)
            escaped = re.escape(norm)
            if len(norm) <= 4 or " " not in norm:
                pattern = rf"(?<![a-zа-я0-9_]){escaped}(?![a-zа-я0-9_])"
            else:
                pattern = escaped
            self._patterns.append(re.compile(pattern, re.IGNORECASE | re.UNICODE))
            self._is_latin.append(bool(_LATIN_ONLY.match(norm)))
        self._loaded = True

    async def find(self, text: str) -> list[str]:
        """Возвращает список совпавших стоп-слов (в оригинальной форме из БД)."""
        if not self._loaded:
            await self.reload()
        if not text or not self._patterns:
            return []
        normalized = normalize_for_compare(text)
        compact = re.sub(r"[\s\-_.·•]+", "", normalized)
        translit = aggressive_translit(text)
        translit_compact = re.sub(r"[\s\-_.·•]+", "", translit)

        matched: list[str] = []
        for original, norm, pattern, is_latin in zip(
            self._originals, self._normalized, self._patterns, self._is_latin
        ):
            if pattern.search(normalized):
                matched.append(original)
                continue
            norm_compact = norm.replace(" ", "")
            if len(norm_compact) >= 5 and norm_compact in compact:
                matched.append(original)
                continue
            if is_latin and len(norm_compact) >= 4:
                if norm_compact in translit_compact:
                    matched.append(original)
        return matched


# Глобальный экземпляр — один на процесс
word_filter = WordFilter()
