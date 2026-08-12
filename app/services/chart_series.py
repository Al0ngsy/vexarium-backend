"""Build time series for charting: price OHLC + per-indicator lines."""
from __future__ import annotations

import pandas as pd


def build_price_series(df: pd.DataFrame, limit: int = 120) -> list[dict]:
    """Return last `limit` OHLC points as {t, open, high, low, close}.

    `t` is the full ISO timestamp, not date-only — intraday timeframes
    (1m/5m/15m/…) would otherwise collapse every bar of a day onto one time
    and the chart renders nothing.
    """
    out = []
    source = df.attrs.get("source", "")  # "alpaca" (IEX, real-time) | "yahoo" | "" (cached/unknown)
    for _, row in df.tail(limit).iterrows():
        ts = row.get("timestamp")
        t = ts.isoformat() if isinstance(ts, pd.Timestamp) else str(ts)
        out.append({
            "t": t,
            "open": round(float(row["open"]), 4),
            "high": round(float(row["high"]), 4),
            "low": round(float(row["low"]), 4),
            "close": round(float(row["close"]), 4),
            "source": source,
        })
    return out


def compute_series_for(df: pd.DataFrame, name: str) -> list[float]:
    """Return the full-length indicator series for a known indicator name, or []."""
    import pandas_ta_remake as ta  # type: ignore
    close = df["close"]
    if name == "RSI(14)":
        s = ta.rsi(close, length=14)
        return list(s) if s is not None else []
    if name == "SMA(50)/EMA(200)":
        sma = ta.sma(close, length=50)
        return list(sma) if sma is not None else []
    if name == "MACD(12,26,9)":
        m = ta.macd(close, fast=12, slow=26, signal=9)
        if m is None: return []
        hist_col = [c for c in m.columns if "MACDh" in c]
        return list(m[hist_col[0]]) if hist_col else []
    if name == "Bollinger(20,2)":
        bb = ta.bbands(close, length=20, std=2)
        if bb is None: return []
        mid_col = [c for c in bb.columns if c.startswith("BBM_")]
        return list(bb[mid_col[0]]) if mid_col else []
    if name == "Stochastic(14,3)":
        st = ta.stoch(df["high"], df["low"], close, k=14, d=3)
        if st is None: return []
        k_col = [c for c in st.columns if "STOCHk" in c]
        return list(st[k_col[0]]) if k_col else []
    # pro indicators
    if name == "ATR(14)":
        s = ta.atr(df["high"], df["low"], close, length=14)
        return list(s) if s is not None else []
    if name == "ADX(25)":
        s = ta.adx(df["high"], df["low"], close, length=25)
        if s is None: return []
        col = [c for c in s.columns if c.startswith("ADX_")]
        return list(s[col[0]]) if col else []
    if name == "VWAP":
        ts = df["timestamp"] if "timestamp" in df.columns else df.index
        work = df.set_index(pd.DatetimeIndex(pd.to_datetime(ts)))
        s = ta.vwap(work["high"], work["low"], work["close"], work["volume"])
        return list(s) if s is not None else []
    return []


def indicator_kind(name: str) -> str:
    """Classify an indicator as price-scale overlay vs. oscillator."""
    upper = name.upper()
    if any(token in upper for token in ("SMA", "EMA", "BOLLINGER", "VWAP", "ICHIMOKU")):
        return "overlay"
    return "oscillator"
