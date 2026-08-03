from pydantic import BaseModel
from typing import Optional

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
    overall: OverallVerdict
    indicators: list[IndicatorResult]
