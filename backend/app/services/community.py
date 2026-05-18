"""Reddit public JSON API community sentiment — no API key required."""

import time
import httpx

_cache: dict[str, tuple[dict, float]] = {}
_TTL = 1800  # 30 min

_BULL_WORDS = {
    "buy", "bullish", "long", "calls", "moon", "rally", "breakout",
    "strong", "upgrade", "beat", "growth", "outperform", "hold",
}
_BEAR_WORDS = {
    "sell", "bearish", "short", "puts", "crash", "dump", "weak",
    "downgrade", "miss", "cut", "loss", "underperform", "avoid",
}

_HEADERS = {"User-Agent": "aifolimizer/1.0 community-signal (personal finance research)"}
_SUBREDDITS = ["stocks", "investing", "canadianinvestor", "wallstreetbets"]


def get_reddit_sentiment(symbol: str) -> dict:
    cached = _cache.get(symbol)
    if cached and time.time() - cached[1] < _TTL:
        return cached[0]

    ticker = symbol.upper().replace(".TO", "")
    posts: list[str] = []

    for sub in _SUBREDDITS[:3]:
        url = (
            f"https://www.reddit.com/r/{sub}/search.json"
            f"?q={ticker}&sort=top&t=week&limit=10&restrict_sr=1"
        )
        try:
            resp = httpx.get(url, headers=_HEADERS, timeout=5.0, follow_redirects=True)
            if resp.status_code != 200:
                continue
            data = resp.json()
            for child in data.get("data", {}).get("children", []):
                title = child.get("data", {}).get("title", "")
                if ticker in title.upper():
                    posts.append(title)
        except Exception:
            continue

    result = _score(symbol, ticker, posts) if posts else _empty(symbol)
    _cache[symbol] = (result, time.time())
    return result


def _score(symbol: str, ticker: str, posts: list[str]) -> dict:
    bull = sum(1 for p in posts for w in _BULL_WORDS if w in p.lower())
    bear = sum(1 for p in posts for w in _BEAR_WORDS if w in p.lower())
    total = bull + bear or 1
    return {
        "symbol": symbol,
        "community_score": round(bull / total * 100, 1),  # 0=all bear, 50=neutral, 100=all bull
        "bull_signals": bull,
        "bear_signals": bear,
        "post_count": len(posts),
        "sample_posts": posts[:3],
        "source": "reddit_public",
        "subreddits_searched": _SUBREDDITS[:3],
    }


def _empty(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "community_score": None,
        "bull_signals": 0,
        "bear_signals": 0,
        "post_count": 0,
        "sample_posts": [],
        "source": "reddit_public",
        "subreddits_searched": _SUBREDDITS[:3],
    }
