POSITIVE_WORDS = {"beat", "surge", "rally", "profit", "growth", "gain", "rise", "up", "bullish", "upgrade", "strong", "record", "high", "jump", "soar"}
NEGATIVE_WORDS = {"miss", "drop", "decline", "loss", "fall", "down", "bearish", "downgrade", "weak", "low", "plunge", "crash", "warning", "fear", "sell", "cut"}

import difflib
from datetime import datetime

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
    """Fetch recent news across sources. Returns (summary, article_list).

    Sources: Alpaca symbol news + Google News RSS (via `client.get_news`),
    then Finnhub `/company-news` as a second outlet. The merged feed is
    deduped so one story over-reported by several sources can't skew either
    the list or the aggregate sentiment. Newest first. Never raises.
    """
    try:
        articles = client.get_news(symbol, limit=limit)
        try:
            from .finnhub import get_company_news

            articles += get_company_news(symbol, limit=limit)
        except Exception:
            pass  # extra source optional; base feed still works
        articles = sorted(dedupe_articles(articles), key=_ts, reverse=True)
        return get_news_sentiment(articles), add_article_scores(articles)
    except Exception:
        return (
            {"sentiment_score": 0.0, "article_count": 0, "summary": "News unavailable."},
            [],
        )


def dedupe_articles(articles: list, similar_ratio: float = 0.85) -> list:
    """Drop duplicate / same-day near-duplicate articles, first occurrence wins.

    Guards the sentiment from one story over-reported by several outlets:
    identical headline OR identical URL always dedupe; a high headline
    similarity (>= ratio) only dedupes on the same calendar day so real
    follow-ups on later days survive. ponytail: O(n²) pairwise scan — fine
    for the ~20-item merged feed; swap to a token-set index if it grows.
    """
    def _norm(h: str) -> str:
        return " ".join(h.lower().split())

    def _day(a: dict) -> str:
        t = a.get("created_at")
        if t is None:
            return ""
        if hasattr(t, "isoformat"):  # datetime object (Alpaca returns these)
            return t.isoformat()[:10]
        if isinstance(t, str):
            return t[:10]
        return str(t)[:10]

    out: list = []
    for a in articles:
        if not isinstance(a, dict):
            continue
        head = _norm(a.get("headline") or "")
        url = (a.get("url") or "").strip().lower()
        if not head:
            continue
        dup = False
        for o in out:
            oh = _norm(o.get("headline") or "")
            if head == oh or (url and url == (o.get("url") or "").strip().lower()):
                dup = True
                break
            if (
                _day(a)
                and _day(a) == _day(o)
                and difflib.SequenceMatcher(None, head, oh).ratio() >= similar_ratio
            ):
                dup = True
                break
        if not dup:
            out.append(a)
    return out


def _ts(a: dict) -> float:
    """Recency key in seconds (0 for unknown timestamps)."""
    t = a.get("created_at")
    if t is not None and hasattr(t, "timestamp"):  # datetime object
        return float(t.timestamp())
    if isinstance(t, (int, float)):
        return float(t)
    if isinstance(t, str):
        try:
            return datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
        except Exception:
            try:
                return float(t)
            except Exception:
                return 0.0
    return 0.0
