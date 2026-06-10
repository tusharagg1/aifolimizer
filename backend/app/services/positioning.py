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

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yfinance as yf

from app.services import cache_layer
from app.services import news as news_svc
from app.security import get_logger

_LOG = get_logger("aifolimizer.services.positioning")


_cache: dict[str, tuple[dict, float]] = {}
_CACHE_TTL = 6 * 3600
_MAX_WORKERS = 8
_L2_NAMESPACE = "positioning"

# Append-only crowding history. One JSONL line per (symbol, day) snapshot.
# Lives next to alerts log for consistent context-dir layout.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_CTX_DIR = _REPO_ROOT / ".claude" / "context"
_HISTORY_FILE = _CTX_DIR / "crowding_history.jsonl"

# Crowding score weights — empirical, not derived. Tune as data accumulates.
_W_INST = 0.35  # high inst ownership = consensus institutional pile-in
_W_SHORT = 0.20  # low short interest = bears already covered = consensus long
_W_ANALYST = 0.20  # high analyst coverage = consensus name
_W_NEWS = 0.25  # surging headlines = retail attention surge


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
    """Returns (ratio_7d_per_day_vs_30d_per_day, count_7d, count_30d).

    Sources headlines via the resilient multi-source news chain (finnhub /
    yfinance / eodhd) rather than a single flaky yfinance scrape, so a Yahoo
    outage no longer silently zeroes out headline velocity.
    """
    try:
        articles = news_svc.recent_headlines(symbol)
        now = datetime.now(timezone.utc).timestamp()
        cutoff_7 = now - 7 * 86400
        cutoff_30 = now - 30 * 86400
        c7 = 0
        c30 = 0
        for a in articles:
            ts = a.get("published_ts")
            if ts is None:
                continue
            if ts >= cutoff_30:
                c30 += 1
                if ts >= cutoff_7:
                    c7 += 1
        if c30 == 0:
            return None, c7, c30
        per_day_7 = c7 / 7.0
        per_day_30 = c30 / 30.0
        ratio = per_day_7 / per_day_30 if per_day_30 > 0 else None
        return ratio, c7, c30
    except Exception as e:
        _LOG.warning(f"[positioning] news velocity {symbol}: {type(e).__name__}: {e}")
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
        _LOG.warning(f"[positioning] {symbol}: {type(e).__name__}: {e}")
        return {}


def get_positioning(symbols: list[str]) -> dict[str, dict]:
    """Parallel-fetch positioning signals. Two-tier cache:
    L1 in-process dict (fast hot-path), L2 diskcache (cross-process, survives
    server restarts and MCP↔FastAPI process boundary). 6h TTL on both.
    """
    now = time.time()
    out: dict[str, dict] = {}
    to_fetch: list[str] = []
    for sym in symbols:
        # L1
        entry = _cache.get(sym)
        if entry and (now - entry[1]) < _CACHE_TTL:
            out[sym] = entry[0]
            continue
        # L2 — populate L1 on hit
        l2 = cache_layer.cache_get(_L2_NAMESPACE, sym)
        if l2:
            _cache[sym] = (l2, now)
            out[sym] = l2
            continue
        to_fetch.append(sym)

    if not to_fetch:
        return out

    workers = min(_MAX_WORKERS, len(to_fetch))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(_fetch_one, to_fetch))
    for sym, result in zip(to_fetch, results):
        if result:
            _cache[sym] = (result, time.time())
            cache_layer.cache_set(_L2_NAMESPACE, sym, result, _CACHE_TTL)
        out[sym] = result
    return out


# ── History persistence ─────────────────────────────────────────────────────


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def snapshot_to_history(symbols: list[str]) -> dict[str, int]:
    """Persist today's crowding score per symbol. Once-per-day idempotent.

    Skips writes if today's row for that symbol already exists. Returns counts.
    File: .claude/context/crowding_history.jsonl (append-only).
    Each line: {"date": "YYYY-MM-DD", "symbol": "AAPL",
                "crowding_score": 85.0, "crowding_label": "consensus"}
    """
    data = get_positioning(symbols)
    today = _today_iso()
    existing_today = _load_today_symbols(today)

    written = 0
    skipped = 0
    _CTX_DIR.mkdir(parents=True, exist_ok=True)
    with _HISTORY_FILE.open("a", encoding="utf-8") as f:
        for sym, rec in data.items():
            if not rec or rec.get("crowding_score") is None:
                skipped += 1
                continue
            if sym in existing_today:
                skipped += 1
                continue
            line = {
                "date": today,
                "symbol": sym,
                "crowding_score": rec.get("crowding_score"),
                "crowding_label": rec.get("crowding_label"),
            }
            f.write(json.dumps(line) + "\n")
            written += 1
    return {"written": written, "skipped": skipped}


def _load_today_symbols(today: str) -> set[str]:
    if not _HISTORY_FILE.exists():
        return set()
    syms: set[str] = set()
    try:
        with _HISTORY_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("date") == today:
                    sym = rec.get("symbol")
                    if sym:
                        syms.add(sym)
    except Exception as e:
        _LOG.warning(f"[positioning] history read failed: {e}")
    return syms


def detect_regime_shifts(
    symbols: list[str],
    lookback_days: int = 30,
    score_delta_threshold: float = 25.0,
) -> list[dict]:
    """Find symbols whose crowding score has shifted materially over lookback.

    Compares latest score per symbol vs the oldest recorded score within the
    lookback window. Returns shifts where |delta| >= score_delta_threshold.

    Each shift: {symbol, from_score, to_score, from_label, to_label, delta,
                 first_seen, last_seen, direction}.
    direction = 'crowding_up' (more consensus) or 'crowding_down' (more contrarian).
    """
    if not _HISTORY_FILE.exists():
        return []

    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=lookback_days)).isoformat()
    wanted = set(s.upper() for s in symbols) if symbols else None

    # symbol -> [(date, score, label), ...] within window
    series: dict[str, list[tuple[str, float, str]]] = {}
    try:
        with _HISTORY_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                d = rec.get("date")
                sym = rec.get("symbol")
                score = rec.get("crowding_score")
                label = rec.get("crowding_label")
                if not (d and sym and score is not None):
                    continue
                if d < cutoff:
                    continue
                if wanted is not None and sym.upper() not in wanted:
                    continue
                series.setdefault(sym, []).append((d, float(score), str(label or "")))
    except Exception as e:
        _LOG.warning(f"[positioning] regime-shift read failed: {e}")
        return []

    shifts: list[dict] = []
    for sym, rows in series.items():
        if len(rows) < 2:
            continue
        rows.sort(key=lambda r: r[0])
        first_d, first_s, first_l = rows[0]
        last_d, last_s, last_l = rows[-1]
        delta = round(last_s - first_s, 1)
        if abs(delta) < score_delta_threshold:
            continue
        shifts.append(
            {
                "symbol": sym,
                "from_score": first_s,
                "to_score": last_s,
                "from_label": first_l,
                "to_label": last_l,
                "delta": delta,
                "first_seen": first_d,
                "last_seen": last_d,
                "direction": "crowding_up" if delta > 0 else "crowding_down",
            }
        )
    shifts.sort(key=lambda s: abs(s["delta"]), reverse=True)
    return shifts
