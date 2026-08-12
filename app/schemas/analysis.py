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
    source: str = ""  # "alpaca" (IEX, real-time) | "yahoo" (~15 min delayed) | "" (unknown)

class IndicatorResult(BaseModel):
    name: str
    value: Optional[float | dict] = None
    verdict: str

class OverallVerdict(BaseModel):
    overall_verdict: str
    score: int
    indicator_count: int
    breakdown: list[IndicatorResult]

class AnalysisRequest(BaseModel):
    symbol: str
    asset_type: str = "stock"
    timeframe: str = "1d"
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


class MainListing(BaseModel):
    """Primary home-exchange listing of an OTC/foreign ADR (RNMBY -> RHM.DE)."""
    symbol: str
    name: Optional[str] = None
    exchange: Optional[str] = None


class CompanyInfo(BaseModel):
    """Free, keyless company/ETF profile + fundamentals (Yahoo + Wikipedia)."""
    symbol: str
    # Identity
    name: Optional[str] = None
    short_name: Optional[str] = None
    exchange: Optional[str] = None
    currency: Optional[str] = None
    description: Optional[str] = None  # plain-English Wikipedia summary
    main_listing: Optional[MainListing] = None  # OTC ADR -> primary listing
    # About / operations
    sector: Optional[str] = None
    industry: Optional[str] = None
    website: Optional[str] = None
    headquarters: Optional[str] = None
    employees: Optional[float] = None
    founded: Optional[float] = None
    # Management
    ceo: Optional[str] = None
    ceo_title: Optional[str] = None
    ceo_pay: Optional[float] = None
    # Market / valuation
    market_cap: Optional[float] = None
    shares_outstanding: Optional[float] = None
    pe_ratio: Optional[float] = None
    forward_pe: Optional[float] = None
    ps_ratio: Optional[float] = None
    pb_ratio: Optional[float] = None
    high_52w: Optional[float] = None
    low_52w: Optional[float] = None
    # Dividend
    dividend_yield: Optional[float] = None
    payout_ratio: Optional[float] = None
    # Performance / profitability (fractions, e.g. 0.63 = 63%)
    revenue_growth: Optional[float] = None
    earnings_growth: Optional[float] = None
    profit_margin: Optional[float] = None
    gross_margin: Optional[float] = None
    roe: Optional[float] = None
    roa: Optional[float] = None
    # Events
    next_earnings_date: Optional[str] = None


class AnalysisResponse(BaseModel):
    symbol: str
    asset_type: str
    timeframe: str = "1d"
    current_price: Optional[float] = None
    day_change_pct: Optional[float] = None
    analyzed_at: Optional[str] = None
    overall: OverallVerdict
    indicators: list[IndicatorResult]
    price_series: list[PricePoint] = []          # OHLC for the chart (last ~120 bars)
    indicator_series: list[IndicatorSeries] = []  # per-indicator line series
    news_sentiment: Optional[dict] = None         # {sentiment_score, article_count, summary}
    news_articles: list[NewsArticle] = []         # actual headlines for the dropdown
    company: Optional[CompanyInfo] = None         # free company/ETF profile
