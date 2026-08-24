"""Logging setup for VEXARIUM.

Level comes from settings.log_level (env LOG_LEVEL): debug, verbose, info,
warning, error, critical. `verbose` is an alias for debug (max detail). The
whole app logs under the `vexarium` namespace so one call configures it all.

Usage: `from app.logging import get_logger; logger = get_logger("cache")`
"""
from __future__ import annotations

import logging

_LEVELS = {
    "debug": logging.DEBUG,
    "verbose": logging.DEBUG,  # alias: most detail
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}

_FORMAT = "%(asctime)s %(name)s %(levelname)s %(message)s"


def resolve_level(name: str) -> int:
    """Map a LOG_LEVEL string to a logging level. Case-insensitive; anything
    unrecognized falls back to INFO (never to a broken level)."""
    return _LEVELS.get((name or "").strip().lower(), logging.INFO)


def configure_logging(level: str = "info") -> None:
    """Idempotent: sets the handler format and the vexarium/uvicorn logger
    levels in one place. Call once at startup."""
    resolved = resolve_level(level)
    logging.basicConfig(level=resolved, format=_FORMAT)
    for ns in ("vexarium", "uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(ns).setLevel(resolved)


def get_logger(name: str) -> logging.Logger:
    """Logger under the shared `vexarium` namespace, e.g. get_logger('cache')."""
    return logging.getLogger(f"vexarium.{name}")
