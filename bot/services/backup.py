# -*- coding: utf-8 -*-
"""Бэкап SQLite БД и отправка в Telegram-канал."""
from __future__ import annotations

import gzip
import logging
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiosqlite
from aiogram import Bot
from aiogram.types import FSInputFile

from ..config import settings

logger = logging.getLogger(__name__)


def _backup_dir() -> Path:
    """Директория для локальных бэкапов: <db_path_parent>/backups/"""
    return settings.db_path.parent / "backups"


async def make_backup() -> Path:
    """
    Делает атомарный снимок БД через VACUUM INTO и сжимает в gzip.
    VACUUM INTO работает на открытой БД, не блокирует читателей/писателей,
    создаёт целостный консистентный файл.

    Возвращает путь к .gz файлу.
    """
    backup_dir = _backup_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    raw_path = backup_dir / f"bot-{today}.db"
    gz_path = backup_dir / f"bot-{today}.db.gz"

    # VACUUM INTO — атомарный снимок текущего состояния БД
    async with aiosqlite.connect(settings.db_path) as conn:
        # параметризация VACUUM INTO не работает в SQLite — используем форматирование
        # с предварительной валидацией пути
        safe_path = str(raw_path).replace("'", "''")
        await conn.execute(f"VACUUM INTO '{safe_path}'")

    # Сжимаем
    with open(raw_path, "rb") as src, gzip.open(gz_path, "wb", compresslevel=6) as dst:
        shutil.copyfileobj(src, dst)
    raw_path.unlink()  # удаляем несжатый

    return gz_path


async def cleanup_old_backups(keep_days: int) -> int:
    """Удаляет локальные .gz бэкапы старше keep_days дней. Возвращает число удалённых."""
    backup_dir = _backup_dir()
    if not backup_dir.exists():
        return 0
    cutoff = time.time() - keep_days * 86400
    removed = 0
    for f in backup_dir.glob("bot-*.db.gz"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except Exception as e:
            logger.warning("Не удалось удалить старый бэкап %s: %s", f, e)
    return removed


async def send_backup(bot: Bot, manual: bool = False) -> Optional[Path]:
    """
    Делает бэкап и отправляет в BACKUP_CHAT_ID/BACKUP_THREAD_ID
    (fallback на ADMIN_CHAT_ID если BACKUP_CHAT_ID не задан).
    Возвращает путь к локальному .gz файлу, или None если отключено/ошибка.
    """
    if not settings.backup_enabled:
        logger.info("Бэкап отключён (BACKUP_ENABLED=false)")
        return None

    gz_path = await make_backup()
    size_kb = gz_path.stat().st_size // 1024

    target_chat_id = settings.backup_chat_id or settings.admin_chat_id
    kwargs: dict = {}
    if settings.backup_thread_id:
        kwargs["message_thread_id"] = settings.backup_thread_id

    label = "📦 <b>Ручной бэкап</b>" if manual else "📦 <b>Ежедневный бэкап БД</b>"
    caption = (
        f"{label}\n"
        f"📅 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"💾 {size_kb} KB (gzip)"
    )

    await bot.send_document(
        chat_id=target_chat_id,
        document=FSInputFile(str(gz_path)),
        caption=caption,
        parse_mode="HTML",
        **kwargs,
    )

    removed = await cleanup_old_backups(settings.backup_keep_days)
    if removed:
        logger.info("Удалено старых локальных бэкапов: %d", removed)

    return gz_path
