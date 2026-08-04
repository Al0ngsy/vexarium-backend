import json
import httpx
from typing import Optional
from ..config import settings

SYSTEM_PROMPT = """You are a trading analyst assistant for VEXARIUM. Analyze the provided technical indicators, options data, and news sentiment to provide a natural-language recommendation.

Rules:
- State whether this is a good time to buy, hold, or sell.
- Reference specific indicator values in your analysis.
- If options data is provided, comment on Greeks and time decay.
- If news sentiment is provided, factor it into your recommendation.
- Always include the disclaimer: "This is not financial advice."
- Keep your response under 200 words.
- Be direct and clinical in tone."""

def build_prompt(indicator_results: list, overall_verdict: dict,
                 options_data: Optional[dict] = None, news_sentiment: Optional[dict] = None,
                 news_articles: Optional[list] = None) -> str:
    context = {
        "overall_verdict": overall_verdict.get("overall_verdict", "unknown"),
        "indicators": [
            {"name": r.get("name"), "verdict": r.get("verdict"), "value": r.get("value")}
            for r in indicator_results
        ],
    }
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
            }
            for a in news_articles[:8]
        ]
    return f"{SYSTEM_PROMPT}\n\nContext:\n{json.dumps(context, indent=2)}\n\nProvide your analysis:"

async def analyze(prompt: str, skip_ai: bool = False) -> str:
    if skip_ai or not settings.llm_api_key:
        return "AI analysis unavailable. Review the technical indicators above. This is not financial advice."
    # The provider occasionally returns an empty completion; retry once.
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{settings.llm_base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                    json={
                        "model": settings.llm_model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 300,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                if content and content.strip():
                    return content
                # empty completion -> try again
        except Exception:
            if attempt == 1:
                return "AI analysis temporarily unavailable. Review the technical indicators above. This is not financial advice."
    return "AI analysis temporarily unavailable. Review the technical indicators above. This is not financial advice."
