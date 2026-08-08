"""Core indicator registry and engine for VEXARIUM.

Provides a pluggable registry of technical-analysis indicators. Each indicator
computes a value (or dict of values) from an OHLCV DataFrame and maps that value
to one of six verdicts: strong_buy, buy, hold, sell, strong_sell, none
(``none`` marks an indicator that could not be computed — insufficient data or
a compute error — and is excluded from aggregation).

New indicators can be registered without touching existing code::

    from app.services.indicator_engine import IndicatorEngine, Indicator

    engine = IndicatorEngine()
    engine.register(MyCustomIndicator())
    results = engine.compute_all(df)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import pandas as pd

# pandas-ta-remake ships as the `pandas_ta_remake` import package but also
# exposes a `pandas_ta` alias on some builds. Handle both gracefully.
try:
    import pandas_ta_remake as ta  # type: ignore
except ImportError:  # pragma: no cover - fallback path
    try:
        import pandas_ta as ta  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "No pandas-ta package found. Install pandas-ta-remake."
        ) from exc

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

VERDICTS = ("strong_buy", "buy", "hold", "sell", "strong_sell", "none")


@dataclass
class IndicatorResult:
    """Result of running a single indicator on a DataFrame."""

    name: str
    value: Any  # float | dict | None
    verdict: str
    note: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "value": self.value,
            "verdict": self.verdict,
            "note": self.note,
        }


@dataclass
class Indicator:
    """Base specification for a technical indicator.

    Subclasses (or instances) provide ``compute`` and ``verdict`` callables.
    """

    name: str
    compute: Callable[[pd.DataFrame], Any]
    verdict: Callable[[Any], str]
    min_rows: int = 0  # minimum rows required for a valid computation

    def evaluate(self, df: pd.DataFrame) -> IndicatorResult:
        """Run compute + verdict with edge-case handling."""
        if len(df) < self.min_rows:
            return IndicatorResult(
                name=self.name,
                value=None,
                verdict="none",
                note=f"insufficient data: have {len(df)} rows, need {self.min_rows}",
            )
        try:
            value = self.compute(df)
        except Exception as exc:  # noqa: BLE001 - log and skip
            logger.error("Indicator %s compute failed: %s", self.name, exc)
            return IndicatorResult(
                name=self.name,
                value=None,
                verdict="none",
                note=f"compute error: {exc}",
            )
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return IndicatorResult(
                name=self.name,
                value=None,
                verdict="none",
                note="no value produced",
            )
        try:
            verdict = self.verdict(value)
        except Exception as exc:  # noqa: BLE001
            logger.error("Indicator %s verdict failed: %s", self.name, exc)
            verdict = "none"
        return IndicatorResult(
            name=self.name,
            value=value,
            verdict=verdict,
        )


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class IndicatorEngine:
    """Pluggable registry + runner for indicators."""

    def __init__(self) -> None:
        self._registry: dict[str, Indicator] = {}

    # -- registration -------------------------------------------------------

    def register(self, indicator: Indicator) -> None:
        """Add (or replace) an indicator in the registry."""
        self._registry[indicator.name] = indicator
        logger.debug("Registered indicator: %s", indicator.name)

    @property
    def indicators(self) -> dict[str, Indicator]:
        return dict(self._registry)

    # -- compute ------------------------------------------------------------

    def compute_all(self, df: pd.DataFrame) -> list[IndicatorResult]:
        """Run every registered indicator on *df*.

        Failures are logged and skipped — one bad indicator never breaks the
        whole batch.
        """
        results: list[IndicatorResult] = []
        for name, indicator in self._registry.items():
            try:
                results.append(indicator.evaluate(df))
            except Exception as exc:  # noqa: BLE001 - ultimate safety net
                logger.error("Indicator %s failed during evaluate: %s", name, exc)
                results.append(
                    IndicatorResult(
                        name=name,
                        value=None,
                        verdict="none",
                        note=f"evaluate error: {exc}",
                    )
                )
        return results


# ---------------------------------------------------------------------------
# Core indicators
# ---------------------------------------------------------------------------


# -- RSI(14) -----------------------------------------------------------------

def _rsi_compute(df: pd.DataFrame) -> Optional[float]:
    close = df["close"]
    rsi = ta.rsi(close, length=14)
    if rsi is None or rsi.dropna().empty:
        return None
    val = rsi.dropna().iloc[-1]
    return float(val) if not pd.isna(val) else None


def _rsi_verdict(value: Any) -> str:
    if value is None:
        return "hold"
    if value < 30:
        return "strong_buy"
    if value < 40:
        return "buy"
    if value < 60:
        return "hold"
    if value < 70:
        return "sell"
    return "strong_sell"


RSIIndicator = Indicator(
    name="RSI(14)",
    compute=_rsi_compute,
    verdict=_rsi_verdict,
    min_rows=15,
)


# -- SMA(50) + EMA(200) ------------------------------------------------------

def _sma_ema_compute(df: pd.DataFrame) -> Optional[dict]:
    close = df["close"]
    current_price = float(close.iloc[-1])
    sma_series = ta.sma(close, length=50)
    ema_series = ta.ema(close, length=200)
    sma50 = float(sma_series.dropna().iloc[-1]) if sma_series is not None and not sma_series.dropna().empty else None
    ema200 = float(ema_series.dropna().iloc[-1]) if ema_series is not None and not ema_series.dropna().empty else None
    if sma50 is None or ema200 is None:
        return None
    return {"sma50": sma50, "ema200": ema200, "current_price": current_price}


def _sma_ema_verdict(value: Any) -> str:
    if value is None or not isinstance(value, dict):
        return "hold"
    price = value["current_price"]
    sma50 = value["sma50"]
    ema200 = value["ema200"]
    above_sma = price > sma50
    above_ema = price > ema200
    if above_sma and above_ema:
        return "buy"
    if above_sma and not above_ema:
        return "hold"
    if not above_sma and above_ema:
        return "hold"
    return "sell"


SMAEMAIndicator = Indicator(
    name="SMA(50)/EMA(200)",
    compute=_sma_ema_compute,
    verdict=_sma_ema_verdict,
    min_rows=201,
)


# -- MACD --------------------------------------------------------------------

def _macd_compute(df: pd.DataFrame) -> Optional[dict]:
    close = df["close"]
    macd_df = ta.macd(close, fast=12, slow=26, signal=9)
    if macd_df is None or macd_df.dropna().empty:
        return None
    row = macd_df.dropna().iloc[-1]
    # columns: MACD_12_26_9, MACDh_12_26_9, MACDs_12_26_9
    macd_col = [c for c in macd_df.columns if c.startswith("MACD_") and "h" not in c.lower() and "s" not in c.lower()]
    hist_col = [c for c in macd_df.columns if "MACDh" in c]
    sig_col = [c for c in macd_df.columns if "MACDs" in c]
    macd_val = float(row[macd_col[0]]) if macd_col else None
    hist_val = float(row[hist_col[0]]) if hist_col else None
    sig_val = float(row[sig_col[0]]) if sig_col else None
    if macd_val is None or hist_val is None or sig_val is None:
        return None
    return {"macd": macd_val, "histogram": hist_val, "signal": sig_val}


def _macd_verdict(value: Any) -> str:
    if value is None or not isinstance(value, dict):
        return "hold"
    hist = value["histogram"]
    macd_val = value["macd"]
    signal = value["signal"]
    if hist > 0 and macd_val > signal:
        return "buy"
    if hist > 0:
        return "hold"
    if hist < 0 and macd_val < signal:
        # significantly negative — use magnitude relative to price for robustness
        if hist < -0.5 * abs(macd_val) and macd_val < 0:
            return "strong_sell"
        return "sell"
    if hist < 0:
        return "sell"
    return "hold"


MACDIndicator = Indicator(
    name="MACD(12,26,9)",
    compute=_macd_compute,
    verdict=_macd_verdict,
    min_rows=35,
)


# -- Bollinger Bands(20, 2) --------------------------------------------------

def _bbands_compute(df: pd.DataFrame) -> Optional[dict]:
    close = df["close"]
    bb = ta.bbands(close, length=20, std=2)
    if bb is None or bb.dropna().empty:
        return None
    row = bb.dropna().iloc[-1]
    lower_col = [c for c in bb.columns if c.startswith("BBL_")]
    upper_col = [c for c in bb.columns if c.startswith("BBU_")]
    # %B column is BBP_*
    pctb_col = [c for c in bb.columns if c.startswith("BBP_")]
    current_price = float(close.iloc[-1])
    lower = float(row[lower_col[0]]) if lower_col else None
    upper = float(row[upper_col[0]]) if upper_col else None
    if pctb_col:
        pct_b = float(row[pctb_col[0]])
    else:
        # manual %B = (price - lower) / (upper - lower)
        if lower is None or upper is None or upper == lower:
            return None
        pct_b = (current_price - lower) / (upper - lower)
    return {"pct_b": pct_b, "lower": lower, "upper": upper, "current_price": current_price}


def _bbands_verdict(value: Any) -> str:
    if value is None or not isinstance(value, dict):
        return "hold"
    pct_b = value["pct_b"]
    if pct_b < 0:
        return "strong_buy"
    if pct_b < 0.2:
        return "buy"
    if pct_b < 0.8:
        return "hold"
    if pct_b <= 1.0:
        return "sell"
    return "strong_sell"


BollingerIndicator = Indicator(
    name="Bollinger(20,2)",
    compute=_bbands_compute,
    verdict=_bbands_verdict,
    min_rows=21,
)


# -- Stochastic(14, 3) -------------------------------------------------------

def _stoch_compute(df: pd.DataFrame) -> Optional[float]:
    stoch = ta.stoch(df["high"], df["low"], df["close"], k=14, d=3)
    if stoch is None or stoch.dropna().empty:
        return None
    k_col = [c for c in stoch.columns if "STOCHk" in c]
    if not k_col:
        return None
    val = stoch.dropna().iloc[-1][k_col[0]]
    return float(val) if not pd.isna(val) else None


def _stoch_verdict(value: Any) -> str:
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


StochasticIndicator = Indicator(
    name="Stochastic(14,3)",
    compute=_stoch_compute,
    verdict=_stoch_verdict,
    min_rows=17,
)


# ---------------------------------------------------------------------------
# Default engine factory
# ---------------------------------------------------------------------------


def create_default_engine() -> IndicatorEngine:
    """Return an engine pre-loaded with the 5 core indicators."""
    engine = IndicatorEngine()
    engine.register(RSIIndicator)
    engine.register(SMAEMAIndicator)
    engine.register(MACDIndicator)
    engine.register(BollingerIndicator)
    engine.register(StochasticIndicator)
    return engine


def create_pro_engine() -> IndicatorEngine:
    """Return an engine with all free + pro indicators registered.

    Same core registry as :func:`create_default_engine`, plus the 5 extended
    indicators (ATR, ADX, OBV, VWAP, Ichimoku).
    Imported lazily to avoid a circular import with the extended module.
    """
    from app.services.indicators.extended import (  # noqa: PLC0415
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
    )

    engine = create_default_engine()
    engine.register(ATRIndicator)
    engine.register(ADXIndicator)
    engine.register(OBVIndicator)
    engine.register(VWAPIndicator)
    engine.register(IchimokuIndicator)
    engine.register(CCIIndicator)
    engine.register(WILLRIndicator)
    engine.register(MFIIndicator)
    engine.register(ROCIndicator)
    engine.register(PSARIndicator)
    engine.register(CMOIndicator)
    return engine