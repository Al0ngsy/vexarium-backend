import logging

from app.logging import get_logger, resolve_level


def test_resolve_levels():
    assert resolve_level("verbose") == logging.DEBUG  # alias for debug
    assert resolve_level("debug") == logging.DEBUG
    assert resolve_level("DEBUG") == logging.DEBUG
    assert resolve_level("info") == logging.INFO
    assert resolve_level("warning") == logging.WARNING
    assert resolve_level("error") == logging.ERROR
    assert resolve_level("critical") == logging.CRITICAL
    assert resolve_level("bogus") == logging.INFO  # invalid falls back to INFO
    assert resolve_level("") == logging.INFO
    assert resolve_level(None) == logging.INFO  # type: ignore[arg-type]


def test_get_logger_namespace():
    assert get_logger("cache").name == "vexarium.cache"
    assert get_logger("alpaca").level == logging.NOTSET  # inherit from parent
