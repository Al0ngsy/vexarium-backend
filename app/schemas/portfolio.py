from pydantic import BaseModel
from typing import Optional

class StanceRequest(BaseModel):
    symbol: str
    entry_price: float
    current_price: float
    trade_type: str = "stock"
    contract: Optional[dict] = None

class StanceResponse(BaseModel):
    stance: str
    reason: str
    pnl_pct: float
    take_profit_at: float
    cut_loss_at: float
