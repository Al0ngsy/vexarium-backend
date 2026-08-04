"""Tests for Black-Scholes option value at price (payoff explorer)."""
from __future__ import annotations

from app.services.options_analyzer import black_scholes_price, option_value_at_price


def test_call_at_expiry_is_intrinsic():
    # At expiry (0 days), a call is worth max(S - K, 0).
    v = black_scholes_price(strike=550, price=560, days_to_expiry=0,
                            implied_vol=0.2, is_call=True)
    assert abs(v - 10.0) < 0.01


def test_put_at_expiry_is_intrinsic():
    v = black_scholes_price(strike=550, price=540, days_to_expiry=0,
                            implied_vol=0.2, is_call=False)
    assert abs(v - 10.0) < 0.01


def test_call_below_strike_has_time_value():
    # ITM call (S > K) with time left must be worth more than intrinsic.
    v = black_scholes_price(strike=550, price=560, days_to_expiry=30,
                            implied_vol=0.3, is_call=True)
    assert v > 10.0  # intrinsic is 10


def test_option_value_at_price_pl():
    r = option_value_at_price(
        strike=550, premium=7.90, current_price=555,
        expiry_date="2026-08-28", implied_vol=0.2, is_call=True,
        target_price=560, target_date=None,
    )
    assert r["target_price"] == 560
    assert r["estimated_option_price"] >= 0
    # At expiry the value should exceed premium (ITM).
    assert r["estimated_pl"] == round(r["estimated_option_price"] - 7.90, 2)
