# -*- coding: utf-8 -*-
"""Команды модераторов: добавление в whitelist/blacklist, бан, инфа о юзере."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ..config import settings
from ..db.repositories import (
    AuditRepo,
    DomainRepo,
    FaqRepo,
    ForumTopicRepo,
    MessageStatsRepo,
    UserRepo,
    WarnRepo,
    WordRepo,
)
from ..services import faq as faq_module
from ..services.content_filter import word_filter
from ..services.signature import signature_service
from ..services.backup import send_backup

logger = logging.getLogger(__name__)
router = Router(name="admin_commands")


def _is_admin_msg(message: Message) -> bool:
    return bool(message.from_user and message.from_user.id in settings.admin_user_ids)


# ────────────────────────────── Управление словами ──────────────────────────────

@router.message(Command("addbanword"))
async def cmd_add_ban_word(message: Message, command: CommandObject) -> None:
    if not _is_admin_msg(message) or not command.args:
        return
    word = command.args.strip().lower()
    if len(word) < 2:
        await message.reply("Слишком короткое слово.")
        return
    await WordRepo.set_status(word, "blocked", message.from_user.id)
    await word_filter.reload()
    await AuditRepo.log(message.from_user.id, None, "addbanword", word)
    await message.reply(f"🚫 Слово <code>{escape(word)}</code> в блэклисте.", parse_mode="HTML")


@router.message(Command("rmword"))
async def cmd_remove_word(message: Message, command: CommandObject) -> None:
    if not _is_admin_msg(message) or not command.args:
        return
    word = command.args.strip().lower()
    await WordRepo.remove(word)
    await word_filter.reload()
    await AuditRepo.log(message.from_user.id, None, "rmword", word)
    await message.reply(f"✅ Слово <code>{escape(word)}</code> удалено из списков.", parse_mode="HTML")


# ────────────────────────────── Управление доменами ──────────────────────────────

def _normalize_domain_input(s: str) -> str:
    """
    Приводит произвольный ввод модератора к форме которую использует link extractor.
    Примеры:
      '@channel'                     -> 't.me/channel'
      'https://t.me/channel'         -> 't.me/channel'
      'https://example.com/path?x=1' -> 'example.com'
      '*.example.com'                -> '*.example.com' (wildcard)
      'EXAMPLE.com'                  -> 'example.com'
    """
    s = s.strip().lower()
    if not s:
        return s
    # @username → t.me/username
    if s.startswith("@"):
        return f"t.me/{s.lstrip('@')}"
    # URL → host (+ first path segment если это t.me/<channel>)
    if "://" in s:
        from urllib.parse import urlparse
        try:
            parsed = urlparse(s)
            host = (parsed.hostname or "").lower().lstrip(".")
            if host.startswith("www."):
                host = host[4:]
            path = parsed.path.lstrip("/")
            if host in {"t.me", "telegram.me", "telegram.dog"} and path:
                first_seg = path.split("/", 1)[0]
                return f"t.me/{first_seg}"
            return host
        except Exception:
            pass
    # Без схемы, но с www
    if s.startswith("www."):
        s = s[4:]
    return s


@router.message(Command("addgooddomain"))
async def cmd_add_good_domain(message: Message, command: CommandObject) -> None:
    if not _is_admin_msg(message) or not command.args:
        return
    domain = _normalize_domain_input(command.args)
    if not domain:
        await message.reply("Пустой ввод.")
        return
    await DomainRepo.set_status(domain, "allowed", message.from_user.id)
    await AuditRepo.log(message.from_user.id, None, "addgooddomain", domain)
    await message.reply(f"✅ <code>{escape(domain)}</code> разрешён.", parse_mode="HTML")


@router.message(Command("addbandomain"))
async def cmd_add_ban_domain(message: Message, command: CommandObject) -> None:
    if not _is_admin_msg(message) or not command.args:
        return
    domain = _normalize_domain_input(command.args)
    if not domain:
        await message.reply("Пустой ввод.")
        return
    await DomainRepo.set_status(domain, "blocked", message.from_user.id)
    await AuditRepo.log(message.from_user.id, None, "addbandomain", domain)
    await message.reply(f"🚫 <code>{escape(domain)}</code> в блэклисте.", parse_mode="HTML")


@router.message(Command("rmdomain"))
async def cmd_remove_domain(message: Message, command: CommandObject) -> None:
    if not _is_admin_msg(message) or not command.args:
        return
    domain = _normalize_domain_input(command.args)
    if not domain:
        await message.reply("Пустой ввод.")
        return
    await DomainRepo.remove(domain)
    await AuditRepo.log(message.from_user.id, None, "rmdomain", domain)
    await message.reply(f"✅ <code>{escape(domain)}</code> удалён.", parse_mode="HTML")


@router.message(Command("listdomains"))
async def cmd_list_domains(message: Message) -> None:
    if not _is_admin_msg(message):
        return
    rows = await DomainRepo.list_all()
    if not rows:
        await message.reply("База доменов пуста.")
        return
    lines = [f"{'✅' if s == 'allowed' else '🚫'} <code>{escape(d)}</code>" for d, s in rows[:100]]
    suffix = f"\n\n<i>+ ещё {len(rows) - 100}</i>" if len(rows) > 100 else ""
    await message.reply("\n".join(lines) + suffix, parse_mode="HTML")


# ────────────────────────────── Управление юзерами ──────────────────────────────

@router.message(Command("trust"))
async def cmd_trust(message: Message) -> None:
    if not _is_admin_msg(message):
        return
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("Команда работает только реплаем на сообщение юзера.")
        return
    target = message.reply_to_message.from_user
    await UserRepo.get_or_create(target.id, target.username, target.full_name)
    await UserRepo.set_trust_override(target.id, True)
    await AuditRepo.log(message.from_user.id, target.id, "trust_override_set", "")
    await message.reply(f"✅ <code>{target.id}</code> помечен доверенным.", parse_mode="HTML")


@router.message(Command("untrust"))
async def cmd_untrust(message: Message) -> None:
    if not _is_admin_msg(message) or not message.reply_to_message or not message.reply_to_message.from_user:
        return
    target = message.reply_to_message.from_user
    await UserRepo.set_trust_override(target.id, False)
    await AuditRepo.log(message.from_user.id, target.id, "trust_override_unset", "")
    await message.reply(f"✅ Снят флаг доверия с <code>{target.id}</code>.", parse_mode="HTML")


@router.message(Command("ban"))
async def cmd_ban(message: Message, bot: Bot) -> None:
    if not _is_admin_msg(message) or not message.reply_to_message or not message.reply_to_message.from_user:
        return
    target = message.reply_to_message.from_user
    try:
        await bot.ban_chat_member(message.chat.id, target.id)
        await UserRepo.set_banned(target.id, True)
        await message.reply(f"🔨 <code>{target.id}</code> забанен.", parse_mode="HTML")
        await AuditRepo.log(message.from_user.id, target.id, "ban", "manual")
        # Удаляем само сообщение нарушителя
        try:
            await bot.delete_message(message.chat.id, message.reply_to_message.message_id)
        except Exception:
            pass
    except Exception as e:
        await message.reply(f"Ошибка: {e}")


@router.message(Command("unban"))
async def cmd_unban(message: Message, bot: Bot, command: CommandObject) -> None:
    if not _is_admin_msg(message):
        return
    user_id: int | None = None
    if message.reply_to_message and message.reply_to_message.from_user:
        user_id = message.reply_to_message.from_user.id
    elif command.args and command.args.strip().isdigit():
        user_id = int(command.args.strip())
    if not user_id:
        await message.reply("Реплай на юзера или /unban <id>.")
        return
    try:
        await bot.unban_chat_member(message.chat.id, user_id, only_if_banned=True)
        await UserRepo.set_banned(user_id, False)
        await AuditRepo.log(message.from_user.id, user_id, "unban", "manual")
        await message.reply(f"✅ <code>{user_id}</code> разбанен.", parse_mode="HTML")
    except Exception as e:
        await message.reply(f"Ошибка: {e}")


@router.message(Command("info"))
async def cmd_info(message: Message) -> None:
    if not _is_admin_msg(message) or not message.reply_to_message or not message.reply_to_message.from_user:
        return
    target = message.reply_to_message.from_user
    record = await UserRepo.get_or_create(target.id, target.username, target.full_name)

    days = record.days_in_group()
    days_str = f"<b>{days}</b> дн." if days is not None else "—"

    last_q_str = (
        datetime.fromtimestamp(record.last_qualifying_at, tz=timezone.utc).isoformat()
        if record.last_qualifying_at else "—"
    )
    qualified_str = (
        datetime.fromtimestamp(record.qualified_at, tz=timezone.utc).isoformat()
        if record.qualified_at else "—"
    )
    trust_status = record.trust_status_human(
        settings.trust_min_hours,
        settings.trust_min_messages,
        settings.trust_min_interval_minutes * 60,
    )

    # Любимый раздел
    top_thread = await MessageStatsRepo.top_thread_for_user(target.id)
    fav_str = "—"
    if top_thread:
        topic_name = await ForumTopicRepo.get_name(
            top_thread["chat_id"], top_thread["thread_id"]
        )
        fav_str = f"{escape(topic_name)} ({top_thread['count']} сообщ.)"

    # Активные предупреждения с датами истечения
    active_warns = await WarnRepo.list_active(target.id)
    warns_str = f"<b>{record.warns}/{settings.warn_ban_at}</b>"
    if active_warns:
        first_to_expire = min(w["expires_at"] for w in active_warns)
        days_to_expire = max(0, (first_to_expire - int(datetime.now().timestamp())) // 86400)
        warns_str += f" (ближайшее списание через {days_to_expire} дн.)"

    info = (
        f"👤 <b>{escape(record.full_name or '')}</b> (@{record.username or '—'})\n"
        f"ID: <code>{record.user_id}</code>\n"
        f"В группе: {days_str}\n"
        f"Сообщений всего: <b>{record.message_count}</b>\n"
        f"Любимый раздел: {fav_str}\n"
        f"Квалифицирующих: <b>{record.qualifying_count}/{settings.trust_min_messages}</b>\n"
        f"Последнее квалиф.: <code>{last_q_str}</code>\n"
        f"Дата 'набрал порог': <code>{qualified_str}</code>\n"
        f"Предупреждений: {warns_str}\n"
        f"Доверие: {trust_status}\n"
        f"Бан: <b>{'да' if record.is_banned else 'нет'}</b>"
    )
    await message.reply(info, parse_mode="HTML")


# ────────────────────────────── Рейтинги ──────────────────────────────

def _user_label(uid: int, full_name: Optional[str], username: Optional[str]) -> str:
    name = escape(full_name or "—")
    return f'<a href="tg://user?id={uid}">{name}</a>' + (f" (@{username})" if username else "")


@router.message(Command("top"))
async def cmd_top(message: Message) -> None:
    """Глобальный рейтинг по сообщениям + любимый раздел каждого."""
    if not _is_admin_msg(message):
        return
    rows = await MessageStatsRepo.top_users(limit=10)
    if not rows:
        await message.reply("Статистика пока пуста.")
        return
    lines = ["🏆 <b>Топ-10 по сообщениям</b>", ""]
    for i, r in enumerate(rows, 1):
        top_thread = await MessageStatsRepo.top_thread_for_user(r["user_id"])
        fav = ""
        if top_thread:
            topic_name = await ForumTopicRepo.get_name(
                top_thread["chat_id"], top_thread["thread_id"]
            )
            fav = f" — чаще в «{escape(topic_name)}»"
        label = _user_label(r["user_id"], r["full_name"], r["username"])
        lines.append(f"{i}. {label} — <b>{r['total']}</b> сообщ.{fav}")
    await message.reply("\n".join(lines), parse_mode="HTML")


@router.message(Command("topthread"))
async def cmd_topthread(message: Message) -> None:
    """Рейтинг юзеров в текущей теме / разделе чата."""
    if not _is_admin_msg(message):
        return
    chat_id = message.chat.id
    thread_id = message.message_thread_id or 0
    topic_name = await ForumTopicRepo.get_name(chat_id, thread_id)
    rows = await MessageStatsRepo.top_users(limit=10, chat_id=chat_id, thread_id=thread_id)
    if not rows:
        await message.reply(f"В разделе «{escape(topic_name)}» статистики нет.")
        return
    lines = [f"🏆 <b>Топ в разделе «{escape(topic_name)}»</b>", ""]
    for i, r in enumerate(rows, 1):
        label = _user_label(r["user_id"], r["full_name"], r["username"])
        lines.append(f"{i}. {label} — <b>{r['total']}</b> сообщ.")
    await message.reply("\n".join(lines), parse_mode="HTML")


@router.message(Command("topthreads"))
async def cmd_topthreads(message: Message) -> None:
    """Рейтинг разделов в текущем чате по активности."""
    if not _is_admin_msg(message):
        return
    chat_id = message.chat.id
    rows = await MessageStatsRepo.top_threads(chat_id=chat_id, limit=15)
    if not rows:
        await message.reply("В этом чате статистики разделов нет.")
        return
    lines = ["📊 <b>Топ разделов по активности</b>", ""]
    for i, r in enumerate(rows, 1):
        topic_name = await ForumTopicRepo.get_name(chat_id, r["thread_id"])
        lines.append(
            f"{i}. <b>{escape(topic_name)}</b> — "
            f"{r['total']} сообщ. от {r['users']} участн."
        )
    await message.reply("\n".join(lines), parse_mode="HTML")


@router.message(Command("addsignature"))
async def cmd_add_signature(message: Message) -> None:
    """Добавляет сигнатуру по реплаю на спам-сообщение."""
    if not _is_admin_msg(message) or not message.reply_to_message:
        await message.reply("Реплай на сообщение для добавления сигнатуры.")
        return
    target = message.reply_to_message
    text = target.text or target.caption or ""
    if not text or signature_service is None:
        await message.reply("Нет текста или сервис не инициализирован.")
        return
    added = await signature_service.add(text, message.from_user.id)
    if added:
        await message.reply("✅ Сигнатура добавлена.")
    else:
        await message.reply("⚠️ Текст слишком короткий, не добавлено.")


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    if not _is_admin_msg(message):
        return
    from ..db.database import db
    async with db.conn.execute("SELECT COUNT(*) AS c FROM users") as cur:
        users_total = (await cur.fetchone())["c"]
    async with db.conn.execute("SELECT COUNT(*) AS c FROM users WHERE is_banned = 1") as cur:
        banned = (await cur.fetchone())["c"]
    async with db.conn.execute("SELECT COUNT(*) AS c FROM domains WHERE status='allowed'") as cur:
        good = (await cur.fetchone())["c"]
    async with db.conn.execute("SELECT COUNT(*) AS c FROM domains WHERE status='blocked'") as cur:
        bad = (await cur.fetchone())["c"]
    async with db.conn.execute("SELECT COUNT(*) AS c FROM signatures") as cur:
        sigs = (await cur.fetchone())["c"]
    async with db.conn.execute("SELECT COUNT(*) AS c FROM words WHERE status='blocked'") as cur:
        bad_words = (await cur.fetchone())["c"]
    async with db.conn.execute(
        "SELECT COUNT(*) AS c FROM pending_reviews WHERE resolved_at IS NULL"
    ) as cur:
        pending = (await cur.fetchone())["c"]

    text = (
        f"📊 <b>Статистика</b>\n"
        f"Юзеров: <b>{users_total}</b> (бан: {banned})\n"
        f"Доменов: ✅ {good} / 🚫 {bad}\n"
        f"Стоп-слов: <b>{bad_words}</b>\n"
        f"Сигнатур: <b>{sigs}</b>\n"
        f"На модерации: <b>{pending}</b>"
    )
    await message.reply(text, parse_mode="HTML")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    if not _is_admin_msg(message):
        return
    text = (
        "<b>Команды модератора:</b>\n"
        "/addbanword слово — добавить стоп-слово\n"
        "/rmword слово — удалить слово из всех списков\n"
        "/addgooddomain example.com — разрешить домен (поддерживается @channel и URL)\n"
        "/addbandomain example.com — забанить домен\n"
        "/rmdomain example.com — удалить домен\n"
        "/listdomains — список всех доменов\n"
        "/trust (реплай) — пометить юзера доверенным\n"
        "/untrust (реплай) — снять флаг доверия\n"
        "/ban (реплай) — забанить юзера\n"
        "/unban (реплай или id) — разбанить\n"
        "/info (реплай) — досье на юзера\n"
        "/addsignature (реплай) — добавить сигнатуру по сообщению\n"
        "/stats — статистика бота\n"
        "/top — топ-10 пользователей по сообщениям\n"
        "/topthread — топ в текущем разделе/теме\n"
        "/topthreads — топ разделов в текущем чате\n"
        "/backup — сделать бэкап БД и отправить сейчас\n"
        "\n<b>⚙️ Настройки бота:</b>\n"
        "/menu — интерактивное меню\n"
        "/config — показать все настройки\n"
        "/setcfg ключ значение — изменить\n"
        "/resetcfg ключ — сбросить к .env\n"
        "\n<b>FAQ-автоответы:</b>\n"
        "/addfaq триггер1, триггер2 :: ответ — добавить\n"
        "/listfaq — список\n"
        "/showfaq id — показать одну запись\n"
        "/rmfaq id — удалить\n"
        "/testfaq текст — проверить какой FAQ сработает\n"
        "\n<b>Импорт/экспорт:</b>\n"
        "/exportlists — выгрузить domains/words/faq в JSON\n"
        "/importlists (реплай на JSON) — импортировать\n"
        "\n<b>Реакции как модерация:</b>\n"
        "🚫 на сообщение → удалить\n"
        "❌ на сообщение → удалить + предупреждение\n"
        "🔨 на сообщение → удалить + бан + сигнатура\n"
        "\n<b>Эскалация предупреждений:</b>\n"
        f"• {settings.warn_reset_trust_at} — сброс доверия\n"
        f"• {settings.warn_mute_at} — авто-мут на {settings.warn_mute_hours} ч\n"
        f"• {settings.warn_ban_at} — бан\n"
        f"Каждое предупреждение живёт {settings.warn_ttl_days} дней.\n"
        "\n<b>Форматы для доменов:</b>\n"
        "• <code>example.com</code> — конкретный домен\n"
        "• <code>*.example.com</code> — wildcard\n"
        "• <code>@channel</code> или <code>t.me/channel</code> — конкретный TG-канал\n"
        "• <code>t.me</code> — общий whitelist для всех TG-ссылок"
    )
    await message.reply(text, parse_mode="HTML")


@router.message(Command("backup"))
async def cmd_backup(message: Message, bot: Bot) -> None:
    """Сделать бэкап БД и отправить в backup-чат прямо сейчас."""
    if not _is_admin_msg(message):
        return
    status_msg = await message.reply("📦 Делаю бэкап...")
    try:
        path = await send_backup(bot, manual=True)
        if not path:
            await status_msg.edit_text("⚠️ Бэкап отключён в настройках.")
            return
        if settings.backup_chat_id:
            await status_msg.edit_text(
                f"✅ Бэкап отправлен в чат <code>{settings.backup_chat_id}</code>.\n"
                f"Локально: <code>{escape(str(path))}</code>",
                parse_mode="HTML",
            )
        else:
            await status_msg.edit_text(
                f"✅ Бэкап сохранён локально: <code>{escape(str(path))}</code>\n"
                f"ℹ️ <code>BACKUP_CHAT_ID</code> не задан — в Telegram не отправляю. "
                f"Чтобы получать бэкап сообщением — настрой через "
                f"<code>/setcfg backup_chat_id ID</code> или <code>/menu → 📦 Бэкап</code>.",
                parse_mode="HTML",
            )
    except Exception as e:
        logger.error("manual backup failed: %s", e)
        await status_msg.edit_text(f"❌ Ошибка: <code>{escape(str(e))}</code>",
                                    parse_mode="HTML")


# ────────────────────────────── FAQ ──────────────────────────────

@router.message(Command("addfaq"))
async def cmd_addfaq(message: Message, command: CommandObject) -> None:
    """
    /addfaq триггер1, триггер2, фраза 3 :: текст ответа

    Триггеры через запятую, разделитель '::' между триггерами и ответом.
    Совпадение — substring в нормализованном тексте (lowercase + NFKC).
    """
    if not _is_admin_msg(message) or not command.args:
        return
    args = command.args
    if "::" not in args:
        await message.reply(
            "Формат: <code>/addfaq триггер1, триггер2 :: текст ответа</code>",
            parse_mode="HTML",
        )
        return
    triggers_part, answer_part = args.split("::", 1)
    triggers = [t.strip() for t in triggers_part.split(",") if t.strip()]
    answer = answer_part.strip()
    if not triggers or not answer:
        await message.reply("Нужны хотя бы один триггер и текст ответа.")
        return
    faq_id = await FaqRepo.add(triggers, answer, message.from_user.id)
    if faq_module.faq_service:
        await faq_module.faq_service.reload()
    await AuditRepo.log(message.from_user.id, None, "addfaq",
                        f"id={faq_id};triggers={triggers}")
    await message.reply(
        f"✅ FAQ #{faq_id} добавлен. Триггеры: <code>{escape(', '.join(triggers))}</code>",
        parse_mode="HTML",
    )


@router.message(Command("listfaq"))
async def cmd_listfaq(message: Message) -> None:
    if not _is_admin_msg(message):
        return
    rows = await FaqRepo.list_all()
    if not rows:
        await message.reply("Список FAQ пуст. Добавь через /addfaq")
        return
    lines = ["<b>FAQ-записи:</b>", ""]
    for r in rows:
        triggers = ", ".join(r.get("triggers_list") or [])
        preview = r["answer"][:60].replace("\n", " ")
        if len(r["answer"]) > 60:
            preview += "…"
        lines.append(
            f"#{r['id']} (исп. {r['use_count']}): "
            f"<code>{escape(triggers)}</code> → {escape(preview)}"
        )
    await message.reply("\n".join(lines), parse_mode="HTML")


@router.message(Command("showfaq"))
async def cmd_showfaq(message: Message, command: CommandObject) -> None:
    if not _is_admin_msg(message) or not command.args:
        return
    try:
        faq_id = int(command.args.strip())
    except ValueError:
        await message.reply("Формат: /showfaq <id>")
        return
    rec = await FaqRepo.get(faq_id)
    if not rec:
        await message.reply(f"FAQ #{faq_id} не найден.")
        return
    triggers = ", ".join(rec.get("triggers_list") or [])
    text = (
        f"<b>FAQ #{faq_id}</b>\n"
        f"Триггеры: <code>{escape(triggers)}</code>\n"
        f"Использован: <b>{rec['use_count']}</b> раз\n\n"
        f"<b>Ответ:</b>\n{escape(rec['answer'])}"
    )
    await message.reply(text, parse_mode="HTML")


@router.message(Command("rmfaq"))
async def cmd_rmfaq(message: Message, command: CommandObject) -> None:
    if not _is_admin_msg(message) or not command.args:
        return
    try:
        faq_id = int(command.args.strip())
    except ValueError:
        await message.reply("Формат: /rmfaq <id>")
        return
    ok = await FaqRepo.remove(faq_id)
    if ok and faq_module.faq_service:
        await faq_module.faq_service.reload()
    await AuditRepo.log(message.from_user.id, None, "rmfaq", str(faq_id))
    await message.reply(f"{'✅' if ok else '❌'} FAQ #{faq_id} {'удалён' if ok else 'не найден'}.")


@router.message(Command("testfaq"))
async def cmd_testfaq(message: Message, command: CommandObject) -> None:
    """Проверить какой FAQ сработает на заданном тексте."""
    if not _is_admin_msg(message) or not command.args:
        return
    if not faq_module.faq_service:
        await message.reply("FAQ-сервис не инициализирован.")
        return
    test_text = command.args
    # Тестовая проверка не должна затронуть реальный кулдаун.
    # Делаем независимую инспекцию:
    rows = await FaqRepo.list_all()
    matches = []
    norm = test_text.lower()
    for r in rows:
        for t in r.get("triggers_list") or []:
            if t and t in norm:
                matches.append((r["id"], t, r["answer"]))
                break
    if not matches:
        await message.reply("Ничего не найдено.")
        return
    lines = []
    for fid, trig, ans in matches[:5]:
        preview = ans[:80].replace("\n", " ") + ("…" if len(ans) > 80 else "")
        lines.append(f"#{fid} триггер «<code>{escape(trig)}</code>» → {escape(preview)}")
    await message.reply("\n".join(lines), parse_mode="HTML")


# ────────────────────────────── Импорт/экспорт списков ──────────────────────────────

@router.message(Command("exportlists"))
async def cmd_exportlists(message: Message) -> None:
    """Выгружает domains, words, faq в JSON-файл."""
    if not _is_admin_msg(message):
        return
    import json as _json
    from aiogram.types import BufferedInputFile

    domains = await DomainRepo.list_all()
    words = await WordRepo.list_all()
    faq_rows = await FaqRepo.list_all()

    data = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "domains": [{"domain": d, "status": s} for d, s in domains],
        "words": [{"word": w, "status": s} for w, s in words],
        "faq": [
            {"triggers": r.get("triggers_list") or [], "answer": r["answer"]}
            for r in faq_rows
        ],
    }
    payload = _json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    fname = f"ahrimod-lists-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
    file = BufferedInputFile(payload, filename=fname)
    await message.reply_document(
        file,
        caption=(
            f"📤 Экспорт: <b>{len(domains)}</b> доменов, "
            f"<b>{len(words)}</b> слов, <b>{len(faq_rows)}</b> FAQ"
        ),
        parse_mode="HTML",
    )


@router.message(Command("importlists"))
async def cmd_importlists(message: Message, bot: Bot) -> None:
    """
    Реплай на сообщение с JSON-файлом → импорт в БД.
    Существующие записи обновляются, новые добавляются.
    Формат файла такой же, как у /exportlists.
    """
    if not _is_admin_msg(message):
        return
    if not message.reply_to_message or not message.reply_to_message.document:
        await message.reply("Реплай на сообщение с приложенным JSON-файлом.")
        return
    doc = message.reply_to_message.document
    if doc.file_size and doc.file_size > 5 * 1024 * 1024:
        await message.reply("Файл слишком большой (>5 MB).")
        return

    import io
    import json as _json
    file_obj = io.BytesIO()
    await bot.download(doc, destination=file_obj)
    file_obj.seek(0)
    try:
        data = _json.loads(file_obj.read().decode("utf-8"))
    except Exception as e:
        await message.reply(f"❌ Не удалось распарсить JSON: <code>{escape(str(e))}</code>",
                            parse_mode="HTML")
        return

    added = {"domains": 0, "words": 0, "faq": 0}
    errors = []

    for d in data.get("domains") or []:
        try:
            await DomainRepo.set_status(
                _normalize_domain_input(d["domain"]),
                d.get("status", "blocked"),
                message.from_user.id,
            )
            added["domains"] += 1
        except Exception as e:
            errors.append(f"domain {d}: {e}")

    for w in data.get("words") or []:
        try:
            await WordRepo.set_status(
                w["word"].strip().lower(),
                w.get("status", "blocked"),
                message.from_user.id,
            )
            added["words"] += 1
        except Exception as e:
            errors.append(f"word {w}: {e}")

    for f in data.get("faq") or []:
        try:
            triggers = f.get("triggers") or []
            answer = f.get("answer") or ""
            if isinstance(triggers, str):
                triggers = [t.strip() for t in triggers.split(",") if t.strip()]
            if triggers and answer:
                await FaqRepo.add(triggers, answer, message.from_user.id)
                added["faq"] += 1
        except Exception as e:
            errors.append(f"faq: {e}")

    if faq_module.faq_service:
        await faq_module.faq_service.reload()
    await AuditRepo.log(message.from_user.id, None, "importlists",
                        f"added={added};errors={len(errors)}")

    summary = (
        f"📥 Импортировано: <b>{added['domains']}</b> доменов, "
        f"<b>{added['words']}</b> слов, <b>{added['faq']}</b> FAQ.\n"
    )
    if errors:
        summary += f"⚠️ Ошибок: {len(errors)} (см. логи бота)"
        for e in errors[:5]:
            logger.warning("importlists error: %s", e)
    await message.reply(summary, parse_mode="HTML")
