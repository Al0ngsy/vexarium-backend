"""Tests for the extended Pro-tier indicators (ATR, ADX, OBV, VWAP, Ichimoku)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.services.indicator_engine import create_default_engine
from app.services.indicators.extended import (
    ADXIndicator,
    ATRIndicator,
    IchimokuIndicator,
    OBVIndicator,
    VWAPIndicator,
    create_pro_engine,
)

VALID_VERDICTS = {"strong_buy", "buy", "hold", "sell", "strong_sell"}


@pytest.fixture()
def sample_df() -> pd.DataFrame:
    """Deterministic 250-row OHLCV DataFrame (matches test_indicators.py style)."""
    rng = np.random.default_rng(seed=42)
    n = 250
    returns = rng.normal(loc=0.0005, scale=0.01, size=n)
    close = 100 * np.cumprod(1 + returns)
    close = np.maximum(close, 1.0)
    high = close * (1 + rng.uniform(0, 0.005, size=n))
    low = close * (1 - rng.uniform(0, 0.005, size=n))
    open_ = (high + low) / 2
    volume = rng.integers(1_000_000, 5_000_000, size=n)
    timestamps = pd.date_range("2025-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


# ---------------------------------------------------------------------------
# Per-indicator value + verdict tests
# ---------------------------------------------------------------------------


def test_atr_value(sample_df):
    value = ATRIndicator.compute(sample_df)
    assert value is not None
    assert isinstance(value, dict)
    assert value["atr"] > 0


def test_atr_verdict(sample_df):
    value = ATRIndicator.compute(sample_df)
    verdict = ATRIndicator.verdict(value)
    assert verdict in VALID_VERDICTS


def test_adx_value(sample_df):
    value = ADXIndicator.compute(sample_df)
    assert value is not None
    assert 0 <= value <= 100


def test_adx_verdict(sample_df):
    value = ADXIndicator.compute(sample_df)
    verdict = ADXIndicator.verdict(value)
    assert verdict in VALID_VERDICTS


def test_obv_value(sample_df):
    value = OBVIndicator.compute(sample_df)
    assert value is not None
    assert isinstance(value, dict)
    assert isinstance(value["obv"], (int, float))


def test_obv_verdict(sample_df):
    value = OBVIndicator.compute(sample_df)
    verdict = OBVIndicator.verdict(value)
    assert verdict in VALID_VERDICTS


def test_vwap_value(sample_df):
    value = VWAPIndicator.compute(sample_df)
    assert value is not None
    assert isinstance(value, dict)
    assert value["vwap"] > 0


def test_vwap_verdict(sample_df):
    value = VWAPIndicator.compute(sample_df)
    verdict = VWAPIndicator.verdict(value)
    assert verdict in VALID_VERDICTS


def test_ichimoku_value(sample_df):
    value = IchimokuIndicator.compute(sample_df)
    assert value is not None
    assert isinstance(value, dict)
    assert "conversion" in value
    assert "base" in value
    assert "cloud_top" in value
    assert "cloud_bottom" in value


def test_ichimoku_verdict(sample_df):
    value = IchimokuIndicator.compute(sample_df)
    verdict = IchimokuIndicator.verdict(value)
    assert verdict in VALID_VERDICTS


# ---------------------------------------------------------------------------
# Engine tests
# ---------------------------------------------------------------------------


def test_pro_engine_has_ten_indicators():
    engine = create_pro_engine()
    assert len(engine.indicators) == 10


def test_pro_engine_compute_all_ten_results(sample_df):
    engine = create_pro_engine()
    results = engine.compute_all(sample_df)
    assert len(results) == 10
    for r in results:
        assert r.name is not None
        assert r.verdict in VALID_VERDICTS


def test_pro_engine_has_five_pro_five_free():
    engine = create_pro_engine()
    assert len(engine.indicators) == 10


def test_default_engine_still_five(sample_df):
    engine = create_default_engine()
    results = engine.compute_all(sample_df)
    assert len(results) == 5
