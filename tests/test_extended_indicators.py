"""Tests for the extended Pro-tier indicators (ATR, ADX, OBV, VWAP, Ichimoku, CCI, Williams %R, MFI, ROC, PSAR, CMO)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.services.indicator_engine import create_default_engine
from app.services.indicators.extended import (
    ADXIndicator,
    ATRIndicator,
    CCIIndicator,
    CMOIndicator,
    IchimokuIndicator,
    MFIIndicator,
    OBVIndicator,
    PSARIndicator,
    ROCIndicator,
    VWAPIndicator,
    WILLRIndicator,
    create_pro_engine,
)

VALID_VERDICTS = {"strong_buy", "buy", "hold", "sell", "strong_sell", "none"}


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


def _short_df(n: int = 4) -> pd.DataFrame:
    """Tiny DataFrame for insufficient-data / None-handling tests."""
    return pd.DataFrame(
        {
            "open": [10.0] * n,
            "high": [11.0] * n,
            "low": [9.0] * n,
            "close": [10.0] * n,
            "volume": [1000] * n,
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


# -- CCI(20) ----------------------------------------------------------------

def test_cci_value(sample_df):
    value = CCIIndicator.compute(sample_df)
    assert value is not None
    assert isinstance(value, float)


def test_cci_verdict_thresholds():
    assert CCIIndicator.verdict(-150.0) == "strong_buy"
    assert CCIIndicator.verdict(-80.0) == "buy"
    assert CCIIndicator.verdict(0.0) == "hold"
    assert CCIIndicator.verdict(80.0) == "sell"
    assert CCIIndicator.verdict(150.0) == "strong_sell"
    assert CCIIndicator.verdict(None) == "hold"


def test_cci_verdict_on_sample(sample_df):
    value = CCIIndicator.compute(sample_df)
    verdict = CCIIndicator.verdict(value)
    assert verdict in VALID_VERDICTS


def test_cci_none_short_df():
    assert CCIIndicator.compute(_short_df()) is None


# -- Williams %R(14) --------------------------------------------------------

def test_willr_value(sample_df):
    value = WILLRIndicator.compute(sample_df)
    assert value is not None
    assert -100 <= value <= 0


def test_willr_verdict_thresholds():
    assert WILLRIndicator.verdict(-90.0) == "strong_buy"
    assert WILLRIndicator.verdict(-70.0) == "buy"
    assert WILLRIndicator.verdict(-50.0) == "hold"
    assert WILLRIndicator.verdict(-30.0) == "sell"
    assert WILLRIndicator.verdict(-10.0) == "strong_sell"
    assert WILLRIndicator.verdict(None) == "hold"


def test_willr_verdict_on_sample(sample_df):
    value = WILLRIndicator.compute(sample_df)
    verdict = WILLRIndicator.verdict(value)
    assert verdict in VALID_VERDICTS


def test_willr_none_short_df():
    assert WILLRIndicator.compute(_short_df()) is None


# -- MFI(14) ----------------------------------------------------------------

def test_mfi_value(sample_df):
    value = MFIIndicator.compute(sample_df)
    assert value is not None
    assert 0 <= value <= 100


def test_mfi_verdict_thresholds():
    assert MFIIndicator.verdict(10.0) == "strong_buy"
    assert MFIIndicator.verdict(30.0) == "buy"
    assert MFIIndicator.verdict(50.0) == "hold"
    assert MFIIndicator.verdict(70.0) == "sell"
    assert MFIIndicator.verdict(90.0) == "strong_sell"
    assert MFIIndicator.verdict(None) == "hold"


def test_mfi_verdict_on_sample(sample_df):
    value = MFIIndicator.compute(sample_df)
    verdict = MFIIndicator.verdict(value)
    assert verdict in VALID_VERDICTS


def test_mfi_none_short_df():
    assert MFIIndicator.compute(_short_df()) is None


# -- ROC(12) ----------------------------------------------------------------

def test_roc_value(sample_df):
    value = ROCIndicator.compute(sample_df)
    assert value is not None
    assert isinstance(value, float)


def test_roc_verdict_thresholds():
    assert ROCIndicator.verdict(10.0) == "strong_buy"
    assert ROCIndicator.verdict(2.0) == "buy"
    assert ROCIndicator.verdict(0.0) == "hold"
    assert ROCIndicator.verdict(-3.0) == "sell"
    assert ROCIndicator.verdict(-10.0) == "strong_sell"
    assert ROCIndicator.verdict(None) == "hold"


def test_roc_verdict_on_sample(sample_df):
    value = ROCIndicator.compute(sample_df)
    verdict = ROCIndicator.verdict(value)
    assert verdict in VALID_VERDICTS


def test_roc_none_short_df():
    assert ROCIndicator.compute(_short_df()) is None


# -- PSAR -------------------------------------------------------------------

def test_psar_value(sample_df):
    value = PSARIndicator.compute(sample_df)
    assert value is not None
    assert isinstance(value, dict)
    assert "psar" in value
    assert value["trend"] in ("up", "down")


def test_psar_verdict_thresholds():
    assert PSARIndicator.verdict({"psar": 1.0, "trend": "up"}) == "buy"
    assert PSARIndicator.verdict({"psar": 1.0, "trend": "down"}) == "sell"
    assert PSARIndicator.verdict(None) == "hold"
    assert PSARIndicator.verdict({"psar": 1.0}) == "hold"


def test_psar_verdict_on_sample(sample_df):
    value = PSARIndicator.compute(sample_df)
    verdict = PSARIndicator.verdict(value)
    assert verdict in {"buy", "sell", "hold"}


def test_psar_none_short_df():
    # 1-row frame: PSARl_/PSARs_ are both NaN on the only row -> None
    assert PSARIndicator.compute(_short_df(1)) is None


# -- CMO(14) ----------------------------------------------------------------

def test_cmo_value(sample_df):
    value = CMOIndicator.compute(sample_df)
    assert value is not None
    assert -100 <= value <= 100


def test_cmo_verdict_thresholds():
    assert CMOIndicator.verdict(-60.0) == "strong_buy"
    assert CMOIndicator.verdict(-30.0) == "buy"
    assert CMOIndicator.verdict(0.0) == "hold"
    assert CMOIndicator.verdict(30.0) == "sell"
    assert CMOIndicator.verdict(60.0) == "strong_sell"
    assert CMOIndicator.verdict(None) == "hold"


def test_cmo_verdict_on_sample(sample_df):
    value = CMOIndicator.compute(sample_df)
    verdict = CMOIndicator.verdict(value)
    assert verdict in VALID_VERDICTS


def test_cmo_none_short_df():
    assert CMOIndicator.compute(_short_df()) is None


# ---------------------------------------------------------------------------
# Engine tests
# ---------------------------------------------------------------------------


def test_pro_engine_has_sixteen_indicators():
    engine = create_pro_engine()
    assert len(engine.indicators) == 16


def test_pro_engine_compute_all_sixteen_results(sample_df):
    engine = create_pro_engine()
    results = engine.compute_all(sample_df)
    assert len(results) == 16
    for r in results:
        assert r.name is not None
        assert r.verdict in VALID_VERDICTS


def test_pro_engine_has_six_extended():
    engine = create_pro_engine()
    assert len(engine.indicators) == 16


def test_pro_engine_includes_new_indicator_names(sample_df):
    engine = create_pro_engine()
    names = set(engine.indicators)
    assert {"CCI(20)", "Williams %R(14)", "MFI(14)", "ROC(12)", "PSAR", "CMO(14)"} <= names


def test_insufficient_rows_gives_none_verdict():
    engine = create_pro_engine()
    # 1 row is below every indicator's min_rows (lowest is VWAP at 2)
    results = engine.compute_all(_short_df(1))
    for r in results:
        assert r.verdict == "none"


def test_default_engine_still_five(sample_df):
    engine = create_default_engine()
    results = engine.compute_all(sample_df)
    assert len(results) == 5
