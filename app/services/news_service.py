from typing import TYPE_CHECKING

POSITIVE_WORDS = {"beat", "surge", "rally", "profit", "growth", "gain", "rise", "up", "bullish", "upgrade", "strong", "record", "high", "jump", "soar"}
NEGATIVE_WORDS = {"miss", "drop", "decline", "loss", "fall", "down", "bearish", "downgrade", "weak", "low", "plunge", "crash", "warning", "fear", "sell", "cut"}

def compute_sentiment(text: str) -> float:
    if not text:
        return 0.0
    words = set(text.lower().split())
    pos = len(words & POSITIVE_WORDS)
    neg = len(words & NEGATIVE_WORDS)
    total = pos + neg
    if total == 0:
        return 0.0
    return (pos - neg) / total

def get_news_sentiment(articles: list) -> dict:
    if not articles:
        return {"sentiment_score": 0.0, "article_count": 0, "summary": "No recent news."}
    scores = []
    for article in articles:
        headline = article.get("headline", "") or article.get("title", "") if isinstance(article, dict) else str(article)
        scores.append(compute_sentiment(headline))
    avg = sum(scores) / len(scores) if scores else 0.0
    if avg > 0.2:
        summary = "Recent news sentiment is positive."
    elif avg < -0.2:
        summary = "Recent news sentiment is negative."
    else:
        summary = "Recent news sentiment is neutral."
    return {
        "sentiment_score": round(avg, 3),
        "article_count": len(articles),
        "summary": summary,
    }
