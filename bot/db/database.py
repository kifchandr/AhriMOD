# -*- coding: utf-8 -*-
"""Инициализация БД и DDL-схема."""
from __future__ import annotations

import aiosqlite
from pathlib import Path


# DDL — создаём таблицы если их нет
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id            INTEGER PRIMARY KEY,
    username           TEXT,
    full_name          TEXT,
    join_at            INTEGER,                 -- unix ts первого join
    message_count      INTEGER NOT NULL DEFAULT 0,
    warns              INTEGER NOT NULL DEFAULT 0,
    is_banned          INTEGER NOT NULL DEFAULT 0,
    trust_override     INTEGER NOT NULL DEFAULT 0,  -- 1 = вручную помечен доверенным
    is_blocked         INTEGER NOT NULL DEFAULT 0,  -- 1 = вручную в чёрном списке
    last_seen          INTEGER,
    qualifying_count   INTEGER NOT NULL DEFAULT 0,  -- сколько "квалифицирующих" сообщений накоплено
    last_qualifying_at INTEGER,                     -- ts последнего квалифицирующего сообщения
    qualified_at       INTEGER                      -- ts когда юзер набрал нужное число (точка отсчёта 24ч)
);

CREATE TABLE IF NOT EXISTS domains (
    domain     TEXT PRIMARY KEY,
    status     TEXT NOT NULL,
    added_by   INTEGER,
    added_at   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS words (
    word       TEXT PRIMARY KEY,
    status     TEXT NOT NULL,
    added_by   INTEGER,
    added_at   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS signatures (
    sha256     TEXT PRIMARY KEY,
    simhash    INTEGER NOT NULL,
    sample     TEXT,
    added_by   INTEGER,
    added_at   INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_signatures_simhash ON signatures(simhash);

CREATE TABLE IF NOT EXISTS pending_reviews (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id         INTEGER NOT NULL,
    chat_thread_id  INTEGER,
    message_id      INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    text            TEXT,
    domains         TEXT,
    words           TEXT,
    deleted         INTEGER NOT NULL DEFAULT 0,
    admin_msg_id    INTEGER,
    created_at      INTEGER NOT NULL,
    resolved_at     INTEGER,
    resolution      TEXT
);

CREATE INDEX IF NOT EXISTS idx_pending_admin_msg ON pending_reviews(admin_msg_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          INTEGER NOT NULL,
    actor_id    INTEGER,
    target_id   INTEGER,
    action      TEXT NOT NULL,
    details     TEXT
);

-- Предупреждения с TTL: каждое имеет дату выдачи и истечения.
-- Активные предупреждения = те где expires_at > now().
CREATE TABLE IF NOT EXISTS warns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    chat_id     INTEGER,
    reason      TEXT,
    created_at  INTEGER NOT NULL,
    expires_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_warns_user_expires ON warns(user_id, expires_at);

-- Статистика сообщений по разделам (для рейтинга и определения "любимого раздела").
-- thread_id = 0 если чат не форум-группа, или сообщение в General.
CREATE TABLE IF NOT EXISTS message_stats (
    user_id    INTEGER NOT NULL,
    chat_id    INTEGER NOT NULL,
    thread_id  INTEGER NOT NULL DEFAULT 0,
    count      INTEGER NOT NULL DEFAULT 0,
    last_at    INTEGER,
    PRIMARY KEY (user_id, chat_id, thread_id)
);
CREATE INDEX IF NOT EXISTS idx_msgstats_chat_thread ON message_stats(chat_id, thread_id);

-- Названия тем форум-групп (если бот видел forum_topic_created/edited).
CREATE TABLE IF NOT EXISTS forum_topics (
    chat_id    INTEGER NOT NULL,
    thread_id  INTEGER NOT NULL,
    name       TEXT,
    last_seen  INTEGER,
    PRIMARY KEY (chat_id, thread_id)
);

-- FAQ-ответы: триггерные фразы и текст ответа.
CREATE TABLE IF NOT EXISTS faq (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    triggers    TEXT NOT NULL,           -- JSON-массив фраз (нормализованных)
    answer      TEXT NOT NULL,
    added_by    INTEGER,
    added_at    INTEGER NOT NULL,
    use_count   INTEGER NOT NULL DEFAULT 0,
    last_used   INTEGER                  -- ts последнего использования (для cooldown)
);

-- Карта recent сообщений: нужна чтобы реакция-модерация знала автора и текст
-- для добавления сигнатуры при бане. Старые записи чистятся по TTL.
CREATE TABLE IF NOT EXISTS recent_messages (
    chat_id     INTEGER NOT NULL,
    message_id  INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    text        TEXT,
    created_at  INTEGER NOT NULL,
    PRIMARY KEY (chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_recent_msg_created ON recent_messages(created_at);

-- Runtime-настройки. Значения переопределяют значения из .env / Settings.
-- Хранятся как строки, кастятся в нужный тип в RuntimeSettings.reload().
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  INTEGER NOT NULL,
    updated_by  INTEGER
);
"""


# Миграции для уже существующих БД — добавляем колонки если их нет.
# Запускаются один раз при connect, безопасны при повторном вызове.
MIGRATIONS = [
    ("users", "qualifying_count", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "last_qualifying_at", "INTEGER"),
    ("users", "qualified_at", "INTEGER"),
    ("pending_reviews", "chat_thread_id", "INTEGER"),
]


class Database:
    """Простая обёртка над aiosqlite с persistent connection."""

    def __init__(self, path: Path):
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.execute("PRAGMA journal_mode = WAL")
        await self._conn.executescript(SCHEMA)
        await self._migrate()
        await self._conn.commit()

    async def _migrate(self) -> None:
        """Безопасная миграция: добавляет недостающие колонки в существующие БД."""
        assert self._conn is not None
        for table, column, definition in MIGRATIONS:
            async with self._conn.execute(f"PRAGMA table_info({table})") as cur:
                rows = await cur.fetchall()
                existing = {r[1] for r in rows}  # row[1] = name
            if column not in existing:
                await self._conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                )

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected. Call connect() first.")
        return self._conn


# Инициализируется в main.py
db = Database(Path("./data/bot.db"))
