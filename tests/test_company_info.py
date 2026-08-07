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


def test_stockanalysis_fallback_when_quote_summary_fails():
    """When Yahoo quoteSummary is blocked, stockanalysis.com fills the gaps."""
    sa_html = (
        '<a href="/stocks/aapl/market-cap/" class="dothref text-default">Market Cap</a>'
        '<!--]--></td><td class="x">4.54T <!----><span class="rg">+51.1%</span>'
        '<a href="/stocks/aapl/revenue/" class="dothref text-default">Revenue (ttm)</a>'
        '<!--]--></td><td class="x">466.82B'
        '<td class="whitespace-nowrap py-[1px] px-0.5 xs:px-1 sm:py-2">52-Week Range</td>'
        '<td class="whitespace-nowrap py-[1px] px-0.5 text-left x">202.16 - 344.57</td><'
    )
    sa_stats_html = (
        '<span><!---->PE Ratio<!----></span>'
        '<td class="x" title="35.677">35.68</td>'
        '<span><!---->Profit Margin<!----></span>'
        '<td class="x" title="27.619">27.62%</td>'
        '<span><!---->Return on Equity<!----></span>'
        '<td class="x" title="148.751">148.75%</td>'
    )

    def _fake_get(self, url, **kw):
        if "test/getcrumb" in url or "quoteSummary" in url:
            raise RuntimeError("Yahoo blocked")
        if "statistics" in url:
            r = SimpleNamespace(status_code=200, text=sa_stats_html)
            r.raise_for_status = lambda: None
            return r
        if "stockanalysis.com" in url:
            r = SimpleNamespace(status_code=200, text=sa_html)
            r.raise_for_status = lambda: None
            return r
        return _resp({"chart": {"result": [{"meta": {}}]}})

    with patch.object(company_info.httpx.Client, "get", _fake_get):
        info = get_company_info("AAPL")
    assert info["symbol"] == "AAPL"
    assert info["market_cap"] == 4.54e12
    assert info["pe_ratio"] == 35.68  # rounded to 2dp by num()
    assert info["profit_margin"] == 0.2762  # rounded to 4dp by pct()
    assert info["roe"] == 1.4875
    assert info["high_52w"] == 344.57
    assert info["low_52w"] == 202.16


def test_wikipedia_title_keeps_legal_suffix():
    assert _wikipedia_title("Apple Inc.") == "Apple_Inc."
    assert _wikipedia_title("NVIDIA Corporation") == "NVIDIA_Corporation"


def test_wikipedia_title_curated_etf():
    assert _wikipedia_title("State Street SPDR S&P 500 ETF Trust", "SPY") == "SPDR_S%26P_500_ETF_Trust"


def test_clean_description_trims_long_text():
    assert len(_clean_description("X. " * 200)) <= 320


def test_clean_description_empty():
    assert _clean_description("") == ""


# ---------------------------------------------------------------------------
# OTC ADR -> main listing resolution
# ---------------------------------------------------------------------------

def _make_adr_fundamentals(exchange: str = "OTC Markets OTCPK") -> dict:
    f = _make_fundamentals()
    f["price"] = {
        **f["price"],
        "longName": "Rheinmetall AG",
        "exchangeName": exchange,
    }
    return f


def _fake_search_response(quotes: list[dict]):
    """Fake httpx.get serving the Yahoo /v1/finance/search endpoint."""

    def _fake_get(self, url, **kw):
        if "test/getcrumb" in url:
            r = SimpleNamespace(status_code=200)
            r.raise_for_status = lambda: None
            r.text = "some-crumb"
            return r
        if "quoteSummary" in url:
            return _resp({"quoteSummary": {"result": [_make_adr_fundamentals()]}})
        if "finance/search" in url:
            return _resp({"quotes": quotes})
        if "finance/chart" in url:
            return _resp({
                "chart": {"result": [{"meta": {
                    "longName": "Rheinmetall AG", "shortName": "Rheinmetall",
                    "fullExchangeName": "OTC Markets OTCPK",
                    "fiftyTwoWeekHigh": 500.0, "fiftyTwoWeekLow": 200.0,
                    "currency": "USD",
                }}]}
            })
        return _resp({"extract": "Rheinmetall AG is a German defense company."})

    return patch.object(company_info.httpx.Client, "get", _fake_get)


def test_otc_adr_resolves_main_listing():
    """RNMBY (OTC ADR) -> primary listing RHM.DE on XETRA via Yahoo search."""
    quotes = [
        {"symbol": "RHM.DE", "longname": "Rheinmetall AG", "shortname": "RHEINMETALL AG",
         "exchDisp": "XETRA", "exchange": "GER", "quoteType": "EQUITY"},
        {"symbol": "RHM.F", "longname": "Rheinmetall AG", "shortname": "RHEINMETALL AG",
         "exchDisp": "Frankfurt", "exchange": "FRA", "quoteType": "EQUITY"},
        {"symbol": "RNMBY", "longname": "Rheinmetall AG", "shortname": "Rheinmetall AG",
         "exchDisp": "OTC Markets", "exchange": "OQX", "quoteType": "EQUITY"},
    ]
    with _fake_search_response(quotes):
        info = get_company_info("RNMBY")
    ml = info.get("main_listing")
    assert ml is not None
    assert ml["symbol"] == "RHM.DE"  # XETRA/GER preferred over Frankfurt
    assert ml["exchange"] == "XETRA"


def test_non_otc_has_no_main_listing():
    with _fake_quote_summary(_make_fundamentals()):
        info = get_company_info("AAPL")
    assert info.get("main_listing") is None


def test_find_main_listing_none_when_no_search_results():
    with patch.object(company_info, "_yahoo_search_quotes", return_value=[]):
        assert company_info.find_main_listing("RNMBY", "Rheinmetall AG") is None


def test_find_main_listing_skips_other_companies():
    """Same company name is required — other tickers are ignored."""
    quotes = [
        {"symbol": "SIE.DE", "longname": "Siemens AG", "shortname": "Siemens AG",
         "exchDisp": "XETRA", "exchange": "GER", "quoteType": "EQUITY"},
        {"symbol": "RHM.DE", "longname": "Rheinmetall AG", "shortname": "RHEINMETALL AG",
         "exchDisp": "XETRA", "exchange": "GER", "quoteType": "EQUITY"},
    ]
    with patch.object(company_info, "_yahoo_search_quotes", return_value=quotes):
        ml = company_info.find_main_listing("RNMBY", "Rheinmetall AG")
    assert ml is not None
    assert ml["symbol"] == "RHM.DE"
