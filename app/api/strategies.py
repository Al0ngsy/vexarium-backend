from fastapi import APIRouter, HTTPException, Query, Request
from ..middleware.rate_limit import limiter
from ..middleware.validation import validate_symbol
from ..schemas.strategy import StrategiesResponse, StrategyCard, PayoffPoint
from ..services.alpaca_client import AlpacaClient, AlpacaError
from ..services.indicator_engine import create_pro_engine
from ..services.strategy_engine import recommend_strategies, compute_strategy
from ..config import settings

router = APIRouter(prefix="/options", tags=["strategies"])

@router.get("/{symbol}/strategies", response_model=StrategiesResponse)
@limiter.limit(f"{settings.rate_limit_free}/minute")
async def get_strategies(request: Request, symbol: str, sentiment: str = Query('neutral'), strike: float = Query(...), expiration_gte: str = Query(...), expiration_lte: str = Query(...)):
    try:
        sym = validate_symbol(symbol)
        client = AlpacaClient()
        contracts = client.get_option_contracts(sym, expiration_gte, expiration_lte)
        chain = []
        for c in contracts:
            # Normalize the type enum/string to a lowercase 'call'/'put'.
            raw_type = c.get('type', 'call')
            t = str(raw_type)
            if '.' in t:
                t = t.rsplit('.', 1)[-1]
            t = t.lower()
            if t not in ('call', 'put'):
                continue
            chain.append({
                'strike_price': float(c.get('strike_price', 0)),
                'type': t,
                'last_price': float(c.get('last_price', 0) or 0),
            })
        # Compute the technical indicators and use them to drive strategy selection.
        df = client.get_stock_bars(sym)
        indicator_results = []
        if not df.empty:
            indicator_results = [r.to_dict() for r in create_pro_engine().compute_all(df)]
        recs = recommend_strategies(sentiment, strike, chain, indicator_results=indicator_results)
        cards = []
        for r in recs:
            curve = r.get('payoff_curve', []) or []
            cards.append(StrategyCard(
                name=r['name'], subtitle=r['subtitle'], is_bullish=r['is_bullish'],
                max_profit=r['max_profit'], max_loss=r['max_loss'], breakeven=r['breakeven'],
                return_on_risk=r['return_on_risk'],
                payoff_curve=[PayoffPoint(**p) for p in curve]
            ))
        return StrategiesResponse(symbol=sym, sentiment=sentiment, strategies=cards)
    except AlpacaError as e:
        raise HTTPException(status_code=502, detail=str(e))
