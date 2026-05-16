# -*- coding: utf-8 -*-
"""Конфигурация бота, читается из .env."""
from __future__ import annotations

from pathlib import Path
from typing import Annotated, List, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


# Корень проекта — папка ahrimod/, на 2 уровня выше этого файла:
#   .../ahrimod/bot/config.py  →  parents[1] = .../ahrimod/
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    # Абсолютный путь к .env — не зависит от текущей рабочей директории.
    # Это важно при ручном запуске из произвольной cwd (через systemd cwd
    # выставлен в WorkingDirectory, но при отладке может быть любым).
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    bot_token: str = Field(..., alias="BOT_TOKEN")
    admin_chat_id: int = Field(..., alias="ADMIN_CHAT_ID")
    # Опционально: ID темы внутри admin-чата (если это форум-группа).
    # 0 или пусто = писать в общий раздел.
    admin_chat_thread_id: int = Field(0, alias="ADMIN_CHAT_THREAD_ID")
    log_chat_id: int = Field(..., alias="LOG_CHAT_ID")
    log_chat_thread_id: int = Field(0, alias="LOG_CHAT_THREAD_ID")
    # NoDecode: pydantic-settings не пытается сам парсить значение
    # (по умолчанию пытается через JSON и падает на "111,222").
    # Передаём сырую строку в валидатор _split_ids ниже.
    protected_chat_ids: Annotated[List[int], NoDecode] = Field(
        default_factory=list, alias="PROTECTED_CHAT_IDS"
    )
    admin_user_ids: Annotated[List[int], NoDecode] = Field(
        default_factory=list, alias="ADMIN_USER_IDS"
    )

    trust_min_hours: int = Field(24, alias="TRUST_MIN_HOURS")
    trust_min_messages: int = Field(3, alias="TRUST_MIN_MESSAGES")
    trust_min_interval_minutes: int = Field(60, alias="TRUST_MIN_INTERVAL_MINUTES")

    db_path: Path = Field(Path("./data/bot.db"), alias="DB_PATH")

    use_cas: bool = Field(True, alias="USE_CAS")
    simhash_threshold: int = Field(4, alias="SIMHASH_THRESHOLD")
    signature_min_length: int = Field(30, alias="SIGNATURE_MIN_LENGTH")

    new_user_punishment: Literal["ban", "mute"] = Field("ban", alias="NEW_USER_PUNISHMENT")
    mute_duration_minutes: int = Field(60, alias="MUTE_DURATION_MINUTES")

    # ── Предупреждения и эскалация наказаний ──
    # При WARN_RESET_TRUST_AT — обнуляется путь к доверию (юзер становится новым)
    warn_reset_trust_at: int = Field(3, alias="WARN_RESET_TRUST_AT")
    # При WARN_MUTE_AT — авто-мут на WARN_MUTE_HOURS
    warn_mute_at: int = Field(5, alias="WARN_MUTE_AT")
    warn_mute_hours: int = Field(24, alias="WARN_MUTE_HOURS")
    # При WARN_BAN_AT — бан
    warn_ban_at: int = Field(7, alias="WARN_BAN_AT")
    # Через сколько дней предупреждение автоматически списывается
    warn_ttl_days: int = Field(7, alias="WARN_TTL_DAYS")
    # Слать ли в чат уведомление о выдаче предупреждения
    notify_on_warn: bool = Field(True, alias="NOTIFY_ON_WARN")
    # Через сколько секунд удалять уведомление о предупреждении (0 = не удалять)
    warn_notification_ttl_seconds: int = Field(60, alias="WARN_NOTIFICATION_TTL_SECONDS")

    # ── Бэкап БД ──
    backup_enabled: bool = Field(True, alias="BACKUP_ENABLED")
    # 0 = использовать ADMIN_CHAT_ID
    backup_chat_id: int = Field(0, alias="BACKUP_CHAT_ID")
    # 0 = General тема (или не форум-чат)
    backup_thread_id: int = Field(0, alias="BACKUP_THREAD_ID")
    # Час по UTC когда делать ежедневный бэкап (0..23)
    backup_hour: int = Field(4, alias="BACKUP_HOUR")
    # Сколько дней хранить локальные бэкапы в data/backups/
    backup_keep_days: int = Field(30, alias="BACKUP_KEEP_DAYS")

    # ── Дополнительные ограничения для не-доверенных ──
    # Запретить новым/не-доверенным юзерам отправлять фото, видео, кружки, gif
    restrict_media_for_untrusted: bool = Field(True, alias="RESTRICT_MEDIA_FOR_UNTRUSTED")

    # ── FAQ ──
    # Минимальный интервал между двумя срабатываниями одной FAQ записи (минуты)
    faq_cooldown_minutes: int = Field(10, alias="FAQ_COOLDOWN_MINUTES")

    # ── Reactions-as-moderation ──
    # Сколько дней хранить map (chat_id, message_id) → user_id для реакций.
    recent_messages_ttl_days: int = Field(7, alias="RECENT_MESSAGES_TTL_DAYS")

    # ── Уведомления о новых участниках ──
    # Слать ли в лог-чат уведомление при вступлении нового юзера
    # (после фильтров CAS и имя-спам). Полезно мониторить кто заходит.
    notify_on_new_member: bool = Field(False, alias="NOTIFY_ON_NEW_MEMBER")
    # Отдельная тема в лог-чате для этих уведомлений (0 = в LOG_CHAT_THREAD_ID)
    new_member_thread_id: int = Field(0, alias="NEW_MEMBER_THREAD_ID")

    @field_validator("protected_chat_ids", "admin_user_ids", mode="before")
    @classmethod
    def _split_ids(cls, v):
        if v is None or v == "":
            return []
        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        if isinstance(v, int):
            return [v]
        if isinstance(v, (list, tuple)):
            return [int(x) for x in v]
        return v

    @field_validator("db_path", mode="after")
    @classmethod
    def _resolve_db_path(cls, v: Path) -> Path:
        # Относительные пути считаем от корня проекта, а не от cwd
        if not v.is_absolute():
            return (PROJECT_ROOT / v).resolve()
        return v


# Какие настройки можно менять в runtime через /menu и /setcfg.
# Ключи .env (BOT_TOKEN, ADMIN_CHAT_ID, ...) сюда не включаются — их
# нельзя менять без рестарта (бот заходит в Telegram под токеном и
# слушает конкретные чаты).
RUNTIME_FIELDS: dict[str, type] = {
    # Доверие
    "trust_min_hours": int,
    "trust_min_messages": int,
    "trust_min_interval_minutes": int,
    # Фильтры
    "use_cas": bool,
    "simhash_threshold": int,
    "signature_min_length": int,
    # Наказания
    "new_user_punishment": str,
    "mute_duration_minutes": int,
    # Эскалация предупреждений
    "warn_reset_trust_at": int,
    "warn_mute_at": int,
    "warn_mute_hours": int,
    "warn_ban_at": int,
    "warn_ttl_days": int,
    "notify_on_warn": bool,
    "warn_notification_ttl_seconds": int,
    # Бэкап
    "backup_enabled": bool,
    "backup_chat_id": int,
    "backup_thread_id": int,
    "backup_hour": int,
    "backup_keep_days": int,
    # Прочее
    "restrict_media_for_untrusted": bool,
    "faq_cooldown_minutes": int,
    "recent_messages_ttl_days": int,
    "notify_on_new_member": bool,
    "new_member_thread_id": int,
}


def _coerce(raw: str, target_type: type):
    """Преобразование строкового значения из БД к нужному типу."""
    if target_type is bool:
        return raw.strip().lower() in ("1", "true", "yes", "on", "да")
    if target_type is int:
        return int(raw)
    if target_type is float:
        return float(raw)
    return raw


class RuntimeSettings:
    """
    Обёртка над статическим Settings. Атрибуты из RUNTIME_FIELDS могут быть
    переопределены значениями из таблицы `settings` в БД. Остальные —
    только из .env (Settings).
    """
    __slots__ = ("_defaults", "_overrides")

    def __init__(self, defaults: "Settings"):
        # Используем object.__setattr__ потому что __setattr__ переопределён
        object.__setattr__(self, "_defaults", defaults)
        object.__setattr__(self, "_overrides", {})

    async def reload_from_db(self) -> int:
        """
        Перечитывает БД и заполняет кэш переопределений.
        Возвращает количество загруженных значений.
        """
        from .db.repositories import SettingsRepo
        rows = await SettingsRepo.get_all()
        new_overrides: dict = {}
        for key, raw in rows.items():
            target = RUNTIME_FIELDS.get(key)
            if target is None:
                continue
            try:
                new_overrides[key] = _coerce(raw, target)
            except Exception:
                pass
        object.__setattr__(self, "_overrides", new_overrides)
        return len(new_overrides)

    async def update(self, key: str, value, updated_by: Optional[int]) -> None:
        """Сохраняет в БД и обновляет кэш. Значение приводится к типу из RUNTIME_FIELDS."""
        from .db.repositories import SettingsRepo
        target = RUNTIME_FIELDS.get(key)
        if target is None:
            raise KeyError(f"{key} не в RUNTIME_FIELDS")
        if not isinstance(value, target):
            value = _coerce(str(value), target)
        await SettingsRepo.set(key, str(value), updated_by)
        self._overrides[key] = value

    async def reset(self, key: str) -> None:
        """Удаляет переопределение — настройка вернётся к значению из .env."""
        from .db.repositories import SettingsRepo
        await SettingsRepo.delete(key)
        self._overrides.pop(key, None)

    def current(self, key: str):
        """Текущее значение настройки (с учётом override)."""
        if key in self._overrides:
            return self._overrides[key]
        return getattr(self._defaults, key)

    def is_overridden(self, key: str) -> bool:
        return key in self._overrides

    def __getattr__(self, name: str):
        # __getattr__ вызывается только если обычный lookup не нашёл атрибут.
        # _defaults и _overrides доступны через __slots__ — попадаем сюда
        # только для обычных полей.
        overrides = object.__getattribute__(self, "_overrides")
        if name in overrides:
            return overrides[name]
        return getattr(object.__getattribute__(self, "_defaults"), name)


from typing import Optional  # noqa: E402

settings = RuntimeSettings(Settings())
