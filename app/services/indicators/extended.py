"""Extended indicators for VEXARIUM.

Implements ATR, ADX, OBV, VWAP, Ichimoku, CCI, Williams %R, MFI, ROC,
PSAR, and CMO following the exact Indicator
pattern used in ``app.services.indicator_engine`` (dataclass ``Indicator`` with
``name`` / ``compute`` / ``verdict`` / ``min_rows``). All indicators
here are registered alongside the core set.
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from app.services.indicator_engine import Indicator

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
    min_rows=60,
)


# ---------------------------------------------------------------------------
# CCI(20) — Commodity Channel Index
# ---------------------------------------------------------------------------


def _cci_compute(df: pd.DataFrame) -> Optional[float]:
    cci = ta.cci(df["high"], df["low"], df["close"], length=20)
    if cci is None or cci.dropna().empty:
        return None
    val = cci.dropna().iloc[-1]
    return float(val) if not pd.isna(val) else None


def _cci_verdict(value: Any) -> str:
    if value is None:
        return "hold"
    if value < -100:
        return "strong_buy"
    if value < -60:
        return "buy"
    if value < 60:
        return "hold"
    if value < 100:
        return "sell"
    return "strong_sell"


CCIIndicator = Indicator(
    name="CCI(20)",
    compute=_cci_compute,
    verdict=_cci_verdict,
    min_rows=21,
)


# ---------------------------------------------------------------------------
# Williams %R(14)
# ---------------------------------------------------------------------------


def _willr_compute(df: pd.DataFrame) -> Optional[float]:
    willr = ta.willr(df["high"], df["low"], df["close"], length=14)
    if willr is None or willr.dropna().empty:
        return None
    val = willr.dropna().iloc[-1]
    return float(val) if not pd.isna(val) else None


def _willr_verdict(value: Any) -> str:
    if value is None:
        return "hold"
    if value < -80:
        return "strong_buy"
    if value < -60:
        return "buy"
    if value < -40:
        return "hold"
    if value < -20:
        return "sell"
    return "strong_sell"


WILLRIndicator = Indicator(
    name="Williams %R(14)",
    compute=_willr_compute,
    verdict=_willr_verdict,
    min_rows=15,
)


# ---------------------------------------------------------------------------
# MFI(14) — Money Flow Index
# ---------------------------------------------------------------------------


def _mfi_compute(df: pd.DataFrame) -> Optional[float]:
    mfi = ta.mfi(df["high"], df["low"], df["close"], df["volume"], length=14)
    if mfi is None or mfi.dropna().empty:
        return None
    val = mfi.dropna().iloc[-1]
    return float(val) if not pd.isna(val) else None


def _mfi_verdict(value: Any) -> str:
    if value is None:
        return "hold"
    if value < 20:
        return "strong_buy"
    if value < 40:
        return "buy"
    if value < 60:
        return "hold"
    if value < 80:
        return "sell"
    return "strong_sell"


MFIIndicator = Indicator(
    name="MFI(14)",
    compute=_mfi_compute,
    verdict=_mfi_verdict,
    min_rows=15,
)


# ---------------------------------------------------------------------------
# ROC(12) — Rate of Change
# ---------------------------------------------------------------------------


def _roc_compute(df: pd.DataFrame) -> Optional[float]:
    roc = ta.roc(df["close"], length=12)
    if roc is None or roc.dropna().empty:
        return None
    val = roc.dropna().iloc[-1]
    return float(val) if not pd.isna(val) else None


def _roc_verdict(value: Any) -> str:
    if value is None:
        return "hold"
    if value > 5:
        return "strong_buy"
    if value > 1:
        return "buy"
    if value > -1:
        return "hold"
    if value > -5:
        return "sell"
    return "strong_sell"


ROCIndicator = Indicator(
    name="ROC(12)",
    compute=_roc_compute,
    verdict=_roc_verdict,
    min_rows=13,
)


# ---------------------------------------------------------------------------
# PSAR — Parabolic SAR
# ---------------------------------------------------------------------------


def _psar_compute(df: pd.DataFrame) -> Optional[dict]:
    psar = ta.psar(df["high"], df["low"], df["close"])
    if psar is None or psar.empty:
        return None
    # Columns: PSARl_* (long/up), PSARs_* (short/down) — mutually exclusive
    # per row. There is no trend column; presence of PSARl_/PSARs_ is the trend.
    long_col = [c for c in psar.columns if c.startswith("PSARl_")]
    short_col = [c for c in psar.columns if c.startswith("PSARs_")]
    if not (long_col and short_col):
        return None
    row = psar.iloc[-1]
    long_val = row[long_col[0]]
    short_val = row[short_col[0]]
    if not pd.isna(long_val):
        return {"psar": float(long_val), "trend": "up"}
    if not pd.isna(short_val):
        return {"psar": float(short_val), "trend": "down"}
    return None


def _psar_verdict(value: Any) -> str:
    if value is None or not isinstance(value, dict):
        return "hold"
    if value.get("trend") == "up":
        return "buy"
    if value.get("trend") == "down":
        return "sell"
    return "hold"


PSARIndicator = Indicator(
    name="PSAR",
    compute=_psar_compute,
    verdict=_psar_verdict,
    min_rows=5,
)


# ---------------------------------------------------------------------------
# CMO(14) — Chande Momentum Oscillator
# ---------------------------------------------------------------------------


def _cmo_compute(df: pd.DataFrame) -> Optional[float]:
    cmo = ta.cmo(df["close"], length=14)
    if cmo is None or cmo.dropna().empty:
        return None
    val = cmo.dropna().iloc[-1]
    return float(val) if not pd.isna(val) else None


def _cmo_verdict(value: Any) -> str:
    if value is None:
        return "hold"
    if value < -50:
        return "strong_buy"
    if value < -25:
        return "buy"
    if value < 25:
        return "hold"
    if value < 50:
        return "sell"
    return "strong_sell"


CMOIndicator = Indicator(
    name="CMO(14)",
    compute=_cmo_compute,
    verdict=_cmo_verdict,
    min_rows=15,
)
