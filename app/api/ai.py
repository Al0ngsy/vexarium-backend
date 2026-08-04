from datetime import datetime, timezone
from fastapi import APIRouter, Request
from ..middleware.rate_limit import limiter
from ..middleware.validation import validate_symbol
from ..schemas.analysis import AnalysisRequest
from ..services.alpaca_client import AlpacaClient
from ..services.indicator_engine import create_default_engine
from ..services.verdicts import aggregate
from ..services.ai_analyzer import build_prompt, analyze as llm_analyze
from ..config import settings
import logging

router = APIRouter(prefix="/analysis", tags=["analysis"])
logger = logging.getLogger("vexarium.api.ai")


@router.post("/ai")
@limiter.limit(f"{settings.rate_limit_free}/minute")
async def ai_analysis(request: Request, body: AnalysisRequest):
    sym = validate_symbol(body.symbol)
    try:
        client = AlpacaClient()
        df = client.get_stock_bars(sym)
        if df.empty:
            return {"symbol": sym, "analysis": "No data available.", "model": settings.llm_model}
        engine = create_default_engine()
        indicator_results = [r.to_dict() for r in engine.compute_all(df)]
        overall = aggregate(indicator_results)
        prompt = build_prompt(indicator_results, overall)
        text = await llm_analyze(prompt)
        return {
            "symbol": sym,
            "analysis": text,
            "model": settings.llm_model,
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception:
        logger.error("AI analysis failed", exc_info=True)
        return {"symbol": sym, "analysis": "AI analysis temporarily unavailable.", "model": settings.llm_model}
