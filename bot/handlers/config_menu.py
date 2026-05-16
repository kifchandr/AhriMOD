# -*- coding: utf-8 -*-
"""
Меню настроек. Команды:
  /config             — показать все настройки
  /menu               — интерактивное меню с inline-кнопками
  /setcfg <key> <val> — изменить настройку напрямую
  /resetcfg <key>     — сбросить к значению из .env
"""
from __future__ import annotations

import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from ..config import RUNTIME_FIELDS, settings
from ..db.repositories import AuditRepo

logger = logging.getLogger(__name__)
router = Router(name="config_menu")


def _is_admin_msg(message: Message) -> bool:
    return bool(message.from_user and message.from_user.id in settings.admin_user_ids)


def _is_admin_cb(callback: CallbackQuery) -> bool:
    return bool(callback.from_user and callback.from_user.id in settings.admin_user_ids)


# Группы настроек для удобного меню
GROUPS: dict[str, tuple[str, list[str]]] = {
    "trust": ("🛡 Доверие", [
        "trust_min_hours", "trust_min_messages", "trust_min_interval_minutes",
    ]),
    "filters": ("🔍 Фильтры", [
        "use_cas", "simhash_threshold", "signature_min_length",
        "restrict_media_for_untrusted",
    ]),
    "warns": ("⚠️ Предупреждения", [
        "warn_reset_trust_at", "warn_mute_at", "warn_mute_hours",
        "warn_ban_at", "warn_ttl_days",
        "notify_on_warn", "warn_notification_ttl_seconds",
    ]),
    "punish": ("🔨 Наказания новичков", [
        "new_user_punishment", "mute_duration_minutes",
    ]),
    "backup": ("📦 Бэкап", [
        "backup_enabled", "backup_chat_id", "backup_thread_id",
        "backup_hour", "backup_keep_days",
    ]),
    "misc": ("🔧 Прочее", [
        "faq_cooldown_minutes", "recent_messages_ttl_days",
        "notify_on_new_member", "new_member_thread_id",
    ]),
}


def _fmt_value(key: str) -> str:
    """Форматирует текущее значение для отображения."""
    val = settings.current(key)
    if isinstance(val, bool):
        return "✅ Вкл" if val else "❌ Выкл"
    return f"<code>{escape(str(val))}</code>"


def _value_marker(key: str) -> str:
    """Маркер если значение переопределено через БД (📝) или из .env."""
    return "📝" if settings.is_overridden(key) else "  "


# ──────────────────────── Команды ────────────────────────

@router.message(Command("config"))
async def cmd_config(message: Message) -> None:
    """Показать все настройки сгруппированно."""
    if not _is_admin_msg(message):
        return
    lines = ["<b>⚙️ Настройки</b>",
             "(<code>📝</code> = переопределено через бота, в остальном — из .env)",
             ""]
    for _gkey, (label, keys) in GROUPS.items():
        lines.append(f"<b>{label}</b>")
        for k in keys:
            lines.append(f"{_value_marker(k)} <code>{k}</code> = {_fmt_value(k)}")
        lines.append("")
    lines.append("Открыть меню: /menu")
    lines.append("Изменить: /setcfg key value")
    lines.append("Сбросить к .env: /resetcfg key")
    await message.reply("\n".join(lines), parse_mode="HTML")


@router.message(Command("setcfg"))
async def cmd_setcfg(message: Message, command: CommandObject) -> None:
    if not _is_admin_msg(message) or not command.args:
        return
    parts = command.args.split(maxsplit=1)
    if len(parts) != 2:
        await message.reply("Формат: <code>/setcfg key value</code>", parse_mode="HTML")
        return
    key, raw_value = parts
    if key not in RUNTIME_FIELDS:
        await message.reply(
            f"❌ <code>{escape(key)}</code> — не runtime-настройка.\n"
            "Список: /config",
            parse_mode="HTML",
        )
        return
    try:
        await settings.update(key, raw_value, updated_by=message.from_user.id)
        await AuditRepo.log(message.from_user.id, None, "setcfg",
                            f"{key}={raw_value}")
        await message.reply(
            f"✅ <code>{escape(key)}</code> = {_fmt_value(key)}",
            parse_mode="HTML",
        )
    except Exception as e:
        await message.reply(f"❌ Ошибка: <code>{escape(str(e))}</code>", parse_mode="HTML")


@router.message(Command("resetcfg"))
async def cmd_resetcfg(message: Message, command: CommandObject) -> None:
    if not _is_admin_msg(message) or not command.args:
        return
    key = command.args.strip()
    if key not in RUNTIME_FIELDS:
        await message.reply(f"❌ <code>{escape(key)}</code> не runtime-настройка.",
                            parse_mode="HTML")
        return
    await settings.reset(key)
    await AuditRepo.log(message.from_user.id, None, "resetcfg", key)
    await message.reply(
        f"✅ <code>{escape(key)}</code> сброшено → {_fmt_value(key)} (из .env)",
        parse_mode="HTML",
    )


# ──────────────────────── Интерактивное меню ────────────────────────

class ConfigStates(StatesGroup):
    waiting_value = State()


def _groups_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for gkey, (label, _) in GROUPS.items():
        rows.append([InlineKeyboardButton(text=label, callback_data=f"cfg:grp:{gkey}")])
    rows.append([InlineKeyboardButton(text="❌ Закрыть", callback_data="cfg:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _group_keyboard(gkey: str) -> InlineKeyboardMarkup:
    label, keys = GROUPS[gkey]
    rows = []
    for k in keys:
        kind = RUNTIME_FIELDS[k]
        marker = "📝" if settings.is_overridden(k) else "  "
        if kind is bool:
            val = "✅" if settings.current(k) else "❌"
            text = f"{marker}{val} {k}"
            rows.append([InlineKeyboardButton(text=text,
                                              callback_data=f"cfg:tog:{k}")])
        else:
            val = settings.current(k)
            text = f"{marker}{k} = {val}"
            rows.append([InlineKeyboardButton(text=text,
                                              callback_data=f"cfg:edit:{k}")])
    rows.append([
        InlineKeyboardButton(text="« Назад", callback_data="cfg:back"),
        InlineKeyboardButton(text="❌ Закрыть", callback_data="cfg:close"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
    if not _is_admin_msg(message):
        return
    await message.reply(
        "<b>⚙️ Настройки бота</b>\nВыбери группу:",
        reply_markup=_groups_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "cfg:back")
async def cb_back(callback: CallbackQuery) -> None:
    if not _is_admin_cb(callback):
        await callback.answer("Только для админов", show_alert=True)
        return
    try:
        await callback.message.edit_text(
            "<b>⚙️ Настройки бота</b>\nВыбери группу:",
            reply_markup=_groups_keyboard(),
            parse_mode="HTML",
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "cfg:close")
async def cb_close(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin_cb(callback):
        await callback.answer("Только для админов", show_alert=True)
        return
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("cfg:grp:"))
async def cb_group(callback: CallbackQuery) -> None:
    if not _is_admin_cb(callback):
        await callback.answer("Только для админов", show_alert=True)
        return
    gkey = callback.data.split(":", 2)[2]
    if gkey not in GROUPS:
        await callback.answer("Неизвестная группа")
        return
    label, _ = GROUPS[gkey]
    try:
        await callback.message.edit_text(
            f"<b>{label}</b>\n"
            "(<code>📝</code> = переопределено)\n\n"
            "Жми на bool-настройку чтобы переключить, на не-bool — чтобы изменить.",
            reply_markup=_group_keyboard(gkey),
            parse_mode="HTML",
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("cfg:tog:"))
async def cb_toggle(callback: CallbackQuery) -> None:
    """Переключение bool-настройки."""
    if not _is_admin_cb(callback):
        await callback.answer("Только для админов", show_alert=True)
        return
    key = callback.data.split(":", 2)[2]
    if key not in RUNTIME_FIELDS or RUNTIME_FIELDS[key] is not bool:
        await callback.answer("Не bool")
        return
    new_val = not bool(settings.current(key))
    await settings.update(key, new_val, updated_by=callback.from_user.id)
    await AuditRepo.log(callback.from_user.id, None, "toggle_cfg",
                        f"{key}={new_val}")
    # Найти в какой группе ключ и перерисовать группу
    for gkey, (label, keys) in GROUPS.items():
        if key in keys:
            try:
                await callback.message.edit_reply_markup(
                    reply_markup=_group_keyboard(gkey)
                )
            except Exception:
                pass
            break
    await callback.answer(f"{key} → {'Вкл' if new_val else 'Выкл'}")


@router.callback_query(F.data.startswith("cfg:edit:"))
async def cb_edit(callback: CallbackQuery, state: FSMContext) -> None:
    """Запрос нового значения через FSM."""
    if not _is_admin_cb(callback):
        await callback.answer("Только для админов", show_alert=True)
        return
    key = callback.data.split(":", 2)[2]
    if key not in RUNTIME_FIELDS:
        await callback.answer("Неизвестный ключ")
        return
    kind = RUNTIME_FIELDS[key].__name__
    current = settings.current(key)
    await state.set_state(ConfigStates.waiting_value)
    await state.update_data(key=key, chat_id=callback.message.chat.id,
                            menu_msg_id=callback.message.message_id)
    await callback.message.reply(
        f"Введи новое значение для <code>{escape(key)}</code> "
        f"(тип: {kind}, сейчас: <code>{escape(str(current))}</code>):\n"
        "Или /cancel чтобы отменить.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(Command("cancel"), ConfigStates.waiting_value)
async def cancel_edit(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.reply("Отменено.")


@router.message(ConfigStates.waiting_value)
async def on_new_value(message: Message, state: FSMContext) -> None:
    if not _is_admin_msg(message):
        return
    data = await state.get_data()
    key = data.get("key")
    if not key or key not in RUNTIME_FIELDS:
        await state.clear()
        return
    raw = (message.text or "").strip()
    try:
        await settings.update(key, raw, updated_by=message.from_user.id)
        await AuditRepo.log(message.from_user.id, None, "setcfg",
                            f"{key}={raw}")
        await message.reply(
            f"✅ <code>{escape(key)}</code> = {_fmt_value(key)}",
            parse_mode="HTML",
        )
    except Exception as e:
        await message.reply(
            f"❌ Не удалось преобразовать: <code>{escape(str(e))}</code>",
            parse_mode="HTML",
        )
    finally:
        await state.clear()
