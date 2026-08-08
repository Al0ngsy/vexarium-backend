"""Regression: intraday bars must keep distinct timestamps.

Date-only timestamps made every bar of a day collapse onto one time, so
lightweight-charts rendered nothing for 1m/5m/15m/1h/… — most visible when
the market is closed and the chart should show the last session up to close.
"""
import pandas as pd

from app.services.chart_series import build_price_series


def test_intraday_bars_keep_unique_times():
    df = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                ["2026-08-07 14:30", "2026-08-07 14:31", "2026-08-07 14:32"]
            ),
            "open": [1.0, 1.1, 1.2],
            "high": [1.05, 1.15, 1.25],
            "low": [0.95, 1.05, 1.15],
            "close": [1.0, 1.1, 1.2],
            "volume": [100, 100, 100],
        }
    )
    out = build_price_series(df)
    times = [p["t"] for p in out]
    assert len(times) == len(set(times)), f"duplicate chart times: {times}"
    assert times == sorted(times)
    assert times[0].startswith("2026-08-07T14:30")
