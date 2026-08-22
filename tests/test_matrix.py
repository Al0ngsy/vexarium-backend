"""Tests for the OptionStrat-inspired options P/L matrix."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.options_analyzer import build_payoff_matrix

client = TestClient(app)


def test_matrix_structure():
    m = build_payoff_matrix(
        strike=550, premium=5.0, current_price=555.0,
        expiry_date="2026-09-01", implied_vol=0.2, is_call=True,
        expiries=["2026-09-01", "2026-08-15"], range_pct=0.05, quantity=100,
    )
    # 21 strikes centered ±5% around 555, 2 expiry columns.
    assert len(m["strikes"]) == 21
    assert len(m["strikes"][0]["cells"]) == 2
    # Breakeven for a 550 call bought at 5 = 555.
    assert m["breakeven"] == 555.0
    # Strike ladder is centered: first is -5%, last is +5%.
    assert m["strikes"][0]["move_pct"] == pytest.approx(-5.0, abs=0.1)
    assert m["strikes"][-1]["move_pct"] == pytest.approx(5.0, abs=0.1)


def test_matrix_higher_strike_has_more_pl_for_call():
    m = build_payoff_matrix(
        strike=550, premium=5.0, current_price=555.0,
        expiry_date="2026-09-01", implied_vol=0.2, is_call=True,
        expiries=["2026-09-01"], range_pct=0.05, quantity=100,
    )
    top = m["strikes"][-1]["cells"][0]["pl"]
    bottom = m["strikes"][0]["cells"][0]["pl"]
    # For a call, a higher strike row (price above current) → higher P/L.
    assert top > bottom


def test_matrix_date_columns_capped_at_expiry():
    """Columns never extend past the contract's expiry and the count is clamped."""
    from datetime import date, timedelta
    from app.api.options import _matrix_date_columns

    exp = (date.today() + timedelta(days=10)).isoformat()
    cols = _matrix_date_columns(exp, 12)
    # dte=10 → clamped to 11 columns (today..expiry), last one exactly expiry.
    assert cols[-1] == exp
    assert all(c <= exp for c in cols)
    assert len(cols) == 11
    # Requesting more than 24 clamps to 24; requesting 1 still yields >= 2.
    assert len(_matrix_date_columns(exp, 99)) == 11
    assert len(_matrix_date_columns(exp, 1)) == 2
    # Already-expired contract: single column (today == expiry).
    past = (date.today() - timedelta(days=1)).isoformat()
    assert len(_matrix_date_columns(past, 8)) == 1
