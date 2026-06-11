"""Management-commentary language signal for post-earnings drift.

Scores earnings-call / guidance language with a management-specific bull/bear
lexicon (negation-aware, via community.score_text_polarity). A probabilistic
read on the 48h-to-multi-week drift direction - NOT a guarantee. Pairs with
EPS-surprise direction (get_earnings_results / pead-tracker).

Source chain (all free):
  1. Alpha Vantage EARNINGS_CALL_TRANSCRIPT (ALPHA_VANTAGE_KEY, 25 req/day).
  2. SEC EDGAR 8-K Item 2.02 press-release text (no key; US-listed only).

Reuses score_text_polarity (no new NLP dep) and the existing EDGAR + fetch_json
plumbing (credential-safe error handling).
"""

from __future__ import annotations

import os
import re
import time

import httpx

from app.security import get_logger
from app.services import cache_layer
from app.services.community import score_text_polarity
from app.services.data_sources.base import SourceUnavailable, fetch_json, to_float

_LOG = get_logger("aifolimizer.services.earnings_commentary")

_CACHE_NS = "earnings_commentary"
_TTL_S = int(os.getenv("COMMENTARY_CACHE_TTL_S", "86400") or 86400)  # transcripts immutable
_HTTP_TIMEOUT = 15.0
_HDR = {"User-Agent": "aifolimizer/1.0 (open-source portfolio analytics)"}

# Management-commentary lexicon - distinct from the retail-chatter lexicon in
# community.py. These are the words executives use when steering expectations.
_MGMT_BULL = {
    "raised",
    "raising",
    "accelerating",
    "accelerate",
    "momentum",
    "record",
    "expanding",
    "expansion",
    "confident",
    "confidence",
    "strong",
    "robust",
    "exceeding",
    "exceeded",
    "ahead",
    "reiterate",
    "reiterating",
    "outperform",
    "demand",
    "growth",
    "upside",
    "tailwind",
    "tailwinds",
    "beat",
    "improving",
    "optimistic",
    "healthy",
    "resilient",
}
_MGMT_BEAR = {
    "headwinds",
    "headwind",
    "cautious",
    "caution",
    "pressure",
    "challenging",
    "softening",
    "soft",
    "weakness",
    "weak",
    "delayed",
    "delay",
    "uncertainty",
    "uncertain",
    "below",
    "shortfall",
    "restructuring",
    "lowered",
    "lowering",
    "cut",
    "cutting",
    "decline",
    "declining",
    "miss",
    "missed",
    "slowdown",
    "deteriorating",
    "impairment",
}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_MAX_CHARS = 40_000  # cap text fed to the scorer


def score_commentary(text: str) -> dict:
    """Score management language → tone 0-1 + signal. Pure, deterministic.

    mgmt_tone = bull / (bull + bear). None when no lexicon hits (uninformative).
    signal: positive (>=0.6) | negative (<=0.4) | neutral.
    """
    bull, bear = score_text_polarity(text, bull_set=_MGMT_BULL, bear_set=_MGMT_BEAR)
    total = bull + bear
    n_words = len(re.findall(r"[a-zA-Z']+", text))
    if total == 0:
        return {"mgmt_tone": None, "mgmt_tone_signal": "neutral", "n_words": n_words, "bull_hits": 0, "bear_hits": 0}
    tone = round(bull / total, 3)
    signal = "positive" if tone >= 0.6 else "negative" if tone <= 0.4 else "neutral"
    return {"mgmt_tone": tone, "mgmt_tone_signal": signal, "n_words": n_words, "bull_hits": bull, "bear_hits": bear}


def _clean(text: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", text or "")).strip()[:_MAX_CHARS]


def _recent_quarters(n: int = 4) -> list[str]:
    """Last n fiscal quarters as Alpha-Vantage 'YYYYQ#' labels, newest first."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    y, q = now.year, (now.month - 1) // 3 + 1
    out: list[str] = []
    for _ in range(n):
        out.append(f"{y}Q{q}")
        q -= 1
        if q == 0:
            q, y = 4, y - 1
    return out


def _av_sentiment_mean(segs: list[dict]) -> float | None:
    """Mean of Alpha Vantage's per-segment LLM sentiment (their model, ~ -1..1)."""
    vals: list[float] = []
    for s in segs:
        v = to_float(s.get("sentiment"))
        if v is not None:
            vals.append(v)
    return round(sum(vals) / len(vals), 4) if vals else None


def _fetch_alphavantage_quarters(ticker: str, n: int = 4) -> list[dict]:
    """Recent earnings-call transcripts via Alpha Vantage (FREE, ALPHA_VANTAGE_KEY).

    Returns up to n quarters (newest first), each: {quarter, text, av_sentiment}.
    av_sentiment is AV's own per-segment LLM sentiment averaged - a more accurate
    primary signal than the lexicon, which only cross-checks / covers EDGAR.
    AV requires an explicit quarter, so we walk recent quarters (allowing gaps).
    """
    key = os.environ.get("ALPHA_VANTAGE_KEY", "").strip()
    if not key:
        return []
    out: list[dict] = []
    for quarter in _recent_quarters(n + 2):
        if len(out) >= n:
            break
        try:
            data = fetch_json(
                "https://www.alphavantage.co/query",
                name="transcript:alphavantage",
                symbol=ticker.upper(),
                params={
                    "function": "EARNINGS_CALL_TRANSCRIPT",
                    "symbol": ticker.upper(),
                    "quarter": quarter,
                    "apikey": key,
                },
                timeout=_HTTP_TIMEOUT,
                default={},
            )
        except SourceUnavailable as e:
            _LOG.debug(f"[commentary] alphavantage {ticker} {quarter}: {e}")
            continue
        segs = data.get("transcript") if isinstance(data, dict) else None
        if isinstance(segs, list) and segs:
            text = _clean(" ".join(str(s.get("content", "")) for s in segs))
            if text:
                out.append({"quarter": quarter, "text": text, "av_sentiment": _av_sentiment_mean(segs)})
    return out


# Trend thresholds - uncalibrated starting points (no labeled outcome set yet).
# AV sentiment spans ~-1..1; lexicon tone clusters high (prepared remarks are
# promotional), so its meaningful moves are smaller.
_AV_TREND_THR = 0.05
_TONE_TREND_THR = 0.03


def _relativize(scored: list[dict]) -> dict:
    """Turn per-quarter scores (newest first) into a RELATIVE trend.

    Absolute tone is near-useless (mgmt always upbeat); the predictive signal is
    latest vs prior-quarter baseline. Prefers AV sentiment (better model); falls
    back to lexicon tone. scored item: {quarter, tone, av_sentiment}.
    """
    quarters = [s.get("quarter") for s in scored]
    latest = scored[0] if scored else {}
    priors = scored[1:]

    def _stats(key: str):
        cur = latest.get(key)
        prior_vals = [s.get(key) for s in priors if s.get(key) is not None]
        if cur is None or not prior_vals:
            return None, None
        baseline = round(sum(prior_vals) / len(prior_vals), 4)
        return baseline, round(cur - baseline, 4)

    tone_baseline, tone_delta = _stats("tone")
    av_baseline, av_delta = _stats("av_sentiment")

    if av_delta is not None:
        primary, delta, thr = "av_sentiment", av_delta, _AV_TREND_THR
    elif tone_delta is not None:
        primary, delta, thr = "lexicon_tone", tone_delta, _TONE_TREND_THR
    else:
        return {
            "relative": False,
            "mgmt_tone_trend": "insufficient_history",
            "primary_metric": None,
            "quarters_analyzed": quarters,
            "tone_baseline": tone_baseline,
            "tone_delta": tone_delta,
            "av_sentiment_baseline": av_baseline,
            "av_sentiment_delta": av_delta,
        }

    trend = "improving" if delta >= thr else "deteriorating" if delta <= -thr else "stable"
    return {
        "relative": True,
        "mgmt_tone_trend": trend,
        "primary_metric": primary,
        "quarters_analyzed": quarters,
        "tone_baseline": tone_baseline,
        "tone_delta": tone_delta,
        "av_sentiment_baseline": av_baseline,
        "av_sentiment_delta": av_delta,
    }


_EARNINGS_DESC_RE = re.compile(r"result|earning|financial condition|2\.02", re.I)


def _pick_earnings_filing(filings: list[dict]) -> dict | None:
    """Prefer the 8-K whose description signals an earnings release; else newest."""
    if not filings:
        return None
    for f in filings:
        if _EARNINGS_DESC_RE.search(str(f.get("description") or "")):
            return f
    return filings[0]


def _fetch_edgar_text(ticker: str) -> str | None:
    """Text of the earnings 8-K primary document (Item 2.02), else the latest 8-K."""
    from app.services import edgar_filings

    res = edgar_filings.recent_filings(ticker, forms=["8-K"], limit=8)
    pick = _pick_earnings_filing(res.get("filings") or [])
    url = pick.get("url") if pick else None
    if not url:
        return None
    try:
        resp = httpx.get(url, headers=_HDR, timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
        return _clean(resp.text) or None
    except Exception as e:
        _LOG.warning(f"[commentary] edgar {ticker}: {type(e).__name__}: {e}")
        return None


def get_commentary_tone(ticker: str) -> dict:
    """Management-commentary signal for a ticker - RELATIVE trend, most accurate.

    Source chain (free): Alpha Vantage multi-quarter transcript -> EDGAR 8-K.
    The headline signal is `mgmt_tone_trend` (improving/deteriorating/stable vs
    prior quarters) - absolute tone is unreliable because prepared remarks are
    always promotional. `primary_metric` says whether the trend is driven by AV's
    LLM sentiment (preferred) or the lexicon (EDGAR-only). `mgmt_tone_signal`
    (absolute positive/negative/neutral) is kept for back-compat.

    source: 'alpha_vantage' | 'edgar_8k' | None. US-listed only. Cached 24h.
    """
    sym = ticker.strip().upper()
    cached = cache_layer.cache_get(_CACHE_NS, sym)
    if cached:
        return cached

    source = None
    quarters: list[dict] = []

    av = _fetch_alphavantage_quarters(sym, 4)
    if av:
        source = "alpha_vantage"
        quarters = [
            {
                "quarter": q["quarter"],
                "av_sentiment": q.get("av_sentiment"),
                **{"_text": q["text"]},
                **{"tone": score_commentary(q["text"])["mgmt_tone"]},
            }
            for q in av
        ]
    else:
        edgar_text = _fetch_edgar_text(sym)
        if edgar_text:
            source = "edgar_8k"
            quarters = [
                {
                    "quarter": "latest_8k",
                    "av_sentiment": None,
                    "_text": edgar_text,
                    "tone": score_commentary(edgar_text)["mgmt_tone"],
                }
            ]

    if not quarters:
        return {
            "ticker": sym,
            "source": None,
            "mgmt_tone": None,
            "mgmt_tone_signal": "neutral",
            "mgmt_tone_trend": "insufficient_history",
            "relative": False,
            "n_words": 0,
            "note": "No transcript (set ALPHA_VANTAGE_KEY for the free transcript source) and no 8-K text; US-listed only.",
            "as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    latest_score = score_commentary(quarters[0]["_text"])
    rel = _relativize([{k: q[k] for k in ("quarter", "tone", "av_sentiment")} for q in quarters])

    result = {
        "ticker": sym,
        "source": source,
        "as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "av_sentiment": quarters[0].get("av_sentiment"),
        **latest_score,
        **rel,
    }
    cache_layer.cache_set(_CACHE_NS, sym, result, _TTL_S)
    return result
