POSITIVE_WORDS = {"beat", "surge", "rally", "profit", "growth", "gain", "rise", "up", "bullish", "upgrade", "strong", "record", "high", "jump", "soar"}
NEGATIVE_WORDS = {"miss", "drop", "decline", "loss", "fall", "down", "bearish", "downgrade", "weak", "low", "plunge", "crash", "warning", "fear", "sell", "cut"}

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

    _vader = SentimentIntensityAnalyzer()
except Exception:  # pragma: no cover - dependency missing
    _vader = None


def _lexicon_score(text: str) -> float:
    """Crude word-count fallback, used only if vaderSentiment isn't installed."""
    words = set(text.lower().split())
    pos = len(words & POSITIVE_WORDS)
    neg = len(words & NEGATIVE_WORDS)
    total = pos + neg
    if total == 0:
        return 0.0
    return (pos - neg) / total


def compute_sentiment(text: str) -> float:
    if not text:
        return 0.0
    if _vader is not None:
        # VADER compound in [-1, 1]: dictionary + negation/intensifier/caps
        # heuristics, far better coverage than the wordlist. NOT market-aware:
        # a plain "buy" call still reads neutral.
        return _vader.polarity_scores(text)["compound"]
    return _lexicon_score(text)

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


def add_article_scores(articles: list) -> list:
    """Attach each article's own headline sentiment score (mutates in place).

    The per-article score is what the aggregate is averaged from; surfacing it
    per row makes the widget's sentiment concrete instead of a single number.
    """
    for a in articles:
        if isinstance(a, dict):
            a["sentiment"] = compute_sentiment(a.get("headline") or a.get("title") or "")
    return articles


def fetch_news(client, symbol: str, limit: int = 10) -> tuple[dict, list]:
    """Fetch recent news. Returns (sentiment_summary, article_list). Never raises."""
    try:
        articles = client.get_news(symbol, limit=limit)
        return get_news_sentiment(articles), add_article_scores(articles)
    except Exception:
        return (
            {"sentiment_score": 0.0, "article_count": 0, "summary": "News unavailable."},
            [],
        )
