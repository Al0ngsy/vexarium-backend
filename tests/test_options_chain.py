"""Tests for the option chain rework + chance-of-profit estimate.

Covers:
- AlpacaClient.get_option_chain (paginated market-data chain, indicative feed)
- OCC symbol parsing (_parse_occ_symbol)
- options_analyzer.prob_profit (Black-Scholes chance estimate)
- _parse_occ / _dte / _spread helpers in api.options
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services.alpaca_client import AlpacaClient
from app.services.options_analyzer import prob_profit
from app.api.options import _dte


# ---------------------------------------------------------------------------
# OCC parsing
# ---------------------------------------------------------------------------

def test_parse_occ_symbol_call():
    assert AlpacaClient._parse_occ_symbol("SPY260814C00752000") == (752.0, "2026-08-14", True)


def test_parse_occ_symbol_put():
    assert AlpacaClient._parse_occ_symbol("AAPL260814P00150000") == (150.0, "2026-08-14", False)


def test_parse_occ_symbol_invalid():
    assert AlpacaClient._parse_occ_symbol("SHORT") == (0.0, "", True)


# ---------------------------------------------------------------------------
# get_option_chain
# ---------------------------------------------------------------------------

def make_quote(bid, ask):
    return SimpleNamespace(bid_price=bid, ask_price=ask)


def make_greeks(delta=0.5):
    return SimpleNamespace(delta=delta, gamma=0.01, theta=-0.2, vega=0.3, rho=0.1)


def make_snapshot(sym, bid, ask, iv, delta=0.5):
    strike, expiry, is_call = AlpacaClient._parse_occ_symbol(sym)
    return SimpleNamespace(
        symbol=sym,
        greeks=make_greeks(delta=delta),
        implied_volatility=iv,
        latest_trade=SimpleNamespace(price=(bid + ask) / 2),
        latest_quote=make_quote(bid, ask),
    )


def make_client():
    with (
        patch("app.services.alpaca_client.StockHistoricalDataClient"),
        patch("app.services.alpaca_client.OptionHistoricalDataClient"),
        patch("app.services.alpaca_client.NewsClient"),
        patch("app.services.alpaca_client.TradingClient"),
    ):
        return AlpacaClient()


def test_get_option_chain_returns_enriched_rows():
    client = make_client()
    syms = ["SPY260814C00752000", "SPY260814P00752000"]
    snaps = {s: make_snapshot(s, bid=10.0, ask=10.5, iv=0.2) for s in syms}
    client._option.get_option_chain.return_value = snaps

    rows = client.get_option_chain("SPY", use_cache=False)

    assert len(rows) == 2
    row = next(r for r in rows if r["symbol"] == "SPY260814C00752000")
    assert row["strike_price"] == 752.0
    assert row["type"] == "call"
    assert row["expiration_date"] == "2026-08-14"
    assert row["bid"] == 10.0
    assert row["ask"] == 10.5
    assert row["implied_volatility"] == 0.2
    assert row["greeks"]["delta"] == 0.5


def test_get_option_chain_empty():
    client = make_client()
    client._option.get_option_chain.return_value = {}
    assert client.get_option_chain("SPY", use_cache=False) == []


def test_get_option_chain_sdk_error():
    client = make_client()
    client._option.get_option_chain.side_effect = Exception("boom")
    from app.services.exceptions import AlpacaError
    with pytest.raises(AlpacaError):
        client.get_option_chain("SPY", use_cache=False)


def test_get_option_chain_subscription_error():
    client = make_client()
    client._option.get_option_chain.side_effect = Exception("subscription required")
    from app.services.exceptions import SubscriptionRequiredError
    with pytest.raises(SubscriptionRequiredError):
        client.get_option_chain("SPY", use_cache=False)


# ---------------------------------------------------------------------------
# chance-of-profit estimate
# ---------------------------------------------------------------------------

def test_prob_profit_call_basic():
    r = prob_profit(
        strike=780, premium=5, current_price=771,
        days_to_expiry=30, implied_vol=0.15, is_call=True,
    )
    assert 0 <= r["prob_profit"] <= 1
    assert 0 <= r["prob_itm"] <= 1
    assert r["breakeven"] == 785


def test_prob_profit_put():
    r = prob_profit(
        strike=760, premium=5, current_price=771,
        days_to_expiry=30, implied_vol=0.15, is_call=False,
    )
    assert 0 <= r["prob_profit"] <= 1
    assert 0 <= r["prob_itm"] <= 1
    assert r["breakeven"] == 755


def test_prob_profit_invalid_inputs_returns_zeros():
    r = prob_profit(
        strike=0, premium=5, current_price=771,
        days_to_expiry=30, implied_vol=0.15, is_call=True,
    )
    assert r["prob_profit"] == 0.0
    assert r["prob_itm"] == 0.0


# ---------------------------------------------------------------------------
# api.options helpers
# ---------------------------------------------------------------------------

def test_dte():
    future = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")
    assert _dte(future) == 10


def test_dte_invalid():
    assert _dte("bad") == 0


def test_spread():
    assert AlpacaClient._spread_expiries(["a", "b", "c", "d", "e"], 3) == ["a", "c", "e"]
    assert AlpacaClient._spread_expiries(["a", "b"], 5) == ["a", "b"]
