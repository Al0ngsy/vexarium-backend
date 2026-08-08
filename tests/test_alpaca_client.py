"""Tests for the Alpaca data client wrapper.

All SDK clients are mocked — no real API calls are made.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

from app.services.alpaca_client import AlpacaClient, _fetch_yahoo_bars
from app.services.exceptions import AlpacaError, SymbolNotFoundError


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def make_bar(open_, high, low, close, volume, ts):
    return SimpleNamespace(
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        timestamp=ts,
    )


def make_quote(bid, ask, ts):
    return SimpleNamespace(bid_price=bid, ask_price=ask, timestamp=ts)


def make_greeks(delta=0.5, gamma=0.01, theta=-0.2, vega=0.3, rho=0.1):
    return SimpleNamespace(
        delta=delta,
        gamma=gamma,
        theta=theta,
        vega=vega,
        rho=rho,
    )


def make_trade(price, ts):
    return SimpleNamespace(price=price, timestamp=ts)


def make_snapshot(greeks=None, iv=0.25, trade=None, quote=None):
    return SimpleNamespace(
        greeks=greeks or make_greeks(),
        implied_volatility=iv,
        latest_trade=trade or make_trade(105.0, datetime.now(timezone.utc)),
        latest_quote=quote or make_quote(104.9, 105.1, datetime.now(timezone.utc)),
    )


# ---------------------------------------------------------------------------
# Fixture: AlpacaClient with all SDK constructors patched at module level
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    """Build an AlpacaClient with all SDK constructors patched out."""
    with (
        patch("app.services.alpaca_client.StockHistoricalDataClient"),
        patch("app.services.alpaca_client.OptionHistoricalDataClient"),
        patch("app.services.alpaca_client.NewsClient"),
        patch("app.services.alpaca_client.TradingClient"),
    ):
        # The patched constructors return Mock instances, which get assigned
        # to c._stock / c._option / c._news / c._trading inside __init__.
        c = AlpacaClient()
        return c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStockBars:
    def test_returns_dataframe_with_expected_columns(self, client):
        ts = datetime.now(timezone.utc)
        bars = [make_bar(100.0, 101.0, 99.0, 100.5, 1000, ts)]
        resp = SimpleNamespace(data={"AAPL": bars})
        client._stock.get_stock_bars.return_value = resp

        df = client.get_stock_bars("AAPL")

        assert isinstance(df, pd.DataFrame)
        assert list(df.columns) == [
            "open", "high", "low", "close", "volume", "timestamp"
        ]
        assert len(df) == 1
        assert df.iloc[0]["close"] == 100.5
        assert df.iloc[0]["volume"] == 1000.0

    def test_empty_data_returns_empty_dataframe(self, client):
        resp = SimpleNamespace(data={"AAPL": []})
        client._stock.get_stock_bars.return_value = resp

        with patch(
            "app.services.alpaca_client._fetch_yahoo_bars",
            return_value=pd.DataFrame(columns=["open", "high", "low", "close", "volume", "timestamp"]),
        ):
            df = client.get_stock_bars("AAPL")

        assert isinstance(df, pd.DataFrame)
        assert df.empty
        assert list(df.columns) == [
            "open", "high", "low", "close", "volume", "timestamp"
        ]

    def test_sdk_error_raises_alpaca_error(self, client):
        client._stock.get_stock_bars.side_effect = Exception("boom")

        with pytest.raises(AlpacaError):
            client.get_stock_bars("AAPL")

    def test_not_found_raises_symbol_not_found(self, client):
        client._stock.get_stock_bars.side_effect = Exception(
            "symbol not found"
        )

        with patch(
            "app.services.alpaca_client._fetch_yahoo_bars",
            return_value=pd.DataFrame(columns=["open", "high", "low", "close", "volume", "timestamp"]),
        ):
            with pytest.raises(SymbolNotFoundError):
                client.get_stock_bars("BAD")

    def test_empty_alpaca_falls_back_to_yahoo(self, client):
        """OTC ADRs (SMERY, RNMBY) return no Alpaca bars -> Yahoo fills in."""
        resp = SimpleNamespace(data={"SMERY": []})
        client._stock.get_stock_bars.return_value = resp
        yahoo_df = pd.DataFrame({
            "open": [10.0, 10.5], "high": [11.0, 11.5], "low": [9.5, 10.0],
            "close": [10.25, 10.75], "volume": [1000.0, 2000.0],
            "timestamp": pd.to_datetime(["2025-01-02", "2025-01-03"]),
        })

        with patch(
            "app.services.alpaca_client._fetch_yahoo_bars",
            return_value=yahoo_df,
        ) as mock_yahoo:
            df = client.get_stock_bars("SMERY")

        mock_yahoo.assert_called_once_with("SMERY", 365, interval="1d")
        assert len(df) == 2
        assert df.iloc[-1]["close"] == 10.75

    def test_not_found_falls_back_to_yahoo(self, client):
        """Alpaca raising 'not found' still tries Yahoo before SymbolNotFound."""
        client._stock.get_stock_bars.side_effect = Exception(
            "symbol not found"
        )
        yahoo_df = pd.DataFrame({
            "open": [10.0], "high": [11.0], "low": [9.5], "close": [10.25],
            "volume": [1000.0], "timestamp": pd.to_datetime(["2025-01-02"]),
        })

        with patch(
            "app.services.alpaca_client._fetch_yahoo_bars",
            return_value=yahoo_df,
        ) as mock_yahoo:
            df = client.get_stock_bars("RNMBY")

        mock_yahoo.assert_called_once()
        assert len(df) == 1
        assert df.iloc[0]["close"] == 10.25


class TestYahooBarsParser:
    """Unit tests for _fetch_yahoo_bars (httpx mocked — no network)."""

    @staticmethod
    def _chart_payload():
        # Two trading days + one null-close (illiquid) day, as Yahoo returns.
        return {
            "chart": {
                "result": [{
                    "timestamp": [1704153600, 1704240000, 1704326400],
                    "indicators": {"quote": [{
                        "open": [10.0, 10.5, None],
                        "high": [10.8, 11.0, None],
                        "low": [9.9, 10.2, None],
                        "close": [10.5, 10.9, None],
                        "volume": [1000, 2000, None],
                    }]},
                }],
            }
        }

    @staticmethod
    def _cm_client(get_fn):
        """Wrap a get() callable in a context-manager client."""

        class _CM:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def get(self, url):
                return get_fn(url)

        return _CM()

    def test_parses_ohlcv_and_skips_null_close_days(self):
        payload = self._chart_payload()
        fake_resp = SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)
        fake_client = self._cm_client(lambda url: fake_resp)

        with patch("app.services.alpaca_client.httpx.Client", return_value=fake_client) as mock_cls:
            df = _fetch_yahoo_bars("SMERY")

        mock_cls.assert_called_once()
        assert len(df) == 2  # null-close day dropped
        assert list(df.columns) == [
            "open", "high", "low", "close", "volume", "timestamp"
        ]
        assert df.iloc[0]["close"] == 10.5
        assert df.iloc[1]["close"] == 10.9
        assert df.iloc[1]["volume"] == 2000.0
        # Unix seconds -> tz-aware datetime (matches Alpaca bar timestamps).
        assert df.iloc[0]["timestamp"] == pd.Timestamp("2024-01-02", tz="UTC")

    def test_tries_query2_after_query1_failure(self):
        payload = self._chart_payload()
        fail_resp = SimpleNamespace(
            raise_for_status=lambda: (_ for _ in ()).throw(Exception("429")),
            json=lambda: {},
        )
        ok_resp = SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)
        calls = {"n": 0}

        def fake_get(url):
            calls["n"] += 1
            if "query1" in url:
                return fail_resp
            return ok_resp

        fake_client = self._cm_client(fake_get)
        with patch("app.services.alpaca_client.httpx.Client", return_value=fake_client):
            df = _fetch_yahoo_bars("RNMBY")

        assert calls["n"] == 2  # query1 failed, query2 succeeded
        assert len(df) == 2

    def test_total_failure_returns_empty_dataframe(self):
        fail_resp = SimpleNamespace(
            raise_for_status=lambda: (_ for _ in ()).throw(Exception("boom")),
            json=lambda: {},
        )
        fake_client = self._cm_client(lambda url: fail_resp)
        with patch("app.services.alpaca_client.httpx.Client", return_value=fake_client):
            df = _fetch_yahoo_bars("ZZZZ")

        assert isinstance(df, pd.DataFrame)
        assert df.empty


class TestLatestQuote:
    def test_returns_quote_dict(self, client):
        quote = make_quote(104.9, 105.1, datetime.now(timezone.utc))
        client._stock.get_stock_latest_quote.return_value = {"AAPL": quote}

        result = client.get_latest_quote("AAPL")

        assert result["bid"] == 104.9
        assert result["ask"] == 105.1
        assert result["last_price"] == 104.9
        assert "timestamp" in result

    def test_empty_quote_returns_empty_dict(self, client):
        client._stock.get_stock_latest_quote.return_value = {"AAPL": None}

        assert client.get_latest_quote("AAPL") == {}

    def test_sdk_error_raises_alpaca_error(self, client):
        client._stock.get_stock_latest_quote.side_effect = Exception("boom")

        with pytest.raises(AlpacaError):
            client.get_latest_quote("AAPL")


class TestOptionContracts:
    def test_returns_list(self, client):
        contract = SimpleNamespace(
            symbol="AAPL251219C00200000",
            expiration_date="2025-12-19",
            model_dump=lambda: {"symbol": "AAPL251219C00200000", "expiration_date": "2025-12-19"},
        )
        resp = SimpleNamespace(option_contracts=[contract], next_page_token=None)
        client._trading.get_option_contracts.return_value = resp

        result = client.get_option_contracts(
            "AAPL", "2025-12-19", "2025-12-19"
        )

        assert isinstance(result, list)
        # The two-phase fetch discovers the expiry then fetches CALL + PUT, so the
        # same mocked contract appears once per type (2 total).
        assert len(result) == 2
        assert all(r["symbol"] == "AAPL251219C00200000" for r in result)

    def test_empty_returns_empty_list(self, client):
        resp = SimpleNamespace(option_contracts=[], next_page_token=None)
        client._trading.get_option_contracts.return_value = resp

        result = client.get_option_contracts("AAPL", "2025-12-19", "2025-12-19")

        assert result == []

    def test_sdk_error_raises_alpaca_error(self, client):
        client._trading.get_option_contracts.side_effect = Exception("boom")

        with pytest.raises(AlpacaError):
            client.get_option_contracts("AAPL", "2025-12-19", "2025-12-19")


class TestOptionSnapshot:
    def test_returns_dict_with_greeks(self, client):
        snap = make_snapshot()
        client._option.get_option_snapshot.return_value = {"AAPL251219C00200000": snap}

        result = client.get_option_snapshot("AAPL251219C00200000")

        assert isinstance(result, dict)
        assert set(result["greeks"].keys()) == {
            "delta", "gamma", "theta", "vega", "rho"
        }
        assert result["greeks"]["delta"] == 0.5
        assert result["implied_volatility"] == 0.25
        assert result["bid"] == 104.9
        assert result["ask"] == 105.1

    def test_empty_returns_empty_dict(self, client):
        client._option.get_option_snapshot.return_value = {"X": None}

        assert client.get_option_snapshot("X") == {}

    def test_sdk_error_raises_alpaca_error(self, client):
        client._option.get_option_snapshot.side_effect = Exception("boom")

        with pytest.raises(AlpacaError):
            client.get_option_snapshot("X")


class TestNews:
    def test_returns_list(self, client, monkeypatch):
        article = SimpleNamespace(
            id=1,
            model_dump=lambda: {"headline": "Hello"},
        )
        client._news.get_news.return_value = SimpleNamespace(data={"news": [article]})
        monkeypatch.setattr(client, "_fetch_google_news", lambda *a, **k: [])

        result = client.get_news("AAPL")

        assert isinstance(result, list)
        assert result == [{"headline": "Hello"}]

    def test_empty_returns_empty_list(self, client, monkeypatch):
        client._news.get_news.return_value = SimpleNamespace(data=[])
        monkeypatch.setattr(client, "_fetch_google_news", lambda *a, **k: [])

        assert client.get_news("AAPL") == []

    def test_sdk_error_raises_alpaca_error(self, client, monkeypatch):
        client._news.get_news.side_effect = Exception("boom")
        monkeypatch.setattr(client, "_fetch_google_news", lambda *a, **k: [])

        with pytest.raises(AlpacaError):
            client.get_news("AAPL")

    def test_merges_google_news_sources(self, client, monkeypatch):
        """Alpaca (Benzinga) + Google News RSS are interleaved so the feed
        shows multiple outlets, not just Benzinga."""
        article = SimpleNamespace(
            id=1,
            model_dump=lambda: {"headline": "AAPL beats", "source": "benzinga"},
        )
        client._news.get_news.return_value = SimpleNamespace(data={"news": [article]})
        monkeypatch.setattr(
            client,
            "_fetch_google_news",
            lambda *a, **k: [
                {"headline": "AAPL rally", "source": "Reuters"},
                {"headline": "AAPL analysis", "source": "Seeking Alpha"},
            ],
        )

        result = client.get_news("AAPL")

        sources = [a.get("source") for a in result]
        assert "benzinga" in sources
        assert "Reuters" in sources
        assert "Seeking Alpha" in sources
        assert len(result) == 3

    def test_google_news_fallback_on_alpaca_error(self, client, monkeypatch):
        """If Alpaca news is down, Google News RSS alone still serves."""
        client._news.get_news.side_effect = Exception("boom")
        monkeypatch.setattr(
            client,
            "_fetch_google_news",
            lambda *a, **k: [{"headline": "AAPL news", "source": "Reuters"}],
        )

        result = client.get_news("AAPL")

        assert result == [{"headline": "AAPL news", "source": "Reuters"}]
