"""Tests for the free, keyless company/ETF profile service.

All external HTTP calls are mocked — no real network calls in tests.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.services import company_info
from app.services.company_info import get_company_info, _wikipedia_title, _clean_description


def _mock_yahoo(meta=None, exc=None):
    """Patch httpx.Client so the Yahoo v8 chart call returns `meta` or raises."""
    def _fake_get(self, url, **kw):
        if exc:
            raise exc
        payload = {"chart": {"result": [{"meta": meta or {"longName": "Apple Inc.", "symbol": "AAPL"}}]}}
        resp = SimpleNamespace(
            status_code=200,
            json=lambda: payload,
        )
        resp.raise_for_status = lambda: None
        return resp
    return patch.object(company_info.httpx.Client, "get", _fake_get)


def test_returns_name_exchange_and_52w():
    meta = {
        "longName": "Apple Inc.",
        "shortName": "Apple",
        "fullExchangeName": "NasdaqGS",
        "fiftyTwoWeekHigh": 300.5,
        "fiftyTwoWeekLow": 120.25,
        "currency": "USD",
    }
    with _mock_yahoo(meta=meta):
        info = get_company_info("AAPL")
    assert info["symbol"] == "AAPL"
    assert info["name"] == "Apple Inc."
    assert info["exchange"] == "NasdaqGS"
    assert info["high_52w"] == 300.5
    assert info["low_52w"] == 120.25
    assert info["currency"] == "USD"


def test_graceful_fallback_on_yahoo_failure():
    with _mock_yahoo(exc=RuntimeError("network down")):
        info = get_company_info("AAPL")
    # Never raises; at minimum the symbol is present, name/description absent.
    assert info["symbol"] == "AAPL"
    assert info.get("name") is None
    assert info.get("description") is None


def test_wikipedia_title_keeps_legal_suffix():
    assert _wikipedia_title("Apple Inc.") == "Apple_Inc."
    assert _wikipedia_title("NVIDIA Corporation") == "NVIDIA_Corporation"


def test_wikipedia_title_curated_etf():
    assert _wikipedia_title("State Street SPDR S&P 500 ETF Trust", "SPY") == "SPDR_S%26P_500_ETF_Trust"
    assert _wikipedia_title("State Street SPDR S&P 500 ETF Trust", "SPY") != ""


def test_clean_description_trims_long_text():
    long_text = "X. " * 200
    cleaned = _clean_description(long_text)
    assert len(cleaned) <= 320


def test_clean_description_empty():
    assert _clean_description("") == ""
