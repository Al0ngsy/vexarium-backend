from fastapi import APIRouter, HTTPException, Query, Request
from typing import Optional
from datetime import datetime
from ..schemas.options import (
    OptionContractSchema, OptionsChainResponse, OptionsPayoffResponse,
    GreeksSchema, PayoffRow
)
from ..middleware.rate_limit import limiter
from ..middleware.validation import validate_symbol
from ..services.alpaca_client import AlpacaClient, AlpacaError
from ..services.options_analyzer import compute_payoff, compute_breakeven, build_payoff_timeline
from ..config import settings

router = APIRouter(prefix="/options", tags=["options"])


def _parse_occ(contract_symbol: str) -> tuple[float, str, bool]:
    """Parse OCC option symbol: ROOT + YYMMDD + C/P + strike*1000. Returns (strike, expiry_iso, is_call)."""
    s = contract_symbol.strip().upper()
    # OCC equity option layout: ROOT(1-6) + YYMMDD(6) + C/P(1) + strike(8) = 16-21 chars.
    if len(s) < 16:
        raise ValueError("Invalid OCC symbol")
    cp = s[-9]
    strike = float(s[-8:]) / 1000.0
    yymmdd = s[-15:-9]
    expiry = f"20{yymmdd[0:2]}-{yymmdd[2:4]}-{yymmdd[4:6]}"
    return (strike, expiry, cp == "C")


@router.get("/{symbol}/chain", response_model=OptionsChainResponse)
@limiter.limit(f"{settings.rate_limit_free}/minute")
async def get_option_chain(
    request: Request,
    symbol: str,
    expiration_gte: str = Query(...),
    expiration_lte: str = Query(...),
    strike_gte: Optional[float] = None,
    strike_lte: Optional[float] = None,
    contract_type: Optional[str] = None,
):
    try:
        sym = validate_symbol(symbol)
        client = AlpacaClient()
        # Center the chain on the current underlying price so the picker shows
        # strikes around it (and includes both calls and puts across expiries).
        try:
            quote = client.get_latest_quote(sym)
            ref_price = quote.get("last_price") or quote.get("bid") or quote.get("ask")
        except Exception:
            ref_price = None
        contracts = client.get_option_contracts(
            underlying=sym,
            expiration_gte=expiration_gte,
            expiration_lte=expiration_lte,
            strike_gte=strike_gte,
            strike_lte=strike_lte,
            contract_type=contract_type,
            around_price=float(ref_price) if ref_price else None,
        )
        schema_contracts = []
        for c in contracts:
            raw_type = c.get("type", "call")
            t = str(raw_type)
            if "." in t:
                t = t.rsplit(".", 1)[-1]
            t = t.lower()
            schema_contracts.append(OptionContractSchema(
                symbol=c.get("symbol", ""),
                strike_price=float(c.get("strike_price", 0)),
                expiration_date=str(c.get("expiration_date", "")),
                type=t,
                last_price=float(c.get("last_price", 0) or 0),
                volume=float(c.get("volume", 0) or 0),
                open_interest=float(c.get("open_interest", 0) or 0),
                implied_volatility=float(c.get("implied_volatility", 0) or 0),
            ))
        return OptionsChainResponse(symbol=sym, contracts=schema_contracts)
    except AlpacaError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/{symbol}/payoff", response_model=OptionsPayoffResponse)
@limiter.limit(f"{settings.rate_limit_free}/minute")
async def get_option_payoff(request: Request, symbol: str, contract_symbol: str = Query(...)):
    try:
        sym = validate_symbol(symbol)
        strike, expiry, is_call = _parse_occ(contract_symbol)
        client = AlpacaClient()
        snap = client.get_option_snapshot(contract_symbol)
        if not snap:
            raise HTTPException(status_code=404, detail=f"No snapshot for {contract_symbol}")
        greeks = snap.get("greeks", {})
        premium = snap.get("latest_trade_price", 0) or snap.get("ask", 0)
        iv = snap.get("implied_volatility", 0)
        theta = greeks.get("theta", 0)
        current_price = snap.get("latest_trade_price", premium)
        be = compute_breakeven(strike, premium, is_call)
        timeline = build_payoff_timeline(strike, premium, current_price, expiry, abs(theta), is_call)
        return OptionsPayoffResponse(
            symbol=sym,
            greeks=GreeksSchema(**greeks),
            implied_volatility=iv,
            premium=premium,
            breakeven=be,
            payoff_timeline=[PayoffRow(**r) for r in timeline],
        )
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid contract symbol: {contract_symbol}")
    except AlpacaError as e:
        raise HTTPException(status_code=502, detail=str(e))
