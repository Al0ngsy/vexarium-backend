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
    strike: Optional[float] = None  # used by the options-strategies AI endpoint

class NewsArticle(BaseModel):
    """Alpaca news article. Alpaca returns id as int and created_at as a
    datetime, so keep those loosely typed to avoid validation failures."""
    id: Optional[str | int] = None
    headline: str
    source: Optional[str] = None
    url: Optional[str] = None
    summary: Optional[str] = None
    created_at: Optional[str] = None
    author: Optional[str] = None
    symbols: list[str] = []

    @classmethod
    def from_article(cls, a: dict) -> "NewsArticle":
        created = a.get("created_at")
        if hasattr(created, "isoformat"):
            created = created.isoformat()
        return cls(
            id=str(a.get("id")) if a.get("id") is not None else None,
            headline=a.get("headline") or a.get("title") or "",
            source=a.get("source") or "",
            url=a.get("url") or "",
            summary=a.get("summary") or "",
            created_at=created,
            author=a.get("author") or "",
            symbols=a.get("symbols") or [],
        )


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
    news_articles: list[NewsArticle] = []         # actual headlines for the dropdown
