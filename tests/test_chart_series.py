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


def test_source_survives_cache_round_trip_via_column():
    """B6: bars cached with df.to_json drop attrs, so the source must be read
    from a regular column (what _bars_cache_json materializes)."""
    df = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2026-08-07 14:30", "2026-08-07 14:31"]),
            "open": [1.0, 1.1],
            "high": [1.05, 1.15],
            "low": [0.95, 1.05],
            "close": [1.0, 1.1],
            "volume": [100, 100],
            "source": ["twelvedata", "twelvedata"],
        }
    )
    out = build_price_series(df)
    assert all(p["source"] == "twelvedata" for p in out)


def test_source_falls_back_to_attrs_without_column():
    df = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2026-08-07 14:30"]),
            "open": [1.0], "high": [1.05], "low": [0.95],
            "close": [1.0], "volume": [100],
        }
    )
    df.attrs["source"] = "yahoo"
    out = build_price_series(df)
    assert out[0]["source"] == "yahoo"
