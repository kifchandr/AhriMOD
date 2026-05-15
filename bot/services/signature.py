# -*- coding: utf-8 -*-
"""Сигнатуры сообщений: точные (SHA256) и нечёткие (SimHash)."""
from __future__ import annotations

import hashlib
from typing import Optional

from simhash import Simhash

from ..db.repositories import SignatureRepo
from .text_normalizer import text_for_signature


def compute_sha256(text: str) -> str:
    """Точный хеш нормализованного текста."""
    norm = text_for_signature(text)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def compute_simhash(text: str) -> int:
    """SimHash 64-бит от нормализованного текста."""
    norm = text_for_signature(text)
    # делим на 2-граммы для лучшего качества на коротких текстах
    tokens = norm.split()
    if len(tokens) >= 2:
        features = [" ".join(tokens[i : i + 2]) for i in range(len(tokens) - 1)]
    else:
        features = tokens or [norm]
    return Simhash(features).value


def hamming(a: int, b: int) -> int:
    """Расстояние Хэмминга между двумя 64-битными числами."""
    return bin(a ^ b).count("1")


class SignatureService:
    """
    Хранит копию simhash'ей в памяти для быстрого сравнения.
    Точные хеши проверяются прямо в БД.
    """

    def __init__(self, threshold: int = 4, min_length: int = 30):
        self.threshold = threshold
        self.min_length = min_length
        self._simhashes: list[int] = []
        self._loaded = False

    async def reload(self) -> None:
        self._simhashes = await SignatureRepo.all_simhashes()
        self._loaded = True

    def is_too_short(self, text: str) -> bool:
        return len(text_for_signature(text)) < self.min_length

    async def check(self, text: str) -> Optional[str]:
        """
        Возвращает причину совпадения ('exact' / 'fuzzy:<distance>') или None.
        """
        if self.is_too_short(text):
            return None
        if not self._loaded:
            await self.reload()

        sha = compute_sha256(text)
        if await SignatureRepo.exists_exact(sha):
            return "exact"

        sh = compute_simhash(text)
        for stored in self._simhashes:
            dist = hamming(sh, stored)
            if dist <= self.threshold:
                return f"fuzzy:{dist}"
        return None

    async def add(self, text: str, added_by: int) -> bool:
        """
        Добавить сигнатуру по тексту в базу. Возвращает True если добавлено.
        Слишком короткие тексты не добавляем — слишком высока вероятность
        ложных срабатываний.
        """
        if self.is_too_short(text):
            return False
        sha = compute_sha256(text)
        sh = compute_simhash(text)
        await SignatureRepo.add(sha, sh, text, added_by)
        if sh not in self._simhashes:
            self._simhashes.append(sh)
        return True


# Глобальный экземпляр — параметры подставит main.py
signature_service: Optional[SignatureService] = None


def init_signature_service(threshold: int, min_length: int) -> SignatureService:
    global signature_service
    signature_service = SignatureService(threshold=threshold, min_length=min_length)
    return signature_service
