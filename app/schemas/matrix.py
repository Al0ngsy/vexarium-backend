from pydantic import BaseModel
from typing import Optional


class MatrixCell(BaseModel):
    expiry: str
    days_to_expiry: int
    option_value: float
    pl: float
    pl_pct: float


class MatrixRow(BaseModel):
    strike: float
    move_pct: float
    cells: list[MatrixCell]


class OptionsMatrixResponse(BaseModel):
    symbol: str
    contract_symbol: str
    current_price: float
    range_pct: float
    premium: float
    breakeven: float
    expiries: list[str]
    strikes: list[MatrixRow]
