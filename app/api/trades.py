from fastapi import APIRouter, HTTPException, Query, Body, Request
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from ..repositories.trades import InMemoryTradeRepository
from ..api.auth import _users
from ..services.auth import decode_token
from ..middleware.rate_limit import limiter
from ..config import settings

router = APIRouter(prefix="/trades", tags=["trades"])

_repo = InMemoryTradeRepository()


class TradeCreate(BaseModel):
    symbol: str
    trade_type: str = "stock"
    entry_date: str
    entry_price: float
    quantity: float = 1.0
    contract: Optional[str] = None
    notes: Optional[str] = None


def _get_user_id(token: str) -> int:
    payload = decode_token(token)
    uid = int(payload.get("sub", "0"))
    if uid not in _users:
        raise HTTPException(status_code=404, detail="User not found")
    return uid


@router.post("", status_code=201)
@limiter.limit(f"{settings.rate_limit_free}/minute")
async def create_trade(request: Request, token: str = Query(""), trade: TradeCreate = Body(...)):
    uid = _get_user_id(token)
    entry_date = datetime.fromisoformat(trade.entry_date)
    result = _repo.create_trade(
        user_id=uid, symbol=trade.symbol, trade_type=trade.trade_type,
        entry_date=entry_date, entry_price=trade.entry_price,
        quantity=trade.quantity, contract=trade.contract, notes=trade.notes,
    )
    return result


@router.get("")
@limiter.limit(f"{settings.rate_limit_free}/minute")
async def list_trades(request: Request, token: str = Query("")):
    uid = _get_user_id(token)
    return _repo.list_trades(uid)


@router.delete("/{trade_id}", status_code=204)
@limiter.limit(f"{settings.rate_limit_free}/minute")
async def delete_trade(request: Request, trade_id: int, token: str = Query("")):
    uid = _get_user_id(token)
    if not _repo.delete_trade(uid, trade_id):
        raise HTTPException(status_code=404, detail="Trade not found")


@router.post("/stance")
@limiter.limit(f"{settings.rate_limit_free}/minute")
async def batch_stance(request: Request, token: str = Query("")):
    uid = _get_user_id(token)
    trades = _repo.list_trades(uid)
    return {"count": len(trades)}
