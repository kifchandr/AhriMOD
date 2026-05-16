# -*- coding: utf-8 -*-
"""
Ahrimod — телеграм-бот модерации чата AhriVPN.

Перед запуском:
  1. Скопировать .env.example -> .env и заполнить
  2. python main.py
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys

# ─────────────── Установка заголовка консоли и автоустановка пакетов ───────────────

SCRIPT_NAME = "Ahrimod"


def _set_console_title(title: str) -> None:
    """Кросс-платформенно ставит title окна консоли."""
    try:
        if os.name == "nt":
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW(title)
        else:
            sys.stdout.write(f"\033]0;{title}\007")
            sys.stdout.flush()
    except Exception:
        pass


def _ensure_packages() -> None:
    """Проверяет наличие нужных пакетов и при необходимости ставит их."""
    required = {
        "aiogram": "aiogram>=3.4.0",
        "aiosqlite": "aiosqlite>=0.19.0",
        "pydantic_settings": "pydantic-settings>=2.1.0",
        "dotenv": "python-dotenv>=1.0.0",
        "simhash": "simhash>=2.1.2",
        "aiohttp": "aiohttp>=3.9.0",
        "rich": "rich>=13.7.0",
    }
    missing = []
    for module, spec in required.items():
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(spec)
    if missing:
        print(f"[setup] Устанавливаю недостающие пакеты: {', '.join(missing)}")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet", *missing]
        )
        # перечитываем sys.path
        importlib.invalidate_caches()


_set_console_title(SCRIPT_NAME)
_ensure_packages()

# ─────────────────────── Основные импорты после установки ───────────────────────

import asyncio
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.config import settings
from bot.db.database import db, Database
from bot.handlers import (
    admin_commands, config_menu, forum_topics, messages,
    moderation_callbacks, new_member, reactions,
)
from bot.middlewares.user_loader import UserLoaderMiddleware
from bot.services.content_filter import word_filter
from bot.services.signature import init_signature_service
from bot.utils.logger import setup_logging


async def main() -> None:
    setup_logging("INFO")
    log = logging.getLogger("main")

    # БД
    global db
    new_db = Database(Path(settings.db_path))
    # пересоздаём ссылку — модули импортировали имя db, поэтому
    # обновляем атрибуты глобального экземпляра
    db.path = new_db.path
    await db.connect()
    log.info("[green]DB подключена[/]: %s", settings.db_path)

    # Загружаем runtime-настройки из БД (переопределения над .env)
    n_overrides = await settings.reload_from_db()
    if n_overrides:
        log.info("Загружено runtime-настроек из БД: %d", n_overrides)

    # Сервисы
    init_signature_service(
        threshold=settings.simhash_threshold,
        min_length=settings.signature_min_length,
    )
    from bot.services.signature import signature_service
    if signature_service:
        await signature_service.reload()
    await word_filter.reload()

    from bot.services.faq import init_faq_service
    fs = init_faq_service(settings.faq_cooldown_minutes)
    await fs.reload()

    log.info("Сервисы инициализированы")

    # Bot + Dispatcher
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    # Middleware на все сообщения и callback'и
    dp.message.middleware(UserLoaderMiddleware())
    dp.callback_query.middleware(UserLoaderMiddleware())

    # Роутеры — порядок важен: команды раньше общего обработчика сообщений
    dp.include_router(config_menu.router)
    dp.include_router(admin_commands.router)
    dp.include_router(moderation_callbacks.router)
    dp.include_router(new_member.router)
    dp.include_router(forum_topics.router)
    dp.include_router(reactions.router)
    dp.include_router(messages.router)

    # Sanity check: проверяем что бот реально может обращаться к настроенным чатам.
    # Если ADMIN_CHAT_ID или PROTECTED_CHAT_IDS невалидны, или бот туда не добавлен —
    # лучше упасть на старте, чем на первом сообщении пользователя.
    from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
    chats_to_check = [("ADMIN_CHAT_ID", settings.admin_chat_id)]
    if settings.log_chat_id != settings.admin_chat_id:
        chats_to_check.append(("LOG_CHAT_ID", settings.log_chat_id))
    for cid in settings.protected_chat_ids:
        chats_to_check.append((f"PROTECTED_CHAT_IDS[{cid}]", cid))

    failures = []
    for label, cid in chats_to_check:
        try:
            chat = await bot.get_chat(cid)
            log.info("✓ %s = %s (%s, type=%s)", label, cid, chat.title or "—", chat.type)
        except (TelegramBadRequest, TelegramForbiddenError) as e:
            log.error("✗ %s = %s: %s", label, cid, e)
            failures.append((label, cid, str(e)))

    if failures:
        log.error(
            "Найдено %d проблемных чатов в конфиге. Проверь .env: ID должен быть с "
            "префиксом -100, бот должен быть добавлен в каждый чат (для admin/log "
            "достаточно обычного участника, для protected — обязательно админом).",
            len(failures),
        )
        # Не выходим — бот может работать частично (например log-чат недоступен,
        # но модерация может работать в protected-чатах). Просто логируем.

    log.info("[bold green]Бот запущен[/]")

    # Фоновый cleanup просроченных предупреждений + старых recent_messages раз в час
    async def _cleanup_loop():
        from bot.db.repositories import RecentMessagesRepo, WarnRepo
        while True:
            await asyncio.sleep(3600)
            try:
                removed = await WarnRepo.cleanup_expired()
                if removed:
                    log.info("Очищено просроченных предупреждений: %d", removed)
            except Exception as e:
                log.warning("warns cleanup failed: %s", e)
            try:
                removed = await RecentMessagesRepo.cleanup_old(
                    settings.recent_messages_ttl_days
                )
                if removed:
                    log.info("Очищено старых recent_messages: %d", removed)
            except Exception as e:
                log.warning("recent_messages cleanup failed: %s", e)
    asyncio.create_task(_cleanup_loop())

    # Ежедневный бэкап в указанный час по UTC
    async def _backup_loop():
        from datetime import datetime, timedelta, timezone
        from bot.services.backup import send_backup
        if not settings.backup_enabled:
            log.info("Ежедневный бэкап отключён (BACKUP_ENABLED=false)")
            return
        while True:
            now = datetime.now(timezone.utc)
            next_run = now.replace(
                hour=settings.backup_hour, minute=0, second=0, microsecond=0,
            )
            if next_run <= now:
                next_run += timedelta(days=1)
            sleep_seconds = (next_run - now).total_seconds()
            log.info(
                "Следующий бэкап: %s UTC (через %.1f ч)",
                next_run.strftime("%Y-%m-%d %H:%M"),
                sleep_seconds / 3600,
            )
            await asyncio.sleep(sleep_seconds)
            try:
                await send_backup(bot)
                log.info("[green]Бэкап успешно отправлен[/]")
            except Exception as e:
                log.error("Бэкап провалился: %s", e)
    asyncio.create_task(_backup_loop())

    try:
        await dp.start_polling(
            bot,
            allowed_updates=[
                "message",
                "edited_message",
                "callback_query",
                "chat_member",
                "my_chat_member",
                "message_reaction",
            ],
        )
    finally:
        await bot.session.close()
        await db.close()
        log.info("Бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\n[exit] Остановлено пользователем.")
