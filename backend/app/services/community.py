"""Reddit and StockTwits community sentiment — no API keys required."""

import re
import time
import httpx

_cache: dict[str, tuple[dict, float]] = {}
_TTL = 1800  # 30 min

_ST_CACHE: dict[str, tuple[dict, float]] = {}
_ST_TTL = 900  # 15 min — StockTwits is real-time retail flow, shorter TTL

_BULL_WORDS = {
    "buy",
    "bullish",
    "long",
    "calls",
    "moon",
    "rally",
    "breakout",
    "strong",
    "upgrade",
    "beat",
    "growth",
    "outperform",
    "hold",
}
_BEAR_WORDS = {
    "sell",
    "bearish",
    "short",
    "puts",
    "crash",
    "dump",
    "weak",
    "downgrade",
    "miss",
    "cut",
    "loss",
    "underperform",
    "avoid",
}

# Tokens that flip polarity of the following keyword (window = 1 token).
_NEGATIONS = {
    "not",
    "no",
    "never",
    "isn't",
    "aren't",
    "wasn't",
    "weren't",
    "don't",
    "doesn't",
    "didn't",
    "won't",
    "wouldn't",
    "shouldn't",
    "couldn't",
    "cannot",
    "can't",
    "nothing",
    "without",
}

_TOKEN_RE = re.compile(r"[a-z']+")


def score_text_polarity(
    text: str,
    bull_set: set[str] = _BULL_WORDS,
    bear_set: set[str] = _BEAR_WORDS,
) -> tuple[int, int]:
    """Count bull / bear keyword hits with 1-token negation lookback.

    "not bullish" → bear+1 (not bull+1). "no growth" → bear+1.
    Matches whole tokens only — "growth" no longer fires inside "regrowth".
    """
    tokens = _TOKEN_RE.findall(text.lower())
    bull = bear = 0
    for i, tok in enumerate(tokens):
        flip = i > 0 and tokens[i - 1] in _NEGATIONS
        if tok in bull_set:
            if flip:
                bear += 1
            else:
                bull += 1
        elif tok in bear_set:
            if flip:
                bull += 1
            else:
                bear += 1
    return bull, bear


_HEADERS = {"User-Agent": "aifolimizer/1.0 community-signal (personal finance research)"}
_SUBREDDITS = ["stocks", "investing", "canadianinvestor", "wallstreetbets"]


def get_reddit_sentiment(symbol: str) -> dict:
    cached = _cache.get(symbol)
    if cached and time.time() - cached[1] < _TTL:
        return cached[0]

    ticker = symbol.upper().replace(".TO", "")
    posts: list[str] = []

    for sub in _SUBREDDITS[:3]:
        url = f"https://www.reddit.com/r/{sub}/search.json?q={ticker}&sort=top&t=week&limit=10&restrict_sr=1"
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
    bull = bear = 0
    for post in posts:
        b, br = score_text_polarity(post)
        bull += b
        bear += br
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


def get_stocktwits_sentiment(symbol: str) -> dict:
    """StockTwits public stream — retail trader sentiment, no API key required.

    Returns bullish/bearish message counts, community_score (0=all bear, 100=all bull),
    and sample messages. TSX tickers strip .TO suffix (StockTwits uses bare ticker).
    """
    cached = _ST_CACHE.get(symbol)
    if cached and time.time() - cached[1] < _ST_TTL:
        return cached[0]

    ticker = symbol.upper().replace(".TO", "").replace(".TSX", "")
    url = f"https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"

    try:
        resp = httpx.get(url, timeout=5.0, headers=_HEADERS, follow_redirects=True)
        if resp.status_code != 200:
            result = _st_empty(symbol)
            _ST_CACHE[symbol] = (result, time.time())
            return result

        data = resp.json()
        messages = data.get("messages") or []

        bull = 0
        bear = 0
        samples: list[str] = []

        for msg in messages[:30]:
            sentiment = (msg.get("entities") or {}).get("sentiment") or {}
            label = (sentiment.get("basic") or "").lower()
            if label == "bullish":
                bull += 1
            elif label == "bearish":
                bear += 1
            body = (msg.get("body") or "")[:100]
            if body and len(samples) < 3:
                samples.append(body)

        total = bull + bear or 1
        result = {
            "symbol": symbol,
            "community_score": round(bull / total * 100, 1),
            "bull_count": bull,
            "bear_count": bear,
            "message_count": len(messages),
            "sample_messages": samples,
            "source": "stocktwits_public",
        }
    except Exception:
        result = _st_empty(symbol)

    _ST_CACHE[symbol] = (result, time.time())
    return result


def _st_empty(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "community_score": None,
        "bull_count": 0,
        "bear_count": 0,
        "message_count": 0,
        "sample_messages": [],
        "source": "stocktwits_public",
    }
