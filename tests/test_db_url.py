"""Tests for the asyncpg-safe DATABASE_URL helper."""
from __future__ import annotations

from app.db import _asyncpg_safe_url, _ssl_for_url


def test_strips_neon_query_params():
    url = ("postgresql://user:pass@host:5432/db"
           "?channel_binding=require&sslmode=require")
    safe = _asyncpg_safe_url(url)
    # Driver swapped to asyncpg.
    assert safe.startswith("postgresql+asyncpg://")
    # Query params stripped (asyncpg can't consume channel_binding/sslmode).
    assert "?" not in safe
    assert "channel_binding" not in safe
    assert "sslmode" not in safe


def test_handles_postgres_scheme():
    url = "postgres://u:p@h:5432/db?sslmode=require"
    safe = _asyncpg_safe_url(url)
    assert safe.startswith("postgresql+asyncpg://")
    assert "?" not in safe


def test_plain_url_unchanged():
    url = "postgresql+asyncpg://u:p@h:5432/db"
    assert _asyncpg_safe_url(url) == url


def test_ssl_defaults_to_require():
    url = "postgresql://u:p@h:5432/db"
    assert _ssl_for_url(url) == "require"


def test_ssl_disable_honored():
    assert _ssl_for_url("postgresql://u:p@h:5432/db?ssl=disable") is False


def test_sslmode_disable_honored():
    assert _ssl_for_url("postgresql://u:p@h:5432/db?sslmode=disable") is False


def test_sslmode_require_keeps_require():
    assert _ssl_for_url("postgresql://u:p@h:5432/db?sslmode=require") == "require"
