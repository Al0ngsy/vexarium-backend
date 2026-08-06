"""Tests for the free, keyless company/ETF profile service.

All external HTTP calls are mocked — no real network calls in tests.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.services import company_info
from app.services.company_info import get_company_info, _wikipedia_title, _clean_description


def _resp(payload: dict):
    r = SimpleNamespace(status_code=200, json=lambda: payload)
    r.raise_for_status = lambda: None
    r.text = ""
    return r


def _fake_quote_summary(fundamentals: dict | None):
    """A fake that serves the crumb + quoteSummary + v8 + wikipedia calls."""
    def _fake_get(self, url, **kw):
        if "test/getcrumb" in url:
            r = SimpleNamespace(status_code=200)
            r.raise_for_status = lambda: None
            r.text = "some-crumb"
            return r
        if "quoteSummary" in url:
            return _resp({"quoteSummary": {"result": [fundamentals or {}]}})
        if "finance/chart" in url:
            return _resp({
                "chart": {"result": [{"meta": {
                    "longName": "Apple Inc.", "shortName": "Apple",
                    "fullExchangeName": "NasdaqGS", "fiftyTwoWeekHigh": 300.5,
                    "fiftyTwoWeekLow": 120.25, "currency": "USD",
                }}]}
            })
        # wikipedia
        return _resp({"extract": "Apple Inc. is an American multinational technology company."})
    return patch.object(company_info.httpx.Client, "get", _fake_get)


def _make_fundamentals() -> dict:
    return {
        "assetProfile": {
            "sector": "Technology", "industry": "Consumer Electronics",
            "website": "https://www.apple.com", "address1": "One Apple Park Way",
            "city": "Cupertino", "state": "CA", "zip": "95014",
            "fullTimeEmployees": 150000,
            "companyOfficers": [{"name": "Tim Cook", "title": "CEO", "totalPay": {"raw": 99000000}}],
        },
        "price": {
            "longName": "Apple Inc.", "exchangeName": "NasdaqGS", "currency": "USD",
            "marketCap": {"raw": 3000000000000},
        },
        "summaryDetail": {
            "trailingPE": {"raw": 32.5}, "forwardPE": {"raw": 28.0},
            "priceToSalesTrailing12Months": {"raw": 8.2}, "priceToBook": {"raw": 45.0},
            "dividendYield": {"raw": 0.0047}, "payoutRatio": {"raw": 0.15},
        },
        "financialData": {
            "revenueGrowth": {"raw": 0.10}, "profitMargins": {"raw": 0.25},
            "grossMargins": {"raw": 0.44}, "returnOnEquity": {"raw": 1.47},
            "returnOnAssets": {"raw": 0.20},
        },
        "defaultKeyStatistics": {
            "sharesOutstanding": {"raw": 15000000000}, "earningsQuarterlyGrowth": {"raw": 0.12},
        },
        "calendarEvents": {"earnings": {"earningsDate": [{"raw": 1787774400, "fmt": "2026-08-26"}]}},
    }


def test_returns_full_fundamentals():
    with _fake_quote_summary(_make_fundamentals()):
        info = get_company_info("AAPL")
    assert info["symbol"] == "AAPL"
    assert info["name"] == "Apple Inc."
    assert info["exchange"] == "NasdaqGS"
    assert info["currency"] == "USD"
    assert info["sector"] == "Technology"
    assert info["industry"] == "Consumer Electronics"
    assert info["employees"] == 150000
    assert info["ceo"] == "Tim Cook"
    assert info["ceo_pay"] == 99000000
    assert info["market_cap"] == 3000000000000
    assert info["pe_ratio"] == 32.5
    assert info["forward_pe"] == 28.0
    assert info["ps_ratio"] == 8.2
    assert info["pb_ratio"] == 45.0
    assert info["dividend_yield"] == 0.0047
    assert info["payout_ratio"] == 0.15
    assert info["revenue_growth"] == 0.10
    assert info["earnings_growth"] == 0.12
    assert info["profit_margin"] == 0.25
    assert info["gross_margin"] == 0.44
    assert info["roe"] == 1.47
    assert info["roa"] == 0.20
    assert info["high_52w"] == 300.5
    assert info["low_52w"] == 120.25
    assert info["next_earnings_date"] == "2026-08-26"
    assert info["headquarters"] == "One Apple Park Way, Cupertino, CA, 95014"
    assert "American multinational" in (info.get("description") or "")


def test_graceful_fallback_on_total_failure():
    def _fake_get(self, url, **kw):
        raise RuntimeError("network down")
    with patch.object(company_info.httpx.Client, "get", _fake_get):
        info = get_company_info("AAPL")
    assert info["symbol"] == "AAPL"
    assert info.get("name") is None


def test_graceful_fallback_when_summary_missing():
    # quoteSummary works but returns no result -> name/values absent, no crash.
    def _fake_get(self, url, **kw):
        if "test/getcrumb" in url:
            r = SimpleNamespace(status_code=200)
            r.raise_for_status = lambda: None
            r.text = "crumb"
            return r
        if "quoteSummary" in url:
            return _resp({"quoteSummary": {"result": [{}]}})
        return _resp({"chart": {"result": [{"meta": {}}]}})
    with patch.object(company_info.httpx.Client, "get", _fake_get):
        info = get_company_info("AAPL")
    assert info["symbol"] == "AAPL"
    assert info.get("pe_ratio") is None


def test_wikipedia_title_keeps_legal_suffix():
    assert _wikipedia_title("Apple Inc.") == "Apple_Inc."
    assert _wikipedia_title("NVIDIA Corporation") == "NVIDIA_Corporation"


def test_wikipedia_title_curated_etf():
    assert _wikipedia_title("State Street SPDR S&P 500 ETF Trust", "SPY") == "SPDR_S%26P_500_ETF_Trust"


def test_clean_description_trims_long_text():
    assert len(_clean_description("X. " * 200)) <= 320


def test_clean_description_empty():
    assert _clean_description("") == ""
