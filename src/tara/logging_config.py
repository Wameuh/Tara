"""Logging helpers for the standalone Tara application."""

from __future__ import annotations

import logging

from tara.config import LoggingConfig


def apply_logging_config(config: LoggingConfig) -> None:
    """Apply process-wide console logging configuration.

    Args:
        config: Logging configuration loaded from JSON.
    """
    handlers: list[logging.Handler] = []
    if config.console_enabled:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(config.console_format))
        handlers.append(handler)
    logging.basicConfig(
        level=getattr(logging, config.level.upper(), logging.INFO),
        handlers=handlers,
        force=True,
    )
