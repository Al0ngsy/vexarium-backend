"""Tests for the core indicator engine and registry."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.services.indicator_engine import (
    BollingerIndicator,
    Indicator,
    IndicatorEngine,
    MACDIndicator,
    RSIIndicator,
    SMAEMAIndicator,
    StochasticIndicator,
    create_default_engine,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_df() -> pd.DataFrame:
    """Deterministic 250-row OHLCV DataFrame (random walk with drift)."""
    rng = np.random.default_rng(seed=42)
    n = 250
    returns = rng.normal(loc=0.0005, scale=0.01, size=n)
    close = 100 * np.cumprod(1 + returns)
    # Ensure positive prices
    close = np.maximum(close, 1.0)
    high = close * (1 + rng.uniform(0, 0.005, size=n))
    low = close * (1 - rng.uniform(0, 0.005, size=n))
    open_ = (high + low) / 2
    volume = rng.integers(1_000_000, 5_000_000, size=n)
    timestamps = pd.date_range("2025-01-01", periods=n, freq="D")
    df = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )
    return df


@pytest.fixture()
def small_df() -> pd.DataFrame:
    """10-row DataFrame — too small for most indicators."""
    rng = np.random.default_rng(seed=1)
    n = 10
    close = 100 + rng.normal(0, 1, size=n).cumsum()
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2025-01-01", periods=n, freq="D"),
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": [1000] * n,
        }
    )


# ---------------------------------------------------------------------------
# Per-indicator domain tests
# ---------------------------------------------------------------------------


VALID_VERDICTS = {"strong_buy", "buy", "hold", "sell", "strong_sell", "none"}


def test_rsi_value_range(sample_df):
    value = RSIIndicator.compute(sample_df)
    assert value is not None
    assert 0 <= value <= 100


def test_rsi_verdict(sample_df):
    value = RSIIndicator.compute(sample_df)
    verdict = RSIIndicator.verdict(value)
    assert verdict in VALID_VERDICTS


def test_sma_ema_value(sample_df):
    value = SMAEMAIndicator.compute(sample_df)
    assert value is not None
    assert isinstance(value, dict)
    assert value["sma50"] > 0
    assert value["ema200"] > 0
    assert value["current_price"] > 0


def test_sma_ema_verdict(sample_df):
    value = SMAEMAIndicator.compute(sample_df)
    verdict = SMAEMAIndicator.verdict(value)
    assert verdict in VALID_VERDICTS


def test_macd_value(sample_df):
    value = MACDIndicator.compute(sample_df)
    assert value is not None
    assert isinstance(value, dict)
    assert isinstance(value["histogram"], float)


def test_macd_verdict(sample_df):
    value = MACDIndicator.compute(sample_df)
    verdict = MACDIndicator.verdict(value)
    assert verdict in VALID_VERDICTS


def test_bollinger_value(sample_df):
    value = BollingerIndicator.compute(sample_df)
    assert value is not None
    assert isinstance(value, dict)
    assert isinstance(value["pct_b"], float)


def test_bollinger_verdict(sample_df):
    value = BollingerIndicator.compute(sample_df)
    verdict = BollingerIndicator.verdict(value)
    assert verdict in VALID_VERDICTS


def test_stochastic_value(sample_df):
    value = StochasticIndicator.compute(sample_df)
    assert value is not None
    assert 0 <= value <= 100


def test_stochastic_verdict(sample_df):
    value = StochasticIndicator.compute(sample_df)
    verdict = StochasticIndicator.verdict(value)
    assert verdict in VALID_VERDICTS


# ---------------------------------------------------------------------------
# Engine / registry tests
# ---------------------------------------------------------------------------


def test_default_engine_has_five_indicators():
    engine = create_default_engine()
    assert len(engine.indicators) == 5
    names = set(engine.indicators)
    assert "RSI(14)" in names
    assert "SMA(50)/EMA(200)" in names
    assert "MACD(12,26,9)" in names
    assert "Bollinger(20,2)" in names
    assert "Stochastic(14,3)" in names


def test_compute_all_returns_results(sample_df):
    engine = create_default_engine()
    results = engine.compute_all(sample_df)
    assert len(results) == 5
    for r in results:
        assert r.name is not None
        assert r.verdict in VALID_VERDICTS


def test_compute_all_results_are_serializable(sample_df):
    engine = create_default_engine()
    results = engine.compute_all(sample_df)
    for r in results:
        d = r.to_dict()
        assert set(d.keys()) == {"name", "value", "verdict", "note"}


def test_custom_indicator_registered(sample_df):
    """Register a custom indicator and verify it appears in compute_all output."""
    custom = Indicator(
        name="CustomPrice",
        compute=lambda df: float(df["close"].iloc[-1]),
        verdict=lambda v: "buy" if v > 50 else "sell",
        min_rows=1,
    )
    engine = IndicatorEngine()
    engine.register(custom)
    assert "CustomPrice" in engine.indicators
    results = engine.compute_all(sample_df)
    names = [r.name for r in results]
    assert "CustomPrice" in names
    custom_result = [r for r in results if r.name == "CustomPrice"][0]
    assert custom_result.verdict in VALID_VERDICTS


def test_compute_all_handles_failing_indicator(sample_df):
    """An indicator that raises should not break the engine."""

    def boom(df):
        raise RuntimeError("intentional")

    bad = Indicator(name="Boom", compute=boom, verdict=lambda v: "hold")
    engine = IndicatorEngine()
    engine.register(bad)
    engine.register(RSIIndicator)
    results = engine.compute_all(sample_df)
    assert len(results) == 2
    boom_result = [r for r in results if r.name == "Boom"][0]
    assert boom_result.verdict == "none"
    assert boom_result.value is None
    assert boom_result.note is not None
    # The good indicator still ran
    rsi_result = [r for r in results if r.name == "RSI(14)"][0]
    assert rsi_result.value is not None


def test_insufficient_data_returns_none(small_df):
    """Not enough rows for EMA(200) → 'none' verdict with a note (not 'hold')."""
    result = SMAEMAIndicator.evaluate(small_df)
    assert result.verdict == "none"
    assert result.value is None
    assert result.note is not None
    assert "insufficient" in result.note


def test_insufficient_data_in_engine(small_df):
    engine = create_default_engine()
    results = engine.compute_all(small_df)
    # Every core indicator needs more than 10 rows — all are uncomputable.
    for r in results:
        assert r.verdict == "none"
        assert r.value is None


def test_replace_indicator():
    engine = IndicatorEngine()
    ind1 = Indicator(name="X", compute=lambda df: 1.0, verdict=lambda v: "hold")
    ind2 = Indicator(name="X", compute=lambda df: 2.0, verdict=lambda v: "buy")
    engine.register(ind1)
    engine.register(ind2)
    assert engine.indicators["X"].compute(pd.DataFrame({"close": [1]})) == 2.0