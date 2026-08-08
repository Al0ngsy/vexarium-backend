"""Timeframe support: TIMEFRAMES mapping + get_stock_bars(timeframe=...)."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app.services.alpaca_client import TIMEFRAMES, AlpacaClient
from app.services.cache import bars_key


def test_bars_key_includes_timeframe():
    assert bars_key("AAPL", "1d") == "bars:AAPL:1d"
    assert bars_key("AAPL", "15m") == "bars:AAPL:15m"
    assert bars_key("AAPL") == "bars:AAPL:1d"  # backward compatible


def test_timestamps_map_has_all_resolutions():
    assert set(TIMEFRAMES) == {"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w", "1mo"}
    # (mult, unit, max_days, yahoo_interval) — each resolution has a Yahoo interval
    for key, (mult, unit, days, yahoo) in TIMEFRAMES.items():
        assert mult >= 1
        assert days > 0
        assert yahoo


@patch("app.services.alpaca_client.run_coro", return_value=None)
def test_get_stock_bars_unknown_timeframe_rejected(mock_coro):
    client = AlpacaClient.__new__(AlpacaClient)
    client._stock = MagicMock()
    with pytest.raises(ValueError, match="unsupported timeframe"):
        client.get_stock_bars("AAPL", timeframe="2h")


@patch("app.services.alpaca_client.run_coro", return_value=None)
def test_get_stock_bars_passes_timeframe_to_alpaca(mock_coro):
    """1h bars -> TimeFrame(1, Hour) request + hour interval in Yahoo fallback."""
    client = AlpacaClient.__new__(AlpacaClient)
    resp = MagicMock()
    bar = MagicMock()
    bar.open, bar.high, bar.low, bar.close, bar.volume = 1.0, 2.0, 0.5, 1.5, 100
    bar.timestamp = datetime.now() - timedelta(days=1)
    resp.data = {"AAPL": [bar]}
    client._stock = MagicMock()
    client._stock.get_stock_bars.return_value = resp

    with patch("app.services.alpaca_client._fetch_yahoo_bars") as mock_yahoo:
        mock_yahoo.return_value = pd.DataFrame()  # not reached — Alpaca has data
        df = client.get_stock_bars("AAPL", timeframe="1h")

    call = client._stock.get_stock_bars.call_args
    req = call.kwargs["request_params"] if "request_params" in call.kwargs else call.args[0]
    assert not df.empty
    assert mock_yahoo.call_count == 0
    # stock bars request carries the timeframe — accept either arg position
    kwargs = call.kwargs
    req_obj = kwargs.get("request_params") or kwargs.get("request") or call.args[0]
    assert str(req_obj.timeframe).lower() in ("1h", "60min", "1hour")
