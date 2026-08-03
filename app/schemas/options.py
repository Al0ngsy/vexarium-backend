from pydantic import BaseModel
from typing import Optional

class GreeksSchema(BaseModel):
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    rho: float = 0.0

class OptionContractSchema(BaseModel):
    symbol: str = ""
    strike_price: float = 0.0
    expiration_date: str = ""
    type: str = "call"
    last_price: float = 0.0
    volume: float = 0.0
    open_interest: float = 0.0
    implied_volatility: float = 0.0

class PayoffRow(BaseModel):
    date: str
    day: int
    estimated_option_price: float
    estimated_pl: float
    pl_pct: float

class OptionsChainResponse(BaseModel):
    symbol: str
    contracts: list[OptionContractSchema]

class OptionsPayoffResponse(BaseModel):
    symbol: str
    greeks: GreeksSchema
    implied_volatility: float = 0.0
    premium: float = 0.0
    breakeven: float = 0.0
    payoff_timeline: list[PayoffRow]
    is_estimate: bool = True
