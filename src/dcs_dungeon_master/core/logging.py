"""Logging helpers."""

from __future__ import annotations

import logging

from dcs_dungeon_master.core.config import LoggingConfig


def configure_logging(config: LoggingConfig) -> None:
    level = getattr(logging, config.level.upper(), logging.INFO)
    if config.format.value == "json":
        format_string = '{"level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}'
    else:
        format_string = "%(levelname)s %(name)s %(message)s"
    logging.basicConfig(level=level, format=format_string, force=True)
