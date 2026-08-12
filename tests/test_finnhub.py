"""Tests for the Finnhub enrichment service (insider, earnings, peers)."""

from unittest.mock import patch

from app.services import finnhub
from app.services.finnhub import get_finnhub_bundle, get_insider_transactions


def _fake_get(payload):
    return patch(
        "app.services.finnhub.httpx.get",
        return_value=__import__("types").SimpleNamespace(
            raise_for_status=lambda: None, json=lambda: payload
        ),
    )


def _enable_key():
    return patch.object(finnhub.settings, "finnhub_api_key", "test-key")


INSIDER = {
    "data": [
        {"name": "COOK TIMOTHY D", "share": 5000, "change": -2500, "filingDate": "2026-07-01"},
        {"name": "MAESTRI LUCA", "share": 12000, "change": 12000, "filingDate": "2026-06-15"},
    ]
}


def test_insider_transactions_parsed():
    with _enable_key(), _fake_get(INSIDER):
        rows = get_insider_transactions("AAPL")
    assert len(rows) == 2
    assert rows[0]["name"] == "COOK TIMOTHY D"
    assert rows[0]["change"] == -2500
    assert rows[0]["filing_date"] == "2026-07-01"


def test_earnings_parsed():
    payload = [
        {"period": "2026-06-30", "estimate": 1.92, "actual": 1.91, "surprisePercent": -0.88},
        {"period": "2026-03-31", "estimate": 1.5, "actual": 1.7, "surprisePercent": 13.3},
    ]
    with _enable_key(), _fake_get(payload):
        rows = finnhub.get_earnings_history("AAPL")
    assert len(rows) == 2
    assert rows[1]["surprise_pct"] == 13.3


def test_peers_parsed():
    with _enable_key(), _fake_get(["AAPL", "MSFT", "", 42]):
        peers = finnhub.get_peers("AAPL")
    assert peers == ["AAPL", "MSFT"]


def test_empty_when_key_missing():
    with patch.object(finnhub.settings, "finnhub_api_key", ""):
        bundle = get_finnhub_bundle("AAPL")
    assert bundle == {"insider": [], "earnings": [], "peers": []}


def test_bundle_fetches_and_caches_each_kind():
    import types

    def responder(url, **kw):
        if "insider-transactions" in url:
            payload = INSIDER
        elif "earnings" in url:
            payload = [{"period": "2026-06-30", "estimate": 1.9, "actual": 1.91, "surprisePercent": 0.5}]
        else:
            payload = ["MSFT"]
        return types.SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)

    with _enable_key(), patch("app.services.finnhub.httpx.get", side_effect=responder) as mock_get:
        b1 = get_finnhub_bundle("AAPL")
        b2 = get_finnhub_bundle("AAPL")
    assert b1["insider"] and b1["earnings"] and b1["peers"] == ["MSFT"]
    # All three kinds cached: each fetched exactly once across both calls.
    assert mock_get.call_count == 3
