# -*- coding: utf-8 -*-
"""Извлечение ссылок из Telegram-сообщений со всеми типами entities."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional
from urllib.parse import urlparse

from aiogram.types import Message, MessageEntity

from .text_normalizer import normalize_domain, normalize_for_compare


# Регулярки по тексту — на случай если entities не распарсились
# (например, ссылка специально написана с пробелами или необычной TLD).
_URL_RE = re.compile(
    r"""(?xi)
    \b
    (?:https?://|tg://|ftp://)?           # схема (опционально)
    (?:[a-z0-9-]+\.)+                     # поддомены
    [a-z]{2,24}                           # TLD
    (?:[/?\#][^\s<>"']*)?                 # путь/query
    """,
)

# Домены верхнего уровня, которым мы верим при поиске ссылок РЕГУЛЯРКОЙ.
# Без этого списка «слово.слово» из обычной речи считается ссылкой: в чате
# постоянно пишут config.json, main.py, app.apk, setup.exe — и сообщение
# новичка улетало на модерацию с удалением. Ссылки, которые уже распарсил
# сам Telegram (entities url/text_link), проверку по списку не проходят —
# там TLD может быть любым.
_KNOWN_TLDS = frozenset("""
com net org info biz pro name mobi xyz top site online store shop club live life
app dev io ai co me tv cc ws to gg gl ly link click cloud page web space website
fun world today news blog wiki tech digital media network email chat social
group team work agency company center city global one now zone plus vip win bet
cash money finance credit bank trade market sale deal gift best cool best hot
ru su рф ua by kz uz kg tj tm md am ge az lv lt ee pl cz sk hu ro bg rs hr si
de fr it es pt nl be ch at dk se no fi is ie uk gb eu
us ca mx br ar cl pe ve cu ai in cn jp kr hk tw sg my th vn ph id au nz
tr il ae sa eg za ng ke ma dz tn ir pk bd lk np mm kh la mn
xn ml tk ga cf gq pw sh st tf yt cx cd cm gs mu re fm
edu gov mil int arpa travel museum aero coop jobs post tel asia cat
""".split())


def _has_known_tld(host: str) -> bool:
    """TLD хоста есть в списке доверенных (для regex-фолбэка)."""
    if "." not in host:
        return False
    return host.rsplit(".", 1)[1].lower() in _KNOWN_TLDS


def find_url_like(text: str) -> list[str]:
    """
    Ищет в уже нормализованном тексте то, что выглядит как ссылка,
    и отсеивает «слово.слово» с неизвестным TLD (config.json, main.py).

    Используется и модерацией сообщений, и проверкой имени нового участника.
    """
    out: list[str] = []
    for m in _URL_RE.finditer(text):
        raw = m.group(0)
        host = raw.split("://", 1)[-1].split("/", 1)[0].split("?", 1)[0]
        if raw.lower().startswith(("http://", "https://", "tg://", "ftp://")) or _has_known_tld(host):
            out.append(raw)
    return out

# username-упоминания каналов/ботов: @channel_name (минимум 5 символов как у Telegram)
_USERNAME_RE = re.compile(r"(?<![\w@])@([a-zA-Z][a-zA-Z0-9_]{4,31})")


@dataclass(slots=True)
class ExtractedLink:
    """Найденная ссылка."""
    raw: str           # как было в сообщении
    domain: str        # домен для блокировки целиком: хост или 't.me/<канал>'
    full: str          # точная ссылка для блокировки: хост + путь (без схемы/query)
    is_telegram: bool  # это ссылка на t.me / @channel / tg://


# Хосты Telegram, которые мы канонизируем к 't.me', чтобы t.me / telegram.me /
# telegram.dog и одинаковые @username совпадали между собой.
_TG_HOSTS = {"t.me", "telegram.me", "telegram.dog"}


def _telegram_channel_domain(path: str) -> str:
    """
    Для telegram-ссылки возвращает 't.me/<канал>' — привязку к конкретному
    каналу/пользователю, а не к голому 't.me'. Номер поста отбрасывается.

    Примеры:
      https://t.me/AhriVPN/52        -> t.me/ahrivpn
      https://t.me/AhriVPN           -> t.me/ahrivpn
      https://t.me/s/AhriVPN         -> t.me/ahrivpn        (s/ — web-превью)
      https://t.me/+abcXYZ           -> t.me/+abcxyz        (инвайт-ссылка)
      https://t.me/joinchat/HASH     -> t.me/joinchat/hash  (инвайт-ссылка)
      https://t.me/c/123456/789      -> t.me/c/123456       (приватный канал)
      https://t.me/                  -> t.me
    """
    segments = [s for s in path.split("/") if s]
    if not segments:
        return "t.me"
    first = segments[0].lower()
    # web-превью: t.me/s/<канал>
    if first == "s" and len(segments) > 1:
        return f"t.me/{segments[1].lower()}"
    # приватный канал по внутреннему id: t.me/c/<id>/<msg>
    if first == "c" and len(segments) > 1:
        return f"t.me/c/{segments[1].lower()}"
    # инвайт через joinchat: t.me/joinchat/<hash>
    if first == "joinchat" and len(segments) > 1:
        return f"t.me/joinchat/{segments[1].lower()}"
    return f"t.me/{first}"


def _parse_url(url: str) -> tuple[str, str, bool]:
    """Парсит URL и возвращает (domain, full, is_telegram).

      domain — для блокировки домена целиком: хост ('apps.apple.com')
               или 't.me/<канал>' для telegram.
      full   — для блокировки ТОЧНОЙ ссылки: хост + путь, без схемы и query
               ('apps.apple.com/ru/app/incy/id6756943388'). Для telegram
               совпадает с domain (на уровне канала).

    Пустые строки если URL не парсится.
    """
    if not url:
        return "", "", False
    # urlparse требует схему
    if "://" not in url:
        url = "http://" + url.lstrip("/")
    try:
        parsed = urlparse(url)
    except Exception:
        return "", "", False
    host = normalize_domain(parsed.hostname or "")
    if not host:
        return "", "", False
    if host in _TG_HOSTS or host.endswith(".t.me"):
        channel = _telegram_channel_domain(parsed.path or "")
        return channel, channel, True
    # обычный домен: domain = хост, full = хост + путь (query/fragment отбрасываем)
    path = (parsed.path or "").rstrip("/").lower()
    full = host + path if path else host
    return host, full, False


def _is_telegram_domain(domain: str) -> bool:
    # domain может быть как голым хостом, так и 't.me/<канал>' — берём хост-часть.
    host = domain.split("/", 1)[0]
    return host in {"t.me", "telegram.me", "telegram.dog"} or host.endswith(".t.me")


def extract_links(message: Message) -> list[ExtractedLink]:
    """
    Извлекает все ссылки из сообщения.
    Источники:
      1. message.entities / message.caption_entities (url, text_link, mention)
      2. Регулярка по нормализованному тексту (на случай скрытых через гомоглифы)
      3. message.forward_from_chat / forward_origin — пересылка из канала
      4. Inline-кнопки с url (reply_markup)
    """
    text = message.text or message.caption or ""
    entities = list(message.entities or []) + list(message.caption_entities or [])
    found: dict[tuple[str, str], ExtractedLink] = {}

    def add(raw: str, domain: str, full: str, is_tg: bool) -> None:
        key = (domain, full)
        if key not in found:
            found[key] = ExtractedLink(raw=raw, domain=domain, full=full, is_telegram=is_tg)

    # 1. Entities
    for ent in entities:
        if ent.type == "url":
            raw = text[ent.offset : ent.offset + ent.length]
            domain, full, is_tg = _parse_url(raw)
            if domain:
                add(raw, domain, full, is_tg)
        elif ent.type == "text_link" and ent.url:
            domain, full, is_tg = _parse_url(ent.url)
            if domain:
                add(ent.url, domain, full, is_tg)
        elif ent.type == "mention":
            raw = text[ent.offset : ent.offset + ent.length]  # @username
            username = raw.lstrip("@")
            tg = f"t.me/{username.lower()}"
            add(raw, tg, tg, True)
        elif ent.type == "text_mention" and ent.user:
            add(f"tg://user?id={ent.user.id}", "tg.user", "tg.user", True)

    # 2. Регулярка по нормализованному тексту (ловим обфусцированное).
    # find_url_like отсекает «слово.слово» с неизвестным TLD — иначе
    # обычные config.json и main.py считались бы ссылками.
    normalized = normalize_for_compare(text)
    for raw in find_url_like(normalized):
        domain, full, is_tg = _parse_url(raw)
        if domain:
            add(raw, domain, full, is_tg)

    # @username упоминания через regex (если entities их не дали)
    for m in _USERNAME_RE.finditer(normalized):
        username = m.group(1).lower()
        # @admin / @everyone и подобное — пропустим короткие
        tg = f"t.me/{username}"
        add(f"@{username}", tg, tg, True)

    # 3. Forward из канала — это де-факто реклама канала
    fwd_chat = getattr(message, "forward_from_chat", None)
    if fwd_chat and getattr(fwd_chat, "username", None):
        username = fwd_chat.username.lower()
        tg = f"t.me/{username}"
        add(f"forward:@{username}", tg, tg, True)

    # 4. Inline-кнопки в reply_markup
    if message.reply_markup and message.reply_markup.inline_keyboard:
        for row in message.reply_markup.inline_keyboard:
            for btn in row:
                if btn.url:
                    domain, full, is_tg = _parse_url(btn.url)
                    if domain:
                        add(btn.url, domain, full, is_tg)

    return list(found.values())