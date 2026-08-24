from pydantic import BaseModel
from typing import Optional

class PayoffPoint(BaseModel):
    price: float
    pl: float

class StrategyCard(BaseModel):
    name: str
    subtitle: str
    is_bullish: bool
    max_profit: Optional[float]
    max_loss: Optional[float]
    breakeven: float
    return_on_risk: Optional[float]
    payoff_curve: list[PayoffPoint]

class StrategiesResponse(BaseModel):
    symbol: str
    sentiment: str
    timeframe: str = '1d'
    strategies: list[StrategyCard]
