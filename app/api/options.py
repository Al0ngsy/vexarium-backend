from fastapi import APIRouter, HTTPException, Query, Request, Depends
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, date
from ..schemas.options import (
    OptionContractSchema, OptionsChainResponse, OptionsPayoffResponse,
    GreeksSchema, PayoffRow, OptionChanceResponse
)
from ..schemas.matrix import OptionsMatrixResponse
from ..middleware.rate_limit import limiter
from ..middleware.validation import validate_symbol
from ..middleware.tier_gating import require_tier
from ..services.alpaca_client import AlpacaClient, AlpacaError
from ..services.options_analyzer import (
    compute_breakeven, build_payoff_timeline,
    option_value_at_price, build_payoff_matrix, black_scholes_price,
    prob_profit,
)
from ..config import settings

router = APIRouter(prefix="/options", tags=["options"])


def _parse_occ(contract_symbol: str) -> tuple[float, str, bool]:
    """Parse OCC option symbol: ROOT + YYMMDD + C/P + strike*1000. Returns (strike, expiry_iso, is_call)."""
    if len(contract_symbol.strip().upper()) < 16:
        raise ValueError("Invalid OCC symbol")
    return AlpacaClient._parse_occ_symbol(contract_symbol)


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
    max_expiries: int = Query(10, ge=1, le=30),
):
    """Return the full option chain (TradingView-style) via Alpaca's market-data
    chain endpoint.

    For every contract in range it returns bid/ask, last trade, IV and greeks —
    not the trading-metadata call that yields empty volume/OI. Derived fields
    (DTE, intrinsic/time/theoretical value, spread, distance %) are computed
    server-side from the underlying price + Black-Scholes.
    """
    try:
        sym = validate_symbol(symbol)
        client = AlpacaClient()
        # Center the chain on the current underlying price so the picker shows
        # strikes around it.
        current_price = None
        day_change_pct = None
        try:
            quote = client.get_latest_quote(sym)
            current_price = quote.get("last_price") or quote.get("bid") or quote.get("ask")
        except Exception:
            current_price = None
        # Day change % from the snapshot.
        try:
            df = client.get_stock_bars(sym, days=30)
            snap = client.get_market_snapshot(sym, df=df)
            if current_price is None:
                current_price = snap.get("price")
            day_change_pct = snap.get("day_change_pct")
        except Exception:
            pass

        contracts = client.get_option_chain(
            underlying=sym,
            expiration_gte=expiration_gte,
            expiration_lte=expiration_lte,
            strike_gte=strike_gte,
            strike_lte=strike_lte,
            contract_type=contract_type,
        )

        # Limit to `max_expiries` expiries. Skip the same-day (0 DTE) expiry —
        # 0DTE contracts carry no greeks/IV on Alpaca, so the default view stays
        # meaningful. Spread across the remaining range.
        today_iso = date.today().isoformat()
        expiries = sorted({c["expiration_date"] for c in contracts if c.get("expiration_date") and c.get("expiration_date") != today_iso})
        picked = AlpacaClient._spread_expiries(expiries, max_expiries)
        picked_set = set(picked)

        schema_contracts = []
        for c in contracts:
            if c.get("expiration_date") not in picked_set:
                continue
            strike = float(c.get("strike_price", 0) or 0)
            is_call = c.get("type", "call") == "call"
            expiry = c.get("expiration_date", "")
            iv = float(c.get("implied_volatility", 0) or 0)
            bid = float(c.get("bid", 0) or 0)
            ask = float(c.get("ask", 0) or 0)
            last = float(c.get("last_price", 0) or 0)
            mid = ((bid + ask) / 2) if (bid and ask) else (last or bid or ask)
            days_to_expiry = _dte(expiry)
            intrinsic = 0.0
            theoretical = mid
            if current_price and strike:
                if is_call:
                    intrinsic = max(current_price - strike, 0)
                else:
                    intrinsic = max(strike - current_price, 0)
                theoretical = black_scholes_price(
                    strike=strike, price=current_price, days_to_expiry=days_to_expiry,
                    implied_vol=iv if iv else 0.3, risk_free=settings.risk_free_rate, is_call=is_call,
                ) if days_to_expiry > 0 else intrinsic
            time_value = max(theoretical - intrinsic, 0)
            spread = max(ask - bid, 0)
            distance_pct = ((strike - current_price) / current_price * 100) if current_price else 0.0
            g = c.get("greeks", {}) or {}
            schema_contracts.append(OptionContractSchema(
                symbol=c.get("symbol", ""),
                strike_price=strike,
                expiration_date=expiry,
                type=c.get("type", "call"),
                bid=round(bid, 2),
                ask=round(ask, 2),
                last_price=round(last, 2),
                implied_volatility=round(iv, 4),
                greeks=GreeksSchema(
                    delta=float(g.get("delta", 0) or 0),
                    gamma=float(g.get("gamma", 0) or 0),
                    theta=float(g.get("theta", 0) or 0),
                    vega=float(g.get("vega", 0) or 0),
                    rho=float(g.get("rho", 0) or 0),
                ),
                days_to_expiry=days_to_expiry,
                intrinsic_value=round(intrinsic, 2),
                time_value=round(time_value, 2),
                theoretical_value=round(theoretical, 2),
                spread=round(spread, 2),
                distance_pct=round(distance_pct, 2),
            ))
        return OptionsChainResponse(
            symbol=sym,
            current_price=round(float(current_price), 2) if current_price else None,
            day_change_pct=day_change_pct,
            delayed=True,
            contracts=schema_contracts,
        )
    except AlpacaError as e:
        raise HTTPException(status_code=502, detail=str(e))


def _dte(expiry: str) -> int:
    try:
        from datetime import datetime
        d = datetime.strptime(expiry[:10], "%Y-%m-%d").date()
        return max((d - date.today()).days, 0)
    except (ValueError, TypeError):
        return 0


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


class MatrixRequest(BaseModel):
    contract_symbol: str
    range_pct: float = 0.05
    quantity: int = 100


@router.post("/{symbol}/matrix", response_model=OptionsMatrixResponse)
@limiter.limit(f"{settings.rate_limit_free}/minute")
async def get_options_matrix(request: Request, symbol: str, body: MatrixRequest):
    """Build a strike × expiry P/L matrix for a contract (OptionStrat-inspired).

    Rows = strikes centered ±range_pct around the current price. Columns =
    expiry dates. Each cell is the projected P/L for holding to that expiry,
    priced via Black-Scholes.
    """
    try:
        sym = validate_symbol(symbol)
        strike, expiry, is_call = _parse_occ(body.contract_symbol)
        client = AlpacaClient()
        snap = client.get_option_snapshot(body.contract_symbol)
        if not snap:
            raise HTTPException(status_code=404, detail=f"No snapshot for {body.contract_symbol}")
        premium = snap.get("latest_trade_price", 0) or snap.get("ask", 0)
        iv = snap.get("implied_volatility", 0)
        try:
            quote = client.get_latest_quote(sym)
            current_price = quote.get("last_price") or quote.get("bid") or 0
        except Exception:
            current_price = strike  # fallback
        expiries = _matrix_expiries(client, sym, expiry)
        matrix = build_payoff_matrix(
            strike=strike, premium=premium, current_price=float(current_price or 0),
            expiry_date=expiry, implied_vol=iv, is_call=is_call,
            expiries=expiries, range_pct=body.range_pct, quantity=body.quantity,
        )
        return OptionsMatrixResponse(
            symbol=sym,
            contract_symbol=body.contract_symbol,
            current_price=round(float(current_price or 0), 2),
            range_pct=body.range_pct,
            premium=premium,
            breakeven=matrix["breakeven"],
            expiries=matrix["expiries"],
            strikes=matrix["strikes"],
        )
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid contract symbol: {body.contract_symbol}")
    except AlpacaError as e:
        raise HTTPException(status_code=502, detail=str(e))


def _matrix_expiries(client, sym: str, primary_expiry: str) -> list[str]:
    """Collect a handful of near expiries (primary + a few more) for the matrix."""
    from datetime import date, timedelta

    today = date.today()
    lte = (today + timedelta(days=200)).isoformat()
    try:
        contracts = client.get_option_contracts(
            sym, today.isoformat(), lte, around_price=None, max_expiries=4
        )
        exps = sorted({str(c.get("expiration_date")) for c in contracts if str(c.get("expiration_date")) != "None"})
    except Exception:
        exps = []
    if primary_expiry not in exps:
        exps = [primary_expiry] + [e for e in exps if e != primary_expiry]
    return exps[:6]


class OptionValueAtPriceResponse(BaseModel):
    symbol: str
    contract_symbol: str
    strike: float
    premium: float
    is_call: bool
    target_price: float
    target_date: str
    days_to_expiry: int
    estimated_option_price: float
    estimated_pl: float
    pl_pct: float


@router.get("/{symbol}/value", response_model=OptionValueAtPriceResponse)
@limiter.limit(f"{settings.rate_limit_free}/minute")
async def get_option_value_at_price(
    request: Request,
    symbol: str,
    contract_symbol: str = Query(...),
    target_price: float = Query(...),
    target_date: Optional[str] = None,
):
    """Estimate what an option is worth if the underlying trades at target_price.

    Uses Black-Scholes with the contract's implied volatility. Optionally pass
    target_date to see the value at a future date (after time decay).
    """
    try:
        sym = validate_symbol(symbol)
        strike, expiry, is_call = _parse_occ(contract_symbol)
        client = AlpacaClient()
        snap = client.get_option_snapshot(contract_symbol)
        if not snap:
            raise HTTPException(status_code=404, detail=f"No snapshot for {contract_symbol}")
        premium = snap.get("latest_trade_price", 0) or snap.get("ask", 0)
        iv = snap.get("implied_volatility", 0)
        val = option_value_at_price(
            strike=strike, premium=premium, current_price=target_price,
            expiry_date=expiry, implied_vol=iv, is_call=is_call,
            target_price=target_price, target_date=target_date,
        )
        return OptionValueAtPriceResponse(
            symbol=sym,
            contract_symbol=contract_symbol,
            strike=strike,
            premium=premium,
            is_call=is_call,
            **val,
        )
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid contract symbol: {contract_symbol}")
    except AlpacaError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/{symbol}/chance", response_model=OptionChanceResponse)
@limiter.limit(f"{settings.rate_limit_free}/minute")
async def get_option_chance(
    request: Request,
    symbol: str,
    contract_symbol: str = Query(...),
    token: str = "",
    # DEV: Pro gate removed during development, re-add `user_tier: str = Depends(require_tier("pro"))` before launch.
):
    """Estimate probability of profit / ending ITM for a long contract.

    Uses a Black-Scholes normal model with the contract's implied volatility.
    All values are estimates.
    """
    try:
        sym = validate_symbol(symbol)
        strike, expiry, is_call = _parse_occ(contract_symbol)
        client = AlpacaClient()
        snap = client.get_option_snapshot(contract_symbol)
        if not snap:
            raise HTTPException(status_code=404, detail=f"No snapshot for {contract_symbol}")
        premium = snap.get("latest_trade_price", 0) or snap.get("ask", 0)
        iv = snap.get("implied_volatility", 0)
        # Current underlying price.
        try:
            quote = client.get_latest_quote(sym)
            current_price = quote.get("last_price") or quote.get("bid") or quote.get("ask")
        except Exception:
            current_price = strike
        dte = _dte(expiry)
        result = prob_profit(
            strike=strike, premium=premium, current_price=float(current_price or 0),
            days_to_expiry=dte, implied_vol=iv, is_call=is_call,
            risk_free=settings.risk_free_rate,
        )
        return OptionChanceResponse(
            symbol=sym,
            contract_symbol=contract_symbol,
            is_call=is_call,
            strike=strike,
            premium=round(premium, 2),
            current_price=round(float(current_price or 0), 2),
            days_to_expiry=dte,
            implied_volatility=round(iv, 4),
            prob_profit=result["prob_profit"],
            prob_itm=result["prob_itm"],
            expected_value=result["expected_value"],
            breakeven=result["breakeven"],
        )
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid contract symbol: {contract_symbol}")
    except AlpacaError as e:
        raise HTTPException(status_code=502, detail=str(e))
