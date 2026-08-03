from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from datetime import datetime, timedelta
from ..schemas.options import (
    OptionContractSchema, OptionsChainResponse, OptionsPayoffResponse,
    GreeksSchema, PayoffRow
)
from ..services.alpaca_client import AlpacaClient, AlpacaError
from ..services.options_analyzer import compute_payoff, compute_breakeven, build_payoff_timeline

router = APIRouter(prefix="/options", tags=["options"])

@router.get("/{symbol}/chain", response_model=OptionsChainResponse)
async def get_option_chain(
    symbol: str,
    expiration_gte: str = Query(...),
    expiration_lte: str = Query(...),
    strike_gte: Optional[float] = None,
    strike_lte: Optional[float] = None,
    contract_type: Optional[str] = None,
):
    try:
        client = AlpacaClient()
        contracts = client.get_option_contracts(
            underlying=symbol,
            expiration_gte=expiration_gte,
            expiration_lte=expiration_lte,
            strike_gte=strike_gte,
            strike_lte=strike_lte,
            contract_type=contract_type,
        )
        schema_contracts = []
        for c in contracts:
            schema_contracts.append(OptionContractSchema(
                symbol=c.get("symbol", ""),
                strike_price=float(c.get("strike_price", 0)),
                expiration_date=str(c.get("expiration_date", "")),
                type=c.get("type", "call"),
                last_price=float(c.get("last_price", 0) or 0),
                volume=float(c.get("volume", 0) or 0),
                open_interest=float(c.get("open_interest", 0) or 0),
                implied_volatility=float(c.get("implied_volatility", 0) or 0),
            ))
        return OptionsChainResponse(symbol=symbol, contracts=schema_contracts)
    except AlpacaError as e:
        raise HTTPException(status_code=502, detail=str(e))

@router.get("/{symbol}/payoff", response_model=OptionsPayoffResponse)
async def get_option_payoff(symbol: str, contract_symbol: str = Query(...)):
    try:
        client = AlpacaClient()
        snap = client.get_option_snapshot(contract_symbol)
        if not snap:
            raise HTTPException(status_code=404, detail=f"No snapshot for {contract_symbol}")
        greeks = snap.get("greeks", {})
        premium = snap.get("latest_trade_price", 0) or snap.get("ask", 0)
        iv = snap.get("implied_volatility", 0)
        theta = greeks.get("theta", 0)
        is_call = "C" in contract_symbol.upper()
        strike = 100.0
        expiry = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        current_price = snap.get("latest_trade_price", premium)
        be = compute_breakeven(strike, premium, is_call)
        timeline = build_payoff_timeline(strike, premium, current_price, expiry, abs(theta), is_call)
        return OptionsPayoffResponse(
            symbol=symbol,
            greeks=GreeksSchema(**greeks),
            implied_volatility=iv,
            premium=premium,
            breakeven=be,
            payoff_timeline=[PayoffRow(**r) for r in timeline],
        )
    except AlpacaError as e:
        raise HTTPException(status_code=502, detail=str(e))
