from pydantic import BaseModel
from typing import Optional

class IndicatorPoint(BaseModel):
    t: str            # ISO date of the bar
    v: float          # indicator value at that point

class IndicatorSeries(BaseModel):
    name: str
    kind: str                       # "overlay" (price-scale) or "oscillator" (0-100 or unbounded)
    points: list[IndicatorPoint]    # series over the same window as the price series

class PricePoint(BaseModel):
    t: str
    open: float
    high: float
    low: float
    close: float

class IndicatorResult(BaseModel):
    name: str
    value: Optional[float | dict] = None
    verdict: str
    tier: str = "free"

class OverallVerdict(BaseModel):
    overall_verdict: str
    score: int
    indicator_count: int
    breakdown: list[IndicatorResult]

class AnalysisRequest(BaseModel):
    symbol: str
    asset_type: str = "stock"
    options_enabled: bool = False

class AnalysisResponse(BaseModel):
    symbol: str
    asset_type: str
    current_price: Optional[float] = None
    analyzed_at: Optional[str] = None
    overall: OverallVerdict
    indicators: list[IndicatorResult]
    price_series: list[PricePoint] = []          # OHLC for the chart (last ~120 bars)
    indicator_series: list[IndicatorSeries] = []  # per-indicator line series
    news_sentiment: Optional[dict] = None         # {sentiment_score, article_count, summary}
