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
    bid: float = 0.0
    ask: float = 0.0
    last_price: float = 0.0
    implied_volatility: float = 0.0
    greeks: GreeksSchema = GreeksSchema()
    # Computed helpers (server-side, Black-Scholes/quote-derived)
    days_to_expiry: int = 0
    intrinsic_value: float = 0.0
    time_value: float = 0.0
    theoretical_value: float = 0.0
    spread: float = 0.0
    distance_pct: float = 0.0  # % of underlying price the strike is away


class PayoffRow(BaseModel):
    date: str
    day: int
    estimated_option_price: float
    estimated_pl: float
    pl_pct: float


class OptionsChainResponse(BaseModel):
    symbol: str
    current_price: float | None = None
    day_change_pct: float | None = None
    delayed: bool = True
    contracts: list[OptionContractSchema]


class OptionsPayoffResponse(BaseModel):
    symbol: str
    greeks: GreeksSchema
    implied_volatility: float = 0.0
    premium: float = 0.0
    breakeven: float = 0.0
    payoff_timeline: list[PayoffRow]


class OptionChanceResponse(BaseModel):
    """Estimated probability of profit / winning for a long contract."""
    symbol: str
    contract_symbol: str
    is_call: bool
    strike: float
    premium: float
    current_price: float
    days_to_expiry: int
    implied_volatility: float
    prob_profit: float  # 0..1
    prob_itm: float  # 0..1
    expected_value: float  # est. option value at current price
    breakeven: float = 0.0
