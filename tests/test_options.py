import pytest
from app.api.options import _parse_occ
from app.services.options_analyzer import compute_payoff, compute_breakeven, build_payoff_timeline

def test_parse_occ_call():
    assert _parse_occ("SPY250919C00750000") == (750.0, "2025-09-19", True)

def test_parse_occ_put():
    assert _parse_occ("SPY250919P00750000") == (750.0, "2025-09-19", False)

def test_call_payoff_in_the_money():
    result = compute_payoff(strike=100, premium=5, current_price=110, is_call=True)
    assert result["intrinsic_value"] == 10
    assert result["pl"] == 5
    assert result["pl_pct"] == 1.0

def test_call_payoff_out_of_the_money():
    result = compute_payoff(strike=100, premium=5, current_price=95, is_call=True)
    assert result["intrinsic_value"] == 0
    assert result["pl"] == -5
    assert result["pl_pct"] == -1.0

def test_put_payoff_in_the_money():
    result = compute_payoff(strike=100, premium=5, current_price=90, is_call=False)
    assert result["intrinsic_value"] == 10
    assert result["pl"] == 5

def test_put_payoff_out_of_the_money():
    result = compute_payoff(strike=100, premium=5, current_price=105, is_call=False)
    assert result["intrinsic_value"] == 0
    assert result["pl"] == -5

def test_breakeven_call():
    assert compute_breakeven(strike=100, premium=5, is_call=True) == 105

def test_breakeven_put():
    assert compute_breakeven(strike=100, premium=5, is_call=False) == 95

def test_payoff_timeline():
    from datetime import date, timedelta
    expiry = (date.today() + timedelta(days=10)).isoformat()
    timeline = build_payoff_timeline(strike=100, premium=5, current_price=105, expiry_date=expiry, theta_per_day=0.5, is_call=True)
    assert len(timeline) == 11
    assert timeline[0]["day"] == 0
    assert timeline[0]["estimated_option_price"] == 5.0
    assert timeline[10]["day"] == 10
    assert timeline[10]["estimated_option_price"] == 0.0

def test_payoff_timeline_past_expiry():
    from datetime import date, timedelta
    expiry = (date.today() - timedelta(days=5)).isoformat()
    timeline = build_payoff_timeline(strike=100, premium=5, current_price=105, expiry_date=expiry, theta_per_day=0.5, is_call=True)
    assert timeline == []

def test_payoff_timeline_invalid_date():
    timeline = build_payoff_timeline(strike=100, premium=5, current_price=105, expiry_date="bad", theta_per_day=0.5, is_call=True)
    assert timeline == []
