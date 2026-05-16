"""Positioning / crowding signals.

Goldman / BlackRock 2025 research: when retail + quant funds use the same AI
signals, they pile into the same names. Late entries into already-consensus
trades have negative expected alpha. This service surfaces positioning data
so skills can flag "edge already priced" before recommending an add.

Signals (all free, no API key):
- institutional_ownership_pct  (yfinance heldPercentInstitutions)
- short_pct_float              (yfinance shortPercentOfFloat)
- insider_ownership_pct        (yfinance heldPercentInsiders)
- analyst_count                (yfinance numberOfAnalystOpinions)
- headline_velocity            (news count last 7d vs 30d, ratio > 1 = surge)
- crowding_score               (0-100, higher = more crowded)
- crowding_label               (consensus | neutral | contrarian)
- contrarian_flag              (True if score <= 30)
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import yfinance as yf

_cache: dict[str, tuple[dict, float]] = {}
_CACHE_TTL = 6 * 3600
_MAX_WORKERS = 8

# Crowding score weights — empirical, not derived. Tune as data accumulates.
_W_INST = 0.35   # high inst ownership = consensus institutional pile-in
_W_SHORT = 0.20  # low short interest = bears already covered = consensus long
_W_ANALYST = 0.20  # high analyst coverage = consensus name
_W_NEWS = 0.25   # surging headlines = retail attention surge


def _norm_inst(pct: float | None) -> float:
    """Institutional ownership 0-1. <40% rare/contrarian, >85% consensus crowded."""
    if pct is None:
        return 0.5
    pct = max(0.0, min(pct, 1.0))
    if pct <= 0.40:
        return 0.0
    if pct >= 0.85:
        return 1.0
    return (pct - 0.40) / 0.45


def _norm_short(short_pct: float | None) -> float:
    """Low short = consensus long. >10% short = contrarian/contested."""
    if short_pct is None:
        return 0.5
    short_pct = max(0.0, min(short_pct, 1.0))
    if short_pct >= 0.10:
        return 0.0
    if short_pct <= 0.01:
        return 1.0
    return 1.0 - (short_pct - 0.01) / 0.09


def _norm_analyst(count: int | None) -> float:
    """0 analysts = contrarian; 25+ = consensus name."""
    if count is None or count <= 0:
        return 0.2
    if count >= 25:
        return 1.0
    return count / 25.0


def _norm_news_velocity(ratio: float | None) -> float:
    """ratio = (headlines_7d / 7) / (headlines_30d / 30). >2 = surge."""
    if ratio is None:
        return 0.3
    if ratio >= 2.5:
        return 1.0
    if ratio <= 0.5:
        return 0.0
    return (ratio - 0.5) / 2.0


def _news_velocity(symbol: str) -> tuple[float | None, int, int]:
    """Returns (ratio_7d_per_day_vs_30d_per_day, count_7d, count_30d)."""
    try:
        articles = yf.Ticker(symbol).news or []
        now = datetime.now(timezone.utc)
        cutoff_7 = now - timedelta(days=7)
        cutoff_30 = now - timedelta(days=30)
        c7 = 0
        c30 = 0
        for a in articles:
            content = a.get("content") if isinstance(a, dict) else None
            pub_str = ""
            ts = None
            if isinstance(content, dict):
                pub_str = content.get("pubDate", "") or ""
            else:
                ts = a.get("providerPublishTime")
            pub_dt = None
            if pub_str:
                try:
                    pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                except Exception:
                    pub_dt = None
            elif ts is not None:
                try:
                    pub_dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
                except Exception:
                    pub_dt = None
            if pub_dt is None:
                continue
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)
            if pub_dt >= cutoff_30:
                c30 += 1
                if pub_dt >= cutoff_7:
                    c7 += 1
        if c30 == 0:
            return None, c7, c30
        per_day_7 = c7 / 7.0
        per_day_30 = c30 / 30.0
        ratio = per_day_7 / per_day_30 if per_day_30 > 0 else None
        return ratio, c7, c30
    except Exception as e:
        print(f"[positioning] news velocity {symbol}: {type(e).__name__}: {e}")
        return None, 0, 0


def _label(score: float) -> str:
    if score >= 70:
        return "consensus"
    if score <= 30:
        return "contrarian"
    return "neutral"


def _fetch_one(symbol: str) -> dict:
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}
        inst = info.get("heldPercentInstitutions")
        short_pct = info.get("shortPercentOfFloat")
        insider = info.get("heldPercentInsiders")
        analyst_count = info.get("numberOfAnalystOpinions")
        rec_key = info.get("recommendationKey")

        velocity, c7, c30 = _news_velocity(symbol)

        score = (
            _W_INST * _norm_inst(inst)
            + _W_SHORT * _norm_short(short_pct)
            + _W_ANALYST * _norm_analyst(analyst_count)
            + _W_NEWS * _norm_news_velocity(velocity)
        ) * 100.0
        score = round(score, 1)

        return {
            "institutional_ownership_pct": round(inst * 100, 2) if inst else None,
            "short_pct_float": round(short_pct * 100, 2) if short_pct else None,
            "insider_ownership_pct": round(insider * 100, 2) if insider else None,
            "analyst_count": int(analyst_count) if analyst_count else None,
            "analyst_recommendation": rec_key,
            "headlines_7d": c7,
            "headlines_30d": c30,
            "headline_velocity_ratio": round(velocity, 2) if velocity else None,
            "crowding_score": score,
            "crowding_label": _label(score),
            "contrarian_flag": score <= 30,
            "consensus_flag": score >= 70,
        }
    except Exception as e:
        print(f"[positioning] {symbol}: {type(e).__name__}: {e}")
        return {}


def get_positioning(symbols: list[str]) -> dict[str, dict]:
    """Parallel-fetch positioning signals. Cached 6h per symbol."""
    now = time.time()
    out: dict[str, dict] = {}
    to_fetch: list[str] = []
    for sym in symbols:
        entry = _cache.get(sym)
        if entry and (now - entry[1]) < _CACHE_TTL:
            out[sym] = entry[0]
        else:
            to_fetch.append(sym)

    if not to_fetch:
        return out

    workers = min(_MAX_WORKERS, len(to_fetch))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(_fetch_one, to_fetch))
    for sym, result in zip(to_fetch, results):
        _cache[sym] = (result, time.time())
        out[sym] = result
    return out
