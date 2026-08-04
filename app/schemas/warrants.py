from pydantic import BaseModel
from typing import Optional


class WarrantSchema(BaseModel):
    wkn: str = ""
    isin: str = ""
    name: str = ""
    underlying: str = ""
    underlying_isin: str = ""
    underlying_wkn: str = ""
    exercise_right: str = ""  # CALL | PUT
    exercise_style: str = ""  # American | European
    strike: Optional[float] = None
    strike_pct: Optional[float] = None
    maturity: Optional[str] = None
    cover_ratio: Optional[float] = None
    leverage: Optional[float] = None
    omega: Optional[float] = None
    implied_volatility: Optional[float] = None
    spread_pct: Optional[float] = None
    issuer: str = ""
    bid: Optional[float] = None
    ask: Optional[float] = None
    premium: Optional[float] = None


class WarrantsResponse(BaseModel):
    underlying: Optional[str] = None
    total: int
    warrants: list[WarrantSchema]


class WarrantValueResponse(BaseModel):
    wkn: str
    isin: str
    exercise_right: str
    strike: float
    cover_ratio: float
    target_price: float
    estimated_option_price: float
    estimated_pl: float
    pl_pct: float
