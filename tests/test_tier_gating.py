"""Tests for tier-based feature gating (Task 19)."""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.middleware.tier_gating import get_user_tier, require_tier

client = TestClient(app)


@pytest.mark.asyncio
async def test_get_user_tier_default():
    assert await get_user_tier("") == "free"


@pytest.mark.asyncio
async def test_require_tier_free_allowed():
    dep = require_tier("free")
    assert await dep("") == "free"


@pytest.mark.asyncio
async def test_require_tier_pro_denied():
    dep = require_tier("pro")
    with pytest.raises(HTTPException) as excinfo:
        await dep("")
    assert excinfo.value.status_code == 403
