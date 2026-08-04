"""Tests for the warrants (Optionsscheine) feature."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_warrant_value_call():
    resp = client.get(
        "/api/v1/warrants/PK3U7R/value",
        params={
            "target_price": 20.0, "strike": 18.0, "premium": 5.35,
            "cover_ratio": 1.0, "exercise_right": "CALL",
        },
    )
    assert resp.status_code == 200
    d = resp.json()
    # At target 20 > strike 18, intrinsic = 2.0, premium 5.35 -> P/L = -3.35
    assert d["estimated_option_price"] == pytest.approx(2.0, abs=0.01)
    assert d["estimated_pl"] == pytest.approx(-3.35, abs=0.01)


def test_warrant_value_put():
    resp = client.get(
        "/api/v1/warrants/X/value",
        params={
            "target_price": 16.0, "strike": 18.0, "premium": 2.0,
            "cover_ratio": 1.0, "exercise_right": "PUT",
        },
    )
    assert resp.status_code == 200
    d = resp.json()
    # Put: intrinsic = max(18-16,0) = 2, P/L = 0
    assert d["estimated_option_price"] == pytest.approx(2.0, abs=0.01)
    assert d["estimated_pl"] == pytest.approx(0.0, abs=0.01)


def test_warrant_value_cover_ratio():
    resp = client.get(
        "/api/v1/warrants/X/value",
        params={
            "target_price": 100.0, "strike": 90.0, "premium": 3.0,
            "cover_ratio": 10.0, "exercise_right": "CALL",
        },
    )
    d = resp.json()
    # intrinsic = 10 / 10 = 1.0
    assert d["estimated_option_price"] == pytest.approx(1.0, abs=0.01)


def test_warrant_list_requires_no_auth():
    resp = client.get("/api/v1/warrants", params={"limit": 5})
    # Should not 401 — warrants are free tier like options. May be 200 or 502 if onvista down.
    assert resp.status_code in (200, 502, 503)
