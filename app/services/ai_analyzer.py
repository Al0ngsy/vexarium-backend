import json
import httpx
from typing import Optional
from ..config import settings

SYSTEM_PROMPT = """You are a senior market analyst for VEXARIUM, a pre-trade research tool. Your job is to give a beginner-friendly but professionally deep briefing that helps someone decide whether to buy, hold, or sell a stock/ETF.

Structure your response with these sections (use markdown headers):
## Summary
One or two sentences: the verdict and the single most important reason.

## The Setup
- Where price sits in its 52-week range and vs key moving averages.
- Trend quality (ADX), momentum (RSI/MACD/Stochastic), volatility (ATR/Bollinger).
- Volume behavior (OBV) — is the move confirmed by volume?

## Key Levels
- Support: the nearest level(s) where buyers have stepped in.
- Resistance: the nearest level(s) where sellers have capped price.
- Give concrete prices, not just percentages.

## Risks & What to Watch
- The biggest risk to this setup right now (e.g. overbought, weak volume, news event, earnings date).
- 1-2 specific things to watch that would change the thesis.

Rules:
- Reference specific indicator values and prices — never vague.
- If fundamentals are provided (P/E, margins, growth, market cap), connect them to the technical picture: is the valuation stretched relative to growth?
- If news is provided, say how it supports or threatens the setup.
- If options data is provided, comment on Greeks and time decay.
- Always end your response with EXACTLY this footer (blank line, dash line, bold disclaimer, dash line):

----------------------------------------
**This is not financial advice. AI can make/will make mistakes.**
----------------------------------------
- Be direct, clinical, and concrete. No hedging filler."""


def _key_levels(indicator_results: list, market_data: Optional[dict]) -> dict:
    """Derive concrete support/resistance levels from the indicators + market data."""
    levels: dict = {"support": [], "resistance": []}
    price = (market_data or {}).get("price")
    if not price:
        return levels
    # Bollinger bands give immediate support/resistance.
    for r in indicator_results:
        name = (r.get("name") or "").upper()
        val = r.get("value")
        if "BOLLINGER" in name and isinstance(val, dict):
            lower = val.get("lower")
            upper = val.get("upper")
            if isinstance(lower, (int, float)):
                levels["support"].append(round(float(lower), 2))
            if isinstance(upper, (int, float)):
                levels["resistance"].append(round(float(upper), 2))
        # SMA/EMA levels.
        if ("SMA" in name or "EMA" in name) and isinstance(val, dict):
            for k in ("sma50", "ema200", "ema20", "ema50"):
                v = val.get(k)
                if isinstance(v, (int, float)):
                    lvl = round(float(v), 2)
                    if lvl < price:
                        levels["support"].append(lvl)
                    else:
                        levels["resistance"].append(lvl)
    # 52-week range as outer bounds.
    hi = (market_data or {}).get("high_52w")
    lo = (market_data or {}).get("low_52w")
    if hi:
        levels["resistance"].append(round(float(hi), 2))
    if lo:
        levels["support"].append(round(float(lo), 2))
    # Dedupe + sort, keep nearest 3 each side.
    levels["support"] = sorted(set(levels["support"]), reverse=True)[:3]
    levels["resistance"] = sorted(set(levels["resistance"]))[:3]
    return levels


def build_prompt(indicator_results: list, overall_verdict: dict,
                 options_data: Optional[dict] = None, news_sentiment: Optional[dict] = None,
                 news_articles: Optional[list] = None,
                 market_data: Optional[dict] = None,
                 company_info: Optional[dict] = None) -> str:
    context = {
        "overall_verdict": overall_verdict.get("overall_verdict", "unknown"),
        "indicators": [
            {"name": r.get("name"), "verdict": r.get("verdict"), "value": r.get("value")}
            for r in indicator_results
        ],
    }
    if market_data:
        context["market"] = market_data
        # Concrete support/resistance derived from the data.
        context["key_levels"] = _key_levels(indicator_results, market_data)
    if company_info:
        # Only include fields that are actually populated.
        filled = {k: v for k, v in company_info.items()
                  if v not in (None, "", 0) and k != "symbol"}
        if filled:
            context["company_fundamentals"] = filled
    if options_data:
        context["options"] = options_data
    if news_sentiment:
        context["news_sentiment"] = news_sentiment
    if news_articles:
        # Surface the actual headlines/summaries so the model can reason about them,
        # not just the aggregate sentiment score.
        context["news_articles"] = [
            {
                "headline": a.get("headline") or a.get("title", ""),
                "source": a.get("source", ""),
                "url": a.get("url", ""),
                "summary": (a.get("summary") or "")[:400],
                "sentiment": a.get("sentiment"),
            }
            for a in news_articles[:8]
        ]
    return f"{SYSTEM_PROMPT}\n\nContext:\n{json.dumps(context, indent=2)}\n\nProvide your analysis:"


async def analyze(prompt: str, skip_ai: bool = False) -> str:
    if skip_ai or not settings.llm_api_key:
        return "AI analysis unavailable. Review the technical indicators manually or come back later."
    # Single model from the OpenCode Go subscription; a rate limit or outage
    # surfaces as "temporarily unavailable" (no free-tier fallbacks anymore).
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{settings.llm_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                json={
                    "model": settings.llm_model,
                    "messages": [{"role": "user", "content": prompt}],
                    # Generous budget: this model spends tokens on internal
                    # reasoning BEFORE producing content. Too small a budget
                    # (e.g. 300) yields empty content. 2000 was cutting deep
                    # analyses mid-sentence; 8192 lets the full briefing through.
                    "max_tokens": 8192,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            if content and content.strip():
                return content
    except Exception:
        pass
    return "AI analysis temporarily unavailable. Review the technical indicators manually or come back later."


async def analyze_stream(prompt: str, skip_ai: bool = False):
    """Stream the LLM answer token-by-token (OpenAI-compatible SSE).

    Yields successive content strings; the full answer is the concatenation.
    Skips the model's internal `reasoning_content` deltas (thinking) — those
    are not shown to the user. Returns the full text when exhausted (empty
    string on failure, like a failed non-streaming call).
    """
    if skip_ai or not settings.llm_api_key:
        return
    # Mid-stream errors propagate — a partial answer must not be silently
    # dropped or swapped for another model's continuation (the INTC bug: a
    # truncated briefing was cached for 24h).
    started = False
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            async with client.stream(
                "POST",
                f"{settings.llm_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                json={
                    "model": settings.llm_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": True,
                    "max_tokens": 8192,
                },
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except Exception:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content")
                    # Skip reasoning_content (the model's hidden thinking) —
                    # only stream the actual answer.
                    if content:
                        started = True
                        yield content
        return
    except Exception:
        if started:
            raise
    # Request-time failure (rate limit, outage) — caller renders the
    # unavailable message.
