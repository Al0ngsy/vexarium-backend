from fastapi import APIRouter, HTTPException, Query, Body, Request
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from ..repositories.trades import InMemoryTradeRepository
from ..repositories.users import get_user_store
from ..services.auth import decode_token
from ..middleware.rate_limit import limiter
from ..config import settings
from ..logging import get_logger
from ..middleware.logging import get_request_id as _rid

router = APIRouter(prefix="/trades", tags=["trades"])
logger = get_logger("trades")

_repo: InMemoryTradeRepository | None = None


def _repo_instance() -> InMemoryTradeRepository:
    global _repo
    if _repo is None:
        _repo = InMemoryTradeRepository()
    return _repo


def reset_trade_repo() -> None:
    global _repo
    _repo = None


class TradeCreate(BaseModel):
    symbol: str
    trade_type: str = "stock"
    entry_date: str
    entry_price: float
    quantity: float = 1.0
    contract: Optional[str] = None
    notes: Optional[str] = None


async def _get_user_id(token: str) -> int:
    payload = decode_token(token)
    uid = int(payload.get("sub", "0"))
    store = get_user_store()
    if await store.get_by_id(uid) is None:
        raise HTTPException(status_code=404, detail="User not found")
    return uid


@router.post("", status_code=201)
@limiter.limit(f"{settings.rate_limit_free}/minute")
async def create_trade(request: Request, token: str = Query(""), trade: TradeCreate = Body(...)):
    uid = await _get_user_id(token)
    logger.info("rid=%s trades create user_id=%d symbol=%s type=%s", _rid(request), uid, trade.symbol, trade.trade_type)
    logger.debug("rid=%s trades create user_id=%d entry_price=%s qty=%s contract=%s", _rid(request), uid, trade.entry_price, trade.quantity, trade.contract or "-")
    entry_date = datetime.fromisoformat(trade.entry_date)
    result = _repo_instance().create_trade(
        user_id=uid, symbol=trade.symbol, trade_type=trade.trade_type,
        entry_date=entry_date, entry_price=trade.entry_price,
        quantity=trade.quantity, contract=trade.contract, notes=trade.notes,
    )
    logger.info("rid=%s trades create user_id=%d done trade_id=%s", _rid(request), uid, (result or {}).get("id"))
    return result


@router.get("")
@limiter.limit(f"{settings.rate_limit_free}/minute")
async def list_trades(request: Request, token: str = Query("")):
    uid = await _get_user_id(token)
    trades = _repo_instance().list_trades(uid)
    logger.info("rid=%s trades list user_id=%d done count=%d", _rid(request), uid, len(trades))
    return trades


@router.delete("/{trade_id}", status_code=204)
@limiter.limit(f"{settings.rate_limit_free}/minute")
async def delete_trade(request: Request, trade_id: int, token: str = Query("")):
    uid = await _get_user_id(token)
    logger.info("rid=%s trades delete user_id=%d trade_id=%d", _rid(request), uid, trade_id)
    if not _repo_instance().delete_trade(uid, trade_id):
        logger.warning("rid=%s trades delete user_id=%d trade_id=%d not found → 404", _rid(request), uid, trade_id)
        raise HTTPException(status_code=404, detail="Trade not found")
