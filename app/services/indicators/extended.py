"""Extended Pro-tier indicators for VEXARIUM.

Implements ATR, ADX, OBV, VWAP, and Ichimoku following the exact Indicator
pattern used in ``app.services.indicator_engine`` (dataclass ``Indicator`` with
``name`` / ``compute`` / ``verdict`` / ``tier`` / ``min_rows``). All indicators
here are registered with ``tier="pro"``.
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from app.services.indicator_engine import Indicator, IndicatorEngine, create_default_engine

# pandas-ta-remake ships as the `pandas_ta_remake` import package but also
# exposes a `pandas_ta` alias on some builds. Handle both gracefully.
try:
    import pandas_ta_remake as ta  # type: ignore
except ImportError:  # pragma: no cover - fallback path
    import pandas_ta as ta  # type: ignore


# ---------------------------------------------------------------------------
# ATR(14) — Average True Range
# ---------------------------------------------------------------------------


def _atr_compute(df: pd.DataFrame) -> Optional[dict]:
    atr = ta.atr(df["high"], df["low"], df["close"], length=14)
    if atr is None or atr.dropna().empty:
        return None
    val = atr.dropna().iloc[-1]
    atr_val = float(val) if not pd.isna(val) else None
    if atr_val is None:
        return None
    close = float(df["close"].iloc[-1])
    return {"atr": atr_val, "close": close}


def _atr_verdict(value: Any) -> str:
    if value is None or not isinstance(value, dict):
        return "hold"
    atr = value.get("atr")
    close = value.get("close")
    if atr is None or close is None or close == 0:
        return "hold"
    # High volatility (ATR > 5% of price) signals elevated risk.
    if atr / close > 0.05:
        return "sell"
    return "hold"


ATRIndicator = Indicator(
    name="ATR(14)",
    compute=_atr_compute,
    verdict=_atr_verdict,
    tier="pro",
    min_rows=15,
)


# ---------------------------------------------------------------------------
# ADX(25) — Trend strength
# ---------------------------------------------------------------------------


def _adx_compute(df: pd.DataFrame) -> Optional[float]:
    adx = ta.adx(df["high"], df["low"], df["close"], length=25)
    if adx is None or adx.dropna().empty:
        return None
    adx_col = [c for c in adx.columns if c.startswith("ADX_")]
    if not adx_col:
        return None
    val = adx.dropna().iloc[-1][adx_col[0]]
    return float(val) if not pd.isna(val) else None


def _adx_verdict(value: Any) -> str:
    if value is None:
        return "hold"
    if value > 25:
        return "buy"  # strong trend
    if value >= 20:
        return "hold"
    return "sell"  # weak / no trend


ADXIndicator = Indicator(
    name="ADX(25)",
    compute=_adx_compute,
    verdict=_adx_verdict,
    tier="pro",
    min_rows=30,
)


# ---------------------------------------------------------------------------
# OBV — On-Balance Volume
# ---------------------------------------------------------------------------


def _obv_compute(df: pd.DataFrame) -> Optional[dict]:
    obv = ta.obv(df["close"], df["volume"])
    if obv is None or obv.dropna().empty:
        return None
    series = obv.dropna()
    # Compare the last 10 values for a rolling trend direction.
    window = series.iloc[-10:]
    if len(window) < 2:
        return None
    first = float(window.iloc[0])
    last = float(window.iloc[-1])
    return {
        "obv": float(series.iloc[-1]),
        "trend": "rising" if last > first else ("falling" if last < first else "flat"),
    }


def _obv_verdict(value: Any) -> str:
    if value is None or not isinstance(value, dict):
        return "hold"
    trend = value.get("trend")
    if trend == "rising":
        return "buy"
    if trend == "falling":
        return "sell"
    return "hold"


OBVIndicator = Indicator(
    name="OBV",
    compute=_obv_compute,
    verdict=_obv_verdict,
    tier="pro",
    min_rows=11,
)


# ---------------------------------------------------------------------------
# VWAP — Volume-Weighted Average Price
# ---------------------------------------------------------------------------


def _vwap_compute(df: pd.DataFrame) -> Optional[dict]:
    # VWAP requires a datetime index to anchor the daily period.
    ts = df["timestamp"] if "timestamp" in df.columns else df.index
    work = df.set_index(pd.DatetimeIndex(pd.to_datetime(ts)))
    vwap = ta.vwap(work["high"], work["low"], work["close"], work["volume"])
    if vwap is None or vwap.dropna().empty:
        return None
    val = vwap.dropna().iloc[-1]
    vwap_val = float(val) if not pd.isna(val) else None
    if vwap_val is None:
        return None
    return {"vwap": vwap_val, "close": float(df["close"].iloc[-1])}


def _vwap_verdict(value: Any) -> str:
    if value is None or not isinstance(value, dict):
        return "hold"
    vwap = value.get("vwap")
    close = value.get("close")
    if vwap is None or close is None or vwap == 0:
        return "hold"
    ratio = close / vwap
    if ratio > 1.005:
        return "buy"
    if ratio < 0.995:
        return "sell"
    return "hold"  # near VWAP


VWAPIndicator = Indicator(
    name="VWAP",
    compute=_vwap_compute,
    verdict=_vwap_verdict,
    tier="pro",
    min_rows=2,
)


# ---------------------------------------------------------------------------
# Ichimoku — Cloud
# ---------------------------------------------------------------------------


def _ichimoku_compute(df: pd.DataFrame) -> Optional[dict]:
    ichi = ta.ichimoku(df["high"], df["low"], df["close"], include_chikou=False)
    if ichi is None or not isinstance(ichi, tuple) or not ichi:
        return None
    cloud_df = ichi[0]  # ISA_9, ISB_26, ITS_9, IKS_26
    if cloud_df is None or cloud_df.dropna().empty:
        return None
    row = cloud_df.dropna().iloc[-1]
    conv_col = [c for c in cloud_df.columns if c.startswith("ISA_")]
    base_col = [c for c in cloud_df.columns if c.startswith("ISB_")]
    # Senkou spans A (ITS) and B (IKS) form the cloud.
    span_a_col = [c for c in cloud_df.columns if c.startswith("ITS_")]
    span_b_col = [c for c in cloud_df.columns if c.startswith("IKS_")]
    if not (conv_col and base_col and span_a_col and span_b_col):
        return None
    conversion = float(row[conv_col[0]])
    base = float(row[base_col[0]])
    span_a = float(row[span_a_col[0]])
    span_b = float(row[span_b_col[0]])
    close = float(df["close"].iloc[-1])
    cloud_top = max(span_a, span_b)
    cloud_bottom = min(span_a, span_b)
    return {
        "conversion": conversion,
        "base": base,
        "cloud_top": cloud_top,
        "cloud_bottom": cloud_bottom,
        "close": close,
    }


def _ichimoku_verdict(value: Any) -> str:
    if value is None or not isinstance(value, dict):
        return "hold"
    close = value.get("close")
    cloud_top = value.get("cloud_top")
    cloud_bottom = value.get("cloud_bottom")
    if close is None or cloud_top is None or cloud_bottom is None:
        return "hold"
    above_cloud = close > cloud_top
    below_cloud = close < cloud_bottom
    if above_cloud:
        # Cloud is "green" when top (span A) >= bottom (span B) — bullish.
        cloud_green = cloud_top >= cloud_bottom
        if cloud_green:
            return "buy"
        return "hold"
    if below_cloud:
        return "sell"
    return "hold"  # inside the cloud


IchimokuIndicator = Indicator(
    name="Ichimoku",
    compute=_ichimoku_compute,
    verdict=_ichimoku_verdict,
    tier="pro",
    min_rows=60,
)


# ---------------------------------------------------------------------------
# Pro engine factory
# ---------------------------------------------------------------------------


def create_pro_engine() -> IndicatorEngine:
    """Return an engine with all free + pro indicators registered.

    Reuses the same free registry as ``create_default_engine`` and extends it
    with the 5 pro-tier indicators (ATR, ADX, OBV, VWAP, Ichimoku).
    """
    engine = create_default_engine()
    engine.register(ATRIndicator)
    engine.register(ADXIndicator)
    engine.register(OBVIndicator)
    engine.register(VWAPIndicator)
    engine.register(IchimokuIndicator)
    return engine
