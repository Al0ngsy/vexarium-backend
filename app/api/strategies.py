from fastapi import APIRouter, HTTPException, Query, Request
from typing import Optional
from ..middleware.rate_limit import limiter
from ..middleware.validation import validate_symbol
from ..schemas.strategy import StrategiesResponse, StrategyCard, PayoffPoint
from ..services.alpaca_client import AlpacaClient, AlpacaError
from ..services.cache import cache_get, cache_set, run_coro, strategies_key, CACHE_TTL_OPTION_CHAIN
from ..services.indicator_engine import create_pro_engine
from ..services.strategy_engine import recommend_strategies, timeframe_for_dte
from ..config import settings

router = APIRouter(prefix="/options", tags=["strategies"])


def _card_dict(r: dict) -> dict:
    """Serializable StrategyCard payload (cache-safe, pre-pydantic)."""
    return {
        'name': r['name'], 'subtitle': r['subtitle'], 'is_bullish': r['is_bullish'],
        'max_profit': r['max_profit'], 'max_loss': r['max_loss'],
        'breakeven': r['breakeven'], 'return_on_risk': r['return_on_risk'],
        'payoff_curve': r.get('payoff_curve', []) or [],
    }


@router.get("/{symbol}/strategies", response_model=StrategiesResponse)
@limiter.limit(f"{settings.rate_limit_free}/minute")
async def get_strategies(request: Request, symbol: str, sentiment: str = Query('neutral'), strike: float = Query(...), expiration_gte: str = Query(...), expiration_lte: str = Query(...), dte: Optional[float] = Query(None), timeframe: Optional[str] = Query(None)):
    try:
        sym = validate_symbol(symbol)
        client = AlpacaClient()
        # Verdict horizon follows the contract's days-to-expiry (1h/1d/1w/1mo)
        # so a 90-day contract is not judged by a 1-day momentum snapshot.
        # An explicit `timeframe` overrides the DTE-matched default.
        tf = timeframe or timeframe_for_dte(dte)
        if tf not in ('1h', '1d', '1w', '1mo'):
            raise HTTPException(status_code=422, detail=f"unsupported timeframe: {tf}")
        key = strategies_key(sym, strike, expiration_gte, expiration_lte, tf)
        payload = run_coro(cache_get(key))
        if payload is None:
            # Market-data chain (bid/ask/last/IV/greeks), already cached 15 min
            # server-side — trading metadata has no reliable last_price.
            contracts = client.get_option_chain(
                underlying=sym,
                expiration_gte=expiration_gte,
                expiration_lte=expiration_lte,
            )
            chain = []
            for c in contracts:
                # Normalize the type enum/string to a lowercase 'call'/'put'.
                raw_type = str(c.get('type', 'call'))
                if '.' in raw_type:
                    raw_type = raw_type.rsplit('.', 1)[-1]
                t = raw_type.lower()
                if t not in ('call', 'put'):
                    continue
                bid = float(c.get('bid', 0) or 0)
                ask = float(c.get('ask', 0) or 0)
                last = float(c.get('last_price', 0) or 0)
                mid = ((bid + ask) / 2) if (bid and ask) else (last or bid or ask)
                if mid <= 0:
                    continue  # no usable quote: skip, it poisons spreads and _nearest
                chain.append({
                    'strike_price': float(c.get('strike_price', 0) or 0),
                    'type': t,
                    'last_price': mid,
                    'expiration_date': c.get('expiration_date'),
                })
            # Compute the technical indicators at the DTE-matched timeframe and
            # use them to drive strategy selection.
            df = client.get_stock_bars(sym, timeframe=tf)
            if df.empty and tf != '1d':
                df = client.get_stock_bars(sym)  # intraday miss -> daily fallback
            indicator_results = []
            if not df.empty:
                indicator_results = [r.to_dict() for r in create_pro_engine().compute_all(df)]
            recs = recommend_strategies(sentiment, strike, chain, indicator_results=indicator_results)
            payload = {
                'symbol': sym,
                'sentiment': sentiment,
                'timeframe': tf,
                'strategies': [_card_dict(r) for r in recs],
            }
            run_coro(cache_set(key, payload, ttl=CACHE_TTL_OPTION_CHAIN))
        return StrategiesResponse(
            symbol=payload['symbol'],
            sentiment=payload['sentiment'],
            timeframe=payload.get('timeframe', '1d'),
            strategies=[
                StrategyCard(
                    name=c['name'], subtitle=c['subtitle'], is_bullish=c['is_bullish'],
                    max_profit=c['max_profit'], max_loss=c['max_loss'], breakeven=c['breakeven'],
                    return_on_risk=c['return_on_risk'],
                    payoff_curve=[PayoffPoint(**p) for p in (c.get('payoff_curve') or [])],
                )
                for c in payload['strategies']
            ],
        )
    except AlpacaError as e:
        raise HTTPException(status_code=502, detail=str(e))