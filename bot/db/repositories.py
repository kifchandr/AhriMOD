# -*- coding: utf-8 -*-
"""Репозитории для работы с БД. Изолируют SQL от бизнес-логики."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Iterable, Optional

from .database import db


def _now() -> int:
    return int(time.time())


# ────────────────────────────── Пользователи ──────────────────────────────

@dataclass(slots=True)
class UserRecord:
    user_id: int
    username: Optional[str]
    full_name: Optional[str]
    join_at: Optional[int]
    message_count: int
    warns: int                                 # число активных (не просроченных) предупреждений
    is_banned: bool
    trust_override: bool
    is_blocked: bool
    last_seen: Optional[int]
    qualifying_count: int = 0
    last_qualifying_at: Optional[int] = None
    qualified_at: Optional[int] = None

    def days_in_group(self) -> Optional[int]:
        """Сколько полных дней в группе (с момента join)."""
        if not self.join_at:
            return None
        return max(0, (_now() - self.join_at) // 86400)

    def is_trusted(self, trust_min_hours: int, trust_min_messages: int) -> bool:
        """
        Доверенный = ручное override ИЛИ
          (qualified_at установлен) И (qualifying_count >= нужного числа) И
          (с момента qualified_at прошло >= trust_min_hours часов) И
          (юзер не забанен/не блокнут вручную).
        """
        if self.trust_override:
            return True
        if self.is_blocked or self.is_banned:
            return False
        if self.qualified_at is None:
            return False
        if self.qualifying_count < trust_min_messages:
            return False
        if (_now() - self.qualified_at) < trust_min_hours * 3600:
            return False
        return True

    def trust_progress_pct(self, trust_min_hours: int, trust_min_messages: int) -> int:
        """
        Процент 0..100 на пути к доверию.
        50% — за квалифицирующие сообщения (поэтапно), ещё 50% — за ожидание
        после набора порога. Override и забанен — крайние случаи.
        """
        if self.trust_override:
            return 100
        if self.is_blocked or self.is_banned:
            return 0
        if trust_min_messages <= 0:
            return 100
        # Часть 1 — квалифицирующие сообщения (максимум 50)
        qual_pct = min(self.qualifying_count, trust_min_messages) / trust_min_messages * 50
        # Часть 2 — ожидание (максимум 50)
        wait_pct = 0.0
        if self.qualified_at is not None:
            wait_seconds = trust_min_hours * 3600
            if wait_seconds > 0:
                elapsed = max(0, _now() - self.qualified_at)
                wait_pct = min(elapsed, wait_seconds) / wait_seconds * 50
        return int(round(qual_pct + wait_pct))

    def trust_status_human(self, trust_min_hours: int, trust_min_messages: int,
                           trust_min_interval_seconds: int) -> str:
        """Человеко-читаемый статус доверия с процентом — для админ-сообщений и /info."""
        pct = self.trust_progress_pct(trust_min_hours, trust_min_messages)
        if self.trust_override:
            return f"<b>100%</b> ✅ (override)"
        if self.is_blocked:
            return f"<b>0%</b> ❌ в чёрном списке"
        if self.is_banned:
            return f"<b>0%</b> ❌ забанен"
        if pct >= 100:
            return f"<b>100%</b> ✅ доверенный"
        # Промежуточный статус — добавим что именно ещё не выполнено
        details: list[str] = []
        if self.qualifying_count < trust_min_messages:
            need = trust_min_messages - self.qualifying_count
            wait_str = ""
            if self.last_qualifying_at:
                elapsed = _now() - self.last_qualifying_at
                if elapsed < trust_min_interval_seconds:
                    left = trust_min_interval_seconds - elapsed
                    wait_str = f", след. через {_fmt_eta(left)}"
            details.append(f"ещё {need} сообщ.{wait_str}")
        elif self.qualified_at is not None:
            elapsed = _now() - self.qualified_at
            wait_seconds = trust_min_hours * 3600
            if elapsed < wait_seconds:
                details.append(f"ещё {_fmt_eta(wait_seconds - elapsed)}")
        suffix = f" ({'; '.join(details)})" if details else ""
        return f"<b>{pct}%</b>{suffix}"


def _fmt_eta(seconds: int) -> str:
    """Кратко форматирует число секунд: '23ч 14м', '45м', '2д 3ч'."""
    if seconds < 0:
        seconds = 0
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}д {hours}ч"
    if hours:
        return f"{hours}ч {minutes}м"
    return f"{minutes}м"


class UserRepo:
    @staticmethod
    async def get_or_create(
        user_id: int,
        username: Optional[str],
        full_name: Optional[str],
        join_at_if_new: Optional[int] = None,
    ) -> UserRecord:
        async with db.conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()

        if row is None:
            now = _now()
            join_at = join_at_if_new if join_at_if_new is not None else now
            await db.conn.execute(
                "INSERT INTO users(user_id, username, full_name, join_at, last_seen) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, username, full_name, join_at, now),
            )
            await db.conn.commit()
            return UserRecord(
                user_id=user_id, username=username, full_name=full_name,
                join_at=join_at, message_count=0, warns=0,
                is_banned=False, trust_override=False, is_blocked=False,
                last_seen=now,
                qualifying_count=0, last_qualifying_at=None, qualified_at=None,
            )

        if row["username"] != username or row["full_name"] != full_name:
            await db.conn.execute(
                "UPDATE users SET username = ?, full_name = ?, last_seen = ? WHERE user_id = ?",
                (username, full_name, _now(), user_id),
            )
            await db.conn.commit()

        # Активные предупреждения подгружаем из таблицы warns с учётом TTL
        active_warns = await WarnRepo.count_active(user_id)

        return UserRecord(
            user_id=row["user_id"],
            username=row["username"],
            full_name=row["full_name"],
            join_at=row["join_at"],
            message_count=row["message_count"],
            warns=active_warns,
            is_banned=bool(row["is_banned"]),
            trust_override=bool(row["trust_override"]),
            is_blocked=bool(row["is_blocked"]),
            last_seen=row["last_seen"],
            qualifying_count=row["qualifying_count"] or 0,
            last_qualifying_at=row["last_qualifying_at"],
            qualified_at=row["qualified_at"],
        )

    @staticmethod
    async def increment_messages(
        user_id: int,
        trust_min_messages: int,
        trust_min_interval_seconds: int,
    ) -> None:
        """
        Увеличивает счётчик сообщений и обновляет квалифицирующие поля.
        """
        now = _now()
        async with db.conn.execute(
            "SELECT qualifying_count, last_qualifying_at, qualified_at "
            "FROM users WHERE user_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return

        qcount = row["qualifying_count"] or 0
        last_q = row["last_qualifying_at"]
        qualified = row["qualified_at"]

        new_qcount = qcount
        new_last_q = last_q
        new_qualified = qualified

        if qcount < trust_min_messages:
            if last_q is None or (now - last_q) >= trust_min_interval_seconds:
                new_qcount = qcount + 1
                new_last_q = now
                if new_qcount >= trust_min_messages and qualified is None:
                    new_qualified = now

        await db.conn.execute(
            """UPDATE users SET
                message_count = message_count + 1,
                last_seen = ?,
                qualifying_count = ?,
                last_qualifying_at = ?,
                qualified_at = ?
               WHERE user_id = ?""",
            (now, new_qcount, new_last_q, new_qualified, user_id),
        )
        await db.conn.commit()

    @staticmethod
    async def reset_trust(user_id: int) -> None:
        """
        Сбрасывает путь к доверию: обнуляет qualifying_count, last_qualifying_at,
        qualified_at и снимает trust_override. Юзер должен заново заработать доверие.
        """
        await db.conn.execute(
            "UPDATE users SET qualifying_count = 0, last_qualifying_at = NULL, "
            "qualified_at = NULL, trust_override = 0 WHERE user_id = ?",
            (user_id,),
        )
        await db.conn.commit()

    @staticmethod
    async def set_banned(user_id: int, banned: bool) -> None:
        await db.conn.execute(
            "UPDATE users SET is_banned = ? WHERE user_id = ?",
            (1 if banned else 0, user_id),
        )
        await db.conn.commit()

    @staticmethod
    async def set_trust_override(user_id: int, trusted: bool) -> None:
        await db.conn.execute(
            "UPDATE users SET trust_override = ? WHERE user_id = ?",
            (1 if trusted else 0, user_id),
        )
        await db.conn.commit()


# ────────────────────────────── Домены ──────────────────────────────

class DomainRepo:
    @staticmethod
    async def _lookup_one(domain: str) -> Optional[str]:
        """Точное совпадение + wildcard для одного домена."""
        async with db.conn.execute(
            "SELECT status FROM domains WHERE domain = ?", (domain,)
        ) as cur:
            row = await cur.fetchone()
        if row:
            return row["status"]
        parts = domain.split(".")
        for i in range(1, len(parts)):
            wildcard = "*." + ".".join(parts[i:])
            async with db.conn.execute(
                "SELECT status FROM domains WHERE domain = ?", (wildcard,)
            ) as cur:
                row = await cur.fetchone()
            if row:
                return row["status"]
        return None

    @staticmethod
    async def get_status(domain: str) -> Optional[str]:
        """
        Возвращает 'allowed' / 'blocked' / None.
        Поддерживает:
          - точное совпадение ('example.com')
          - wildcard ('*.example.com')
          - hostpart-fallback: для доменов с путём вида 't.me/channel'
            если конкретный канал не записан, проверяется хост 't.me'.
            Это позволяет общим записям типа 't.me' покрывать любые
            t.me/<channel>, при этом отдельный спам-канал можно
            заблокировать индивидуально.
        """
        domain = domain.lower().lstrip(".")
        # 1. Полный домен (включая возможный path для @-каналов)
        result = await DomainRepo._lookup_one(domain)
        if result is not None:
            return result
        # 2. Хост-часть как fallback (для 't.me/channel' → 't.me')
        if "/" in domain:
            host = domain.split("/", 1)[0]
            return await DomainRepo._lookup_one(host)
        return None

    @staticmethod
    async def set_status(domain: str, status: str, added_by: int) -> None:
        domain = domain.lower().lstrip(".")
        await db.conn.execute(
            "INSERT INTO domains(domain, status, added_by, added_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(domain) DO UPDATE SET status=excluded.status, "
            "added_by=excluded.added_by, added_at=excluded.added_at",
            (domain, status, added_by, _now()),
        )
        await db.conn.commit()

    @staticmethod
    async def remove(domain: str) -> None:
        domain = domain.lower().lstrip(".")
        await db.conn.execute("DELETE FROM domains WHERE domain = ?", (domain,))
        await db.conn.commit()

    @staticmethod
    async def list_all(status: Optional[str] = None) -> list[tuple[str, str]]:
        if status:
            sql = "SELECT domain, status FROM domains WHERE status = ? ORDER BY domain"
            params: tuple = (status,)
        else:
            sql = "SELECT domain, status FROM domains ORDER BY status, domain"
            params = ()
        async with db.conn.execute(sql, params) as cur:
            return [(r["domain"], r["status"]) async for r in cur]


# ────────────────────────────── Слова ──────────────────────────────

class WordRepo:
    @staticmethod
    async def get_status(word: str) -> Optional[str]:
        word = word.lower().strip()
        async with db.conn.execute(
            "SELECT status FROM words WHERE word = ?", (word,)
        ) as cur:
            row = await cur.fetchone()
        return row["status"] if row else None

    @staticmethod
    async def all_blocked() -> list[str]:
        async with db.conn.execute(
            "SELECT word FROM words WHERE status = 'blocked'"
        ) as cur:
            return [r["word"] async for r in cur]

    @staticmethod
    async def all_allowed() -> set[str]:
        async with db.conn.execute(
            "SELECT word FROM words WHERE status = 'allowed'"
        ) as cur:
            return {r["word"] async for r in cur}

    @staticmethod
    async def set_status(word: str, status: str, added_by: int) -> None:
        word = word.lower().strip()
        await db.conn.execute(
            "INSERT INTO words(word, status, added_by, added_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(word) DO UPDATE SET status=excluded.status, "
            "added_by=excluded.added_by, added_at=excluded.added_at",
            (word, status, added_by, _now()),
        )
        await db.conn.commit()

    @staticmethod
    async def remove(word: str) -> None:
        word = word.lower().strip()
        await db.conn.execute("DELETE FROM words WHERE word = ?", (word,))
        await db.conn.commit()


# ────────────────────────────── Сигнатуры ──────────────────────────────

class SignatureRepo:
    @staticmethod
    async def exists_exact(sha256: str) -> bool:
        async with db.conn.execute(
            "SELECT 1 FROM signatures WHERE sha256 = ?", (sha256,)
        ) as cur:
            return await cur.fetchone() is not None

    @staticmethod
    async def all_simhashes() -> list[int]:
        async with db.conn.execute("SELECT simhash FROM signatures") as cur:
            return [r["simhash"] async for r in cur]

    @staticmethod
    async def add(sha256: str, simhash: int, sample: str, added_by: int) -> None:
        await db.conn.execute(
            "INSERT OR IGNORE INTO signatures(sha256, simhash, sample, added_by, added_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (sha256, simhash, sample[:200], added_by, _now()),
        )
        await db.conn.commit()


# ────────────────────────────── Pending reviews ──────────────────────────────

class PendingRepo:
    @staticmethod
    async def create(
        chat_id: int,
        message_id: int,
        user_id: int,
        text: str,
        domains: Iterable[str],
        words: Iterable[str],
        deleted: bool,
        chat_thread_id: Optional[int] = None,
    ) -> int:
        cur = await db.conn.execute(
            "INSERT INTO pending_reviews(chat_id, chat_thread_id, message_id, user_id, "
            "text, domains, words, deleted, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                chat_id, chat_thread_id, message_id, user_id, text,
                json.dumps(list(domains), ensure_ascii=False),
                json.dumps(list(words), ensure_ascii=False),
                1 if deleted else 0,
                _now(),
            ),
        )
        await db.conn.commit()
        return cur.lastrowid  # type: ignore

    @staticmethod
    async def set_admin_msg_id(review_id: int, admin_msg_id: int) -> None:
        await db.conn.execute(
            "UPDATE pending_reviews SET admin_msg_id = ? WHERE id = ?",
            (admin_msg_id, review_id),
        )
        await db.conn.commit()

    @staticmethod
    async def get(review_id: int) -> Optional[dict]:
        async with db.conn.execute(
            "SELECT * FROM pending_reviews WHERE id = ?", (review_id,)
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    @staticmethod
    async def resolve(review_id: int, resolution: str) -> None:
        await db.conn.execute(
            "UPDATE pending_reviews SET resolved_at = ?, resolution = ? WHERE id = ?",
            (_now(), resolution, review_id),
        )
        await db.conn.commit()


# ────────────────────────────── Audit log ──────────────────────────────

class AuditRepo:
    @staticmethod
    async def log(
        actor_id: Optional[int],
        target_id: Optional[int],
        action: str,
        details: str = "",
    ) -> None:
        await db.conn.execute(
            "INSERT INTO audit_log(ts, actor_id, target_id, action, details) "
            "VALUES (?, ?, ?, ?, ?)",
            (_now(), actor_id, target_id, action, details),
        )
        await db.conn.commit()


# ────────────────────────────── Warns (с TTL) ──────────────────────────────

class WarnRepo:
    """
    Каждое предупреждение — запись с датой создания и датой истечения.
    Активные предупреждения = те где expires_at > now().
    Это позволяет автоматически "списывать" старые предупреждения.
    """

    @staticmethod
    async def add(user_id: int, reason: str, chat_id: Optional[int],
                  ttl_days: int) -> int:
        """Добавить предупреждение. Возвращает число активных после добавления."""
        now = _now()
        expires = now + ttl_days * 86400
        await db.conn.execute(
            "INSERT INTO warns(user_id, chat_id, reason, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, chat_id, reason, now, expires),
        )
        await db.conn.commit()
        return await WarnRepo.count_active(user_id)

    @staticmethod
    async def count_active(user_id: int) -> int:
        """Число не просроченных предупреждений."""
        async with db.conn.execute(
            "SELECT COUNT(*) AS c FROM warns WHERE user_id = ? AND expires_at > ?",
            (user_id, _now()),
        ) as cur:
            row = await cur.fetchone()
        return row["c"] if row else 0

    @staticmethod
    async def list_active(user_id: int) -> list[dict]:
        """Список активных предупреждений (для /info)."""
        async with db.conn.execute(
            "SELECT id, reason, created_at, expires_at FROM warns "
            "WHERE user_id = ? AND expires_at > ? ORDER BY created_at DESC",
            (user_id, _now()),
        ) as cur:
            return [dict(r) async for r in cur]

    @staticmethod
    async def cleanup_expired() -> int:
        """Удаляет просроченные предупреждения. Возвращает число удалённых."""
        cur = await db.conn.execute(
            "DELETE FROM warns WHERE expires_at <= ?", (_now(),)
        )
        await db.conn.commit()
        return cur.rowcount


# ────────────────────────────── Статистика по разделам ──────────────────────────────

class MessageStatsRepo:
    """Учёт сообщений по (user_id, chat_id, thread_id) для рейтинга и любимого раздела."""

    @staticmethod
    async def increment(user_id: int, chat_id: int, thread_id: Optional[int]) -> None:
        tid = thread_id or 0
        await db.conn.execute(
            "INSERT INTO message_stats(user_id, chat_id, thread_id, count, last_at) "
            "VALUES (?, ?, ?, 1, ?) "
            "ON CONFLICT(user_id, chat_id, thread_id) DO UPDATE SET "
            "count = count + 1, last_at = excluded.last_at",
            (user_id, chat_id, tid, _now()),
        )
        await db.conn.commit()

    @staticmethod
    async def top_users(
        limit: int = 10,
        chat_id: Optional[int] = None,
        thread_id: Optional[int] = None,
    ) -> list[dict]:
        """
        Топ юзеров по сумме сообщений. Если chat_id/thread_id заданы —
        статистика только по этому разделу. Иначе — по всем чатам/темам.
        """
        sql = (
            "SELECT s.user_id, u.username, u.full_name, SUM(s.count) AS total "
            "FROM message_stats s LEFT JOIN users u ON u.user_id = s.user_id "
        )
        params: list = []
        where: list[str] = []
        if chat_id is not None:
            where.append("s.chat_id = ?")
            params.append(chat_id)
        if thread_id is not None:
            where.append("s.thread_id = ?")
            params.append(thread_id or 0)
        if where:
            sql += "WHERE " + " AND ".join(where) + " "
        sql += "GROUP BY s.user_id ORDER BY total DESC LIMIT ?"
        params.append(limit)
        async with db.conn.execute(sql, params) as cur:
            return [dict(r) async for r in cur]

    @staticmethod
    async def top_thread_for_user(user_id: int) -> Optional[dict]:
        """Самый активный раздел юзера (chat_id, thread_id, count)."""
        async with db.conn.execute(
            "SELECT chat_id, thread_id, count FROM message_stats "
            "WHERE user_id = ? ORDER BY count DESC LIMIT 1",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    @staticmethod
    async def top_threads(chat_id: int, limit: int = 10) -> list[dict]:
        """Топ разделов внутри чата по числу сообщений."""
        async with db.conn.execute(
            "SELECT thread_id, SUM(count) AS total, COUNT(DISTINCT user_id) AS users "
            "FROM message_stats WHERE chat_id = ? "
            "GROUP BY thread_id ORDER BY total DESC LIMIT ?",
            (chat_id, limit),
        ) as cur:
            return [dict(r) async for r in cur]


# ────────────────────────────── Имена тем форум-групп ──────────────────────────────

class ForumTopicRepo:
    @staticmethod
    async def set_name(chat_id: int, thread_id: int, name: str) -> None:
        await db.conn.execute(
            "INSERT INTO forum_topics(chat_id, thread_id, name, last_seen) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(chat_id, thread_id) DO UPDATE SET "
            "name = excluded.name, last_seen = excluded.last_seen",
            (chat_id, thread_id, name, _now()),
        )
        await db.conn.commit()

    @staticmethod
    async def get_name(chat_id: int, thread_id: Optional[int]) -> str:
        """Возвращает имя темы или fallback ('General' / 'Тема <id>')."""
        if not thread_id:
            return "General"
        async with db.conn.execute(
            "SELECT name FROM forum_topics WHERE chat_id = ? AND thread_id = ?",
            (chat_id, thread_id),
        ) as cur:
            row = await cur.fetchone()
        if row and row["name"]:
            return row["name"]
        return f"Тема {thread_id}"


# ────────────────────────────── FAQ ──────────────────────────────

class FaqRepo:
    @staticmethod
    async def add(triggers: list[str], answer: str, added_by: int) -> int:
        """triggers — список фраз (lowercase, нормализованных). answer — текст ответа."""
        cur = await db.conn.execute(
            "INSERT INTO faq(triggers, answer, added_by, added_at) "
            "VALUES (?, ?, ?, ?)",
            (json.dumps(triggers, ensure_ascii=False), answer, added_by, _now()),
        )
        await db.conn.commit()
        return cur.lastrowid  # type: ignore

    @staticmethod
    async def remove(faq_id: int) -> bool:
        cur = await db.conn.execute("DELETE FROM faq WHERE id = ?", (faq_id,))
        await db.conn.commit()
        return cur.rowcount > 0

    @staticmethod
    async def list_all() -> list[dict]:
        async with db.conn.execute(
            "SELECT id, triggers, answer, use_count, last_used FROM faq ORDER BY id"
        ) as cur:
            rows = await cur.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["triggers"] = json.loads(d["triggers"])
            result.append(d)
        return result

    @staticmethod
    async def get(faq_id: int) -> Optional[dict]:
        async with db.conn.execute(
            "SELECT id, triggers, answer, use_count, last_used FROM faq WHERE id = ?",
            (faq_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        d = dict(row)
        d["triggers"] = json.loads(d["triggers"])
        return d

    @staticmethod
    async def mark_used(faq_id: int) -> None:
        await db.conn.execute(
            "UPDATE faq SET use_count = use_count + 1, last_used = ? WHERE id = ?",
            (_now(), faq_id),
        )
        await db.conn.commit()


# ────────────────────────────── Recent messages (для реакций) ──────────────────────────────

class RecentMessagesRepo:
    """
    Хранит соответствие (chat_id, message_id) → автор + текст.
    Нужно для модерации через реакции: когда модератор ставит 🚫/🔨 на сообщение,
    мы должны знать автора чтобы выдать варн / бан / добавить сигнатуру.
    """

    @staticmethod
    async def add(chat_id: int, message_id: int, user_id: int,
                  text: Optional[str]) -> None:
        await db.conn.execute(
            "INSERT INTO recent_messages(chat_id, message_id, user_id, text, created_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(chat_id, message_id) DO NOTHING",
            (chat_id, message_id, user_id, text, _now()),
        )
        await db.conn.commit()

    @staticmethod
    async def get(chat_id: int, message_id: int) -> Optional[dict]:
        async with db.conn.execute(
            "SELECT user_id, text, created_at FROM recent_messages "
            "WHERE chat_id = ? AND message_id = ?",
            (chat_id, message_id),
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    @staticmethod
    async def cleanup_old(ttl_days: int) -> int:
        cutoff = _now() - ttl_days * 86400
        cur = await db.conn.execute(
            "DELETE FROM recent_messages WHERE created_at < ?", (cutoff,)
        )
        await db.conn.commit()
        return cur.rowcount


# ────────────────────────────── FAQ ──────────────────────────────

class FaqRepo:
    """FAQ-автоответы. Триггеры — JSON-массив фраз для substring-матчинга."""

    @staticmethod
    async def add(triggers: list[str], answer: str, added_by: int) -> int:
        triggers_json = json.dumps([t.strip().lower() for t in triggers if t.strip()],
                                   ensure_ascii=False)
        cur = await db.conn.execute(
            "INSERT INTO faq(triggers, answer, added_by, added_at) VALUES (?, ?, ?, ?)",
            (triggers_json, answer, added_by, _now()),
        )
        await db.conn.commit()
        return cur.lastrowid  # type: ignore

    @staticmethod
    async def remove(faq_id: int) -> bool:
        cur = await db.conn.execute("DELETE FROM faq WHERE id = ?", (faq_id,))
        await db.conn.commit()
        return cur.rowcount > 0

    @staticmethod
    async def get(faq_id: int) -> Optional[dict]:
        async with db.conn.execute(
            "SELECT * FROM faq WHERE id = ?", (faq_id,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["triggers_list"] = json.loads(d["triggers"])
        except Exception:
            d["triggers_list"] = []
        return d

    @staticmethod
    async def list_all() -> list[dict]:
        async with db.conn.execute(
            "SELECT * FROM faq ORDER BY id"
        ) as cur:
            rows = [dict(r) async for r in cur]
        for d in rows:
            try:
                d["triggers_list"] = json.loads(d["triggers"])
            except Exception:
                d["triggers_list"] = []
        return rows

    @staticmethod
    async def mark_used(faq_id: int) -> None:
        await db.conn.execute(
            "UPDATE faq SET use_count = use_count + 1, last_used = ? WHERE id = ?",
            (_now(), faq_id),
        )
        await db.conn.commit()


# ────────────────────────────── Recent messages (для реакций) ──────────────────────────────

class RecentMessagesRepo:
    """
    Кэш recent сообщений. Нужен потому что событие message_reaction
    даёт только chat_id+message_id, а нам нужен автор и текст
    (для бана + добавления сигнатуры). TTL очищаем периодически.
    """

    @staticmethod
    async def add(chat_id: int, message_id: int, user_id: int, text: Optional[str]) -> None:
        await db.conn.execute(
            "INSERT INTO recent_messages(chat_id, message_id, user_id, text, created_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(chat_id, message_id) DO UPDATE SET "
            "user_id = excluded.user_id, text = excluded.text",
            (chat_id, message_id, user_id, text or "", _now()),
        )
        await db.conn.commit()

    @staticmethod
    async def get(chat_id: int, message_id: int) -> Optional[dict]:
        async with db.conn.execute(
            "SELECT user_id, text, created_at FROM recent_messages "
            "WHERE chat_id = ? AND message_id = ?",
            (chat_id, message_id),
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    @staticmethod
    async def cleanup_old(ttl_days: int) -> int:
        cutoff = _now() - ttl_days * 86400
        cur = await db.conn.execute(
            "DELETE FROM recent_messages WHERE created_at < ?", (cutoff,)
        )
        await db.conn.commit()
        return cur.rowcount


# ────────────────────────────── Runtime-настройки ──────────────────────────────

class SettingsRepo:
    @staticmethod
    async def get(key: str) -> Optional[str]:
        async with db.conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ) as cur:
            row = await cur.fetchone()
        return row["value"] if row else None

    @staticmethod
    async def get_all() -> dict[str, str]:
        async with db.conn.execute(
            "SELECT key, value FROM settings"
        ) as cur:
            return {r["key"]: r["value"] async for r in cur}

    @staticmethod
    async def set(key: str, value: str, updated_by: Optional[int]) -> None:
        await db.conn.execute(
            "INSERT INTO settings(key, value, updated_at, updated_by) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET "
            "value = excluded.value, updated_at = excluded.updated_at, "
            "updated_by = excluded.updated_by",
            (key, value, _now(), updated_by),
        )
        await db.conn.commit()

    @staticmethod
    async def delete(key: str) -> bool:
        cur = await db.conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        await db.conn.commit()
        return cur.rowcount > 0
