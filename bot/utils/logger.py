# -*- coding: utf-8 -*-
"""Настройка логгирования с rich-форматированием и цветами даже в journald."""
from __future__ import annotations

import logging
import os
import sys

from rich.console import Console
from rich.logging import RichHandler


def setup_logging(level: str = "INFO") -> None:
    # Rich по умолчанию отключает цвета в не-TTY (например в systemd journald).
    # Чтобы цвета сохранялись в логах journalctl, форсируем цветной вывод
    # через FORCE_COLOR=1 (стандартный envvar) или force_terminal=True.
    force_color = (
        os.environ.get("FORCE_COLOR", "").lower() in ("1", "true", "yes")
        or os.environ.get("CLICOLOR_FORCE", "").lower() in ("1", "true", "yes")
        or sys.stderr.isatty()
    )

    console = Console(
        force_terminal=force_color,
        color_system="256" if force_color else None,
        width=120,
    )

    handler = RichHandler(
        console=console,
        rich_tracebacks=True,
        show_path=False,
        markup=True,
        show_level=True,
        show_time=True,
        log_time_format="[%X]",
    )

    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[handler],
    )

    # Меньше шума от внешних либ
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
