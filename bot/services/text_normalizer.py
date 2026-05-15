# -*- coding: utf-8 -*-
"""Нормализация текста для борьбы с обходами фильтров."""
from __future__ import annotations

import re
import unicodedata


# Невидимые/нулевой ширины символы — частая обфускация
_INVISIBLE = re.compile(
    r"[\u200B-\u200F\u202A-\u202E\u2060-\u206F\uFEFF\u00AD]"
)

# Полный набор похожих кириллических букв -> латинские (для нормализации доменов и слов)
# Только реально визуально идентичные глифы (homoglyphs) которые используют для обхода фильтров.
# НЕ заменяем те что выглядят похоже но разные ('н'≠'h', 'в'≠'b' в нижнем регистре),
# чтобы не ломать обычный русский текст.
_HOMOGLYPHS_CYR_TO_LAT = str.maketrans({
    "а": "a", "А": "A",
    "е": "e", "Е": "E",
    "о": "o", "О": "O",
    "р": "p", "Р": "P",
    "с": "c", "С": "C",
    "у": "y", "У": "Y",
    "х": "x", "Х": "X",
    "К": "K",
    "Т": "T",
    "М": "M",
    "Н": "H",
    "В": "B",
    "і": "i", "І": "I",
    "ј": "j", "Ј": "J",
    "ѕ": "s",
    "ԁ": "d",
})

# Спецсимволы-разделители часто заменяются на похожие, нормализуем точку
_DOT_LIKES = str.maketrans({
    "․": ".",  # U+2024
    "．": ".", # U+FF0E
    "·": ".",  # бывает
})


def strip_invisible(text: str) -> str:
    """Удаляет zero-width и управляющие символы."""
    return _INVISIBLE.sub("", text)


def normalize_for_compare(text: str) -> str:
    """
    Нормализация для сравнения текста (поиск стоп-слов, сигнатуры).
    Цель: сделать обход через гомоглифы/невидимые/регистр невозможным.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = strip_invisible(text)
    text = text.lower()
    text = text.translate(_HOMOGLYPHS_CYR_TO_LAT)
    text = text.translate(_DOT_LIKES)
    # схлопываем повторяющиеся пробелы
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_domain(domain: str) -> str:
    """Нормализация домена: lowercase, NFKC, убираем www., zero-width, гомоглифы."""
    if not domain:
        return ""
    domain = unicodedata.normalize("NFKC", domain)
    domain = strip_invisible(domain)
    domain = domain.lower().strip()
    domain = domain.translate(_HOMOGLYPHS_CYR_TO_LAT)
    domain = domain.translate(_DOT_LIKES)
    if domain.startswith("www."):
        domain = domain[4:]
    # отрезаем порт и trailing-точку
    domain = domain.rstrip(".")
    if ":" in domain:
        domain = domain.split(":", 1)[0]
    return domain


def text_for_signature(text: str) -> str:
    """
    Текст для сигнатур: агрессивнее нормализуем — выкидываем все небуквенно-цифровые
    кроме пробелов, что бы вариации с пунктуацией ловились.
    """
    text = normalize_for_compare(text)
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# Расширенная транслитерация кириллица → латиница. Используется только для
# дополнительной проверки стоп-слов (чтобы поймать обходы типа "nordvпн" → "nordvpn",
# где русские буквы не совсем homoglyphs, но для русскоязычных это "те же буквы").
# Применяется ОТДЕЛЬНО от normalize_for_compare, чтобы не ломать обычный русский текст.
_AGGRESSIVE_TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "j", "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "c", "ш": "s", "щ": "s", "ъ": "",
    "ы": "y", "ь": "", "э": "e", "ю": "u", "я": "a",
})


def aggressive_translit(text: str) -> str:
    """
    Грубая русско-английская транслитерация для поиска обходов.
    НЕ для отображения, только для матчинга.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = strip_invisible(text)
    text = text.lower()
    text = text.translate(_AGGRESSIVE_TRANSLIT)
    text = re.sub(r"\s+", " ", text).strip()
    return text
