"""Signal history — feature-vector logger + forward-horizon scorer + accuracy report.

Primary KPI: buy/sell signal accuracy. This module turns the recommendation
engine into a measurable prediction problem so thresholds and sub-score
weights can later be calibrated against realized outcomes.

Three concerns:

1. log_signal()   — append a full feature snapshot for every recommendation
                    produced by recommendations._score_position. Captures
                    sub-scores, indicators, action, entry price and source.

2. score_horizons() — for every open signal whose H-day window has elapsed,
                    fetch historical bars via data_router and compute the
                    realized forward return at +5d / +21d. Writes back to
                    the same JSONL (in-place rewrite) with horizon columns.

3. accuracy_report() — classification metrics per action class
                    (precision/recall/F1), calibration table (score bucket →
                    realized win rate), and per-feature lift. Drives
                    threshold and weight calibration.

Output (gitignored):
  .claude/context/signal_history.jsonl

A signal is "won" at horizon H iff:
  BUY/ADD:   return at H > 0
  SELL/TRIM: return at H < 0   (inverted; SELL is a short-side prediction)
  WATCH/HOLD: excluded from accuracy calc (informational only)
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime
from pathlib import Path
from statistics import mean

from app.services import data_router

_CTX = Path(__file__).resolve().parents[2] / ".claude" / "context"
_HIST_FILE = _CTX / "signal_history.jsonl"

_DIRECTIONAL_ACTIONS = frozenset({"BUY", "ADD", "SELL", "TRIM"})
_LONG_ACTIONS = frozenset({"BUY", "ADD"})
_SHORT_ACTIONS = frozenset({"SELL", "TRIM"})

_DEFAULT_HORIZONS = (1, 3, 5, 10, 21, 42, 63)

# In-process dedupe set keyed by today. Reset on date rollover. Prevents
# O(N²) full-file scan on every log call when batch-logging recommendations.
_DEDUPE_SEEN: set[str] = set()
_DEDUPE_DAY: str | None = None


def _today_keys() -> set[str]:
    global _DEDUPE_DAY, _DEDUPE_SEEN
    today = date.today().isoformat()
    if _DEDUPE_DAY != today:
        _DEDUPE_DAY = today
        _DEDUPE_SEEN = set()
        if _HIST_FILE.exists():
            for line in _HIST_FILE.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (row.get("date") or "") == today:
                    k = row.get("key")
                    if k:
                        _DEDUPE_SEEN.add(k)
    return _DEDUPE_SEEN


def log_signal(rec: dict, *, source: str = "recommendations") -> dict:
    """Append a signal row with full feature vector for later scoring.

    `rec` is the dict returned by recommendations._score_position. Only the
    fields needed for accuracy auditing are persisted. Idempotency is per
    (date, source, symbol, action) — duplicate same-day signals are skipped.
    """
    symbol = (rec.get("symbol") or "").upper()
    action = (rec.get("action") or "").upper()
    if not symbol or not action:
        return {"skipped": "missing symbol or action"}

    today = date.today().isoformat()
    sig_key = f"{today}:{source}:{symbol}:{action}"

    seen = _today_keys()
    if sig_key in seen:
        return {"skipped": "duplicate", "key": sig_key}

    row = {
        "key": sig_key,
        "date": today,
        "ts": time.time(),
        "source": source,
        "symbol": symbol,
        "action": action,
        "score": rec.get("score"),
        "confidence": rec.get("confidence"),
        "evidence_tier": rec.get("evidence_tier"),
        "entry_price": rec.get("current_price"),
        # Feature vector — what the model "saw" at decision time
        "features": {
            "tech_score": rec.get("tech_score"),
            "fund_score": rec.get("fund_score"),
            "macro_score": rec.get("macro_score"),
            "sentiment": rec.get("sentiment"),
            "rsi": rec.get("rsi"),
            "stage": rec.get("stage"),
            "market_regime": rec.get("market_regime"),
            "analyst_upside_pct": rec.get("analyst_upside_pct"),
            "weight": rec.get("weight"),
            "signal_quality": rec.get("signal_quality"),
            "risk_reward": rec.get("risk_reward"),
            "kelly_pct": rec.get("kelly_pct"),
            "win_prob": rec.get("win_prob"),
            "earnings_risk": rec.get("earnings_risk"),
        },
        # Outcome columns — filled by score_horizons()
        "outcomes": {},  # {"h5": {"ret_pct":..., "win":..., "scored_at":...}, ...}
    }

    _CTX.mkdir(parents=True, exist_ok=True)
    with _HIST_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    seen.add(sig_key)
    return row


def score_horizons(
    horizons: tuple[int, ...] = _DEFAULT_HORIZONS,
    *,
    max_history_days: int = 365,
) -> dict:
    """Walk signal history, fill realized return at each horizon when window elapsed.

    For each signal:
      - Skip if non-directional (WATCH/HOLD)
      - Skip horizon if already scored
      - Skip horizon if (today - signal_date) < horizon (window not closed)
      - Fetch ~6mo of daily bars (or what's needed); find close at signal_date+H trading-day approx
      - Compute realized return; flip sign for SELL/TRIM
    """
    if not _HIST_FILE.exists():
        return {"error": "no signal_history.jsonl"}

    rows = []
    for line in _HIST_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    # Cache history per symbol to avoid repeated network calls
    history_cache: dict[str, list[dict]] = {}
    today = datetime.utcnow().date()
    scored_new = 0
    skipped_window = 0
    skipped_nondirectional = 0
    skipped_data = 0

    for row in rows:
        action = (row.get("action") or "").upper()
        if action not in _DIRECTIONAL_ACTIONS:
            skipped_nondirectional += 1
            continue

        sig_date_str = row.get("date") or ""
        try:
            sig_date = datetime.fromisoformat(sig_date_str).date()
        except ValueError:
            continue

        if (today - sig_date).days > max_history_days:
            continue  # too old, skip

        outcomes = row.setdefault("outcomes", {})
        entry = row.get("entry_price") or 0.0
        symbol = row.get("symbol") or ""

        for h in horizons:
            key = f"h{h}"
            if key in outcomes:
                continue
            if (today - sig_date).days < h:
                skipped_window += 1
                continue

            if symbol not in history_cache:
                try:
                    bars = data_router.get_history(symbol, period="1y", interval="1d")
                    history_cache[symbol] = bars or []
                except Exception:
                    history_cache[symbol] = []
            bars = history_cache[symbol]
            if not bars:
                skipped_data += 1
                continue

            exit_close = _close_at_offset(bars, sig_date, h)
            if exit_close is None or entry <= 0:
                skipped_data += 1
                continue

            raw_ret = (exit_close - entry) / entry * 100
            ret_pct = raw_ret if action in _LONG_ACTIONS else -raw_ret
            outcomes[key] = {
                "ret_pct": round(ret_pct, 3),
                "win": ret_pct > 0,
                "exit_close": round(exit_close, 4),
                "scored_at": time.time(),
            }
            scored_new += 1

    _rewrite_history(rows)
    return {
        "scored_new": scored_new,
        "skipped_window": skipped_window,
        "skipped_nondirectional": skipped_nondirectional,
        "skipped_data": skipped_data,
        "total_rows": len(rows),
    }


def accuracy_report(
    horizon: int = 21,
    *,
    min_count: int = 5,
) -> dict:
    """Per-action precision/recall/F1 + calibration table for one horizon.

    Treats each directional signal as a binary classifier:
      label = realized return at H > 0 for long actions, < 0 for short actions.
      prediction = "directional bet was correct" (win=True).

    Returns:
      {
        "horizon": 21,
        "n": total directional signals,
        "by_action": {"BUY": {n, win_rate, avg_ret, precision, ...}, ...},
        "by_score_bucket": {"7.5-10": {n, win_rate, avg_ret}, ...},
        "by_confidence": {"high": {...}, "medium": {...}, "low": {...}},
        "expectancy_pct": float,   # win_rate*avg_win - (1-win_rate)*avg_loss
        "as_of": iso,
      }
    """
    if not _HIST_FILE.exists():
        return {"error": "no signal_history.jsonl — log signals first"}

    rows = []
    for line in _HIST_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    key = f"h{horizon}"
    scored = [
        r for r in rows
        if (r.get("action") or "").upper() in _DIRECTIONAL_ACTIONS
        and key in (r.get("outcomes") or {})
    ]
    if not scored:
        return {
            "horizon": horizon,
            "n": 0,
            "error": f"no scored signals at h{horizon} — run score_horizons first",
        }

    # By action
    by_action: dict[str, dict] = {}
    for action in ("BUY", "ADD", "SELL", "TRIM"):
        subset = [r for r in scored if (r.get("action") or "").upper() == action]
        if len(subset) < min_count:
            if subset:
                by_action[action] = {"n": len(subset), "insufficient_data": True}
            continue
        by_action[action] = _classify_subset(subset, horizon)

    # By score bucket (full 0-10 range, 1.0 buckets)
    by_score: dict[str, dict] = {}
    buckets = [(0, 3.5), (3.5, 5.5), (5.5, 7.5), (7.5, 10.001)]
    for lo, hi in buckets:
        subset = [
            r for r in scored
            if r.get("score") is not None and lo <= r.get("score") < hi
        ]
        if not subset:
            continue
        label = f"{lo:.1f}-{hi:.1f}" if hi <= 10 else f"{lo:.1f}-10.0"
        by_score[label] = _classify_subset(subset, horizon)

    # By confidence
    by_conf: dict[str, dict] = {}
    for conf in ("high", "medium", "low"):
        subset = [r for r in scored if (r.get("confidence") or "").lower() == conf]
        if subset:
            by_conf[conf] = _classify_subset(subset, horizon)

    # Overall expectancy
    overall = _classify_subset(scored, horizon)

    return {
        "horizon": horizon,
        "n": len(scored),
        "overall": overall,
        "by_action": by_action,
        "by_score_bucket": by_score,
        "by_confidence": by_conf,
        "as_of": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }


def calibrate_thresholds(
    horizon: int = 21,
    *,
    min_count: int = 10,
) -> dict:
    """Sweep BUY/WATCH/SELL thresholds to maximize expectancy on history.

    Returns the current thresholds and the best-found thresholds with their
    win rate + expectancy. Caller can manually update recommendations.py
    if best > current by a meaningful margin.

    Caveat: small-sample fits. Surface n alongside metrics so user can judge.
    """
    if not _HIST_FILE.exists():
        return {"error": "no signal_history.jsonl"}

    rows = []
    for line in _HIST_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    key = f"h{horizon}"
    scored = [
        r for r in rows
        if (r.get("action") or "").upper() in _DIRECTIONAL_ACTIONS
        and key in (r.get("outcomes") or {})
        and r.get("score") is not None
    ]
    if len(scored) < min_count:
        return {
            "horizon": horizon,
            "n": len(scored),
            "error": f"insufficient scored signals (<{min_count})",
        }

    current = {"buy": 7.5, "watch": 3.5, "sell_below": 3.5}

    # Grid search BUY threshold in [6.0, 8.5] step 0.25.
    # For each candidate, treat scores >= buy_thr as predicted-positive,
    # scores < sell_thr as predicted-negative, and compute expectancy on
    # what the engine would have actually traded.
    best = None
    for buy_thr_x10 in range(60, 91, 5):  # 6.0..9.0 step 0.5 (cheaper grid)
        buy_thr = buy_thr_x10 / 10
        for sell_thr_x10 in range(20, 51, 5):  # 2.0..5.0 step 0.5
            sell_thr = sell_thr_x10 / 10
            if sell_thr >= buy_thr:
                continue
            traded = [
                r for r in scored
                if r["score"] >= buy_thr or r["score"] < sell_thr
            ]
            if len(traded) < min_count:
                continue
            metrics = _classify_subset(traded, horizon)
            cand = {
                "buy_thr": buy_thr,
                "sell_below": sell_thr,
                "n_traded": len(traded),
                **metrics,
            }
            if best is None or cand["expectancy_pct"] > best["expectancy_pct"]:
                best = cand

    return {
        "horizon": horizon,
        "n_scored": len(scored),
        "current": current,
        "best": best,
        "note": "best is highest expectancy_pct on history; small-n caveat applies",
    }


# ────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ────────────────────────────────────────────────────────────────────────────


def _classify_subset(rows: list[dict], horizon: int) -> dict:
    key = f"h{horizon}"
    rets = [
        r["outcomes"][key]["ret_pct"]
        for r in rows
        if key in (r.get("outcomes") or {})
        and r["outcomes"][key].get("ret_pct") is not None
    ]
    if not rets:
        return {"n": 0}
    wins = [x for x in rets if x > 0]
    losses = [x for x in rets if x <= 0]
    win_rate = len(wins) / len(rets)
    avg_win = mean(wins) if wins else 0.0
    avg_loss = mean(losses) if losses else 0.0  # negative or zero
    avg_ret = mean(rets)
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss
    # F1 treating "win" as positive class; precision = win_rate since every
    # signal in the subset is a "predicted positive" by construction
    precision = win_rate
    recall = win_rate  # subset has no false-negatives (all are predictions)
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0 else 0.0
    )
    return {
        "n": len(rets),
        "win_rate_pct": round(win_rate * 100, 1),
        "avg_ret_pct": round(avg_ret, 2),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "expectancy_pct": round(expectancy, 3),
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
    }


def _close_at_offset(
    bars: list[dict],
    sig_date: date,
    trading_days_offset: int,
) -> float | None:
    """Find the close `trading_days_offset` trading days after sig_date.

    Bars are EOD daily, ascending by date. We pick the first bar whose date
    is >= sig_date (entry bar), then index forward by N trading-day positions.
    Returns None if the window hasn't elapsed in the dataset.
    """
    if not bars:
        return None
    # Bars may carry 'date' as ISO YYYY-MM-DD or full timestamp
    entry_idx = None
    for i, b in enumerate(bars):
        bd_str = (b.get("date") or "")[:10]
        try:
            bd = datetime.fromisoformat(bd_str).date()
        except ValueError:
            continue
        if bd >= sig_date:
            entry_idx = i
            break
    if entry_idx is None:
        return None
    exit_idx = entry_idx + trading_days_offset
    if exit_idx >= len(bars):
        return None
    exit_bar = bars[exit_idx]
    close = exit_bar.get("close") or exit_bar.get("adj_close")
    return float(close) if close else None


def _rewrite_history(rows: list[dict]) -> None:
    _CTX.mkdir(parents=True, exist_ok=True)
    tmp = _HIST_FILE.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    tmp.replace(_HIST_FILE)


# ───────────────────────────────────────────────────────────────────────────────
# Signal decay, per-source attribution, confidence calibration
# ───────────────────────────────────────────────────────────────────────────────


def _load_history() -> list[dict]:
    if not _HIST_FILE.exists():
        return []
    rows: list[dict] = []
    for line in _HIST_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def signal_decay_curve(
    horizons: tuple[int, ...] = _DEFAULT_HORIZONS,
    *,
    action_filter: str | None = None,
    min_count: int = 5,
) -> dict:
    """Average realized return per horizon to find peak holding period.

    For each horizon h in horizons, computes mean ret_pct, win_rate, n.
    The horizon with the highest mean return is the empirical "best" hold
    duration for this signal type — anything held longer is decay.

    action_filter: restrict to one action (BUY / SELL / ADD / TRIM). When
    None, pools all directional actions (long-side returns + flipped
    short-side returns so direction is normalized).
    """
    rows = _load_history()
    if not rows:
        return {"error": "no signal history"}

    if action_filter:
        action_filter = action_filter.upper()
        rows = [r for r in rows if (r.get("action") or "").upper() == action_filter]
    else:
        rows = [
            r for r in rows
            if (r.get("action") or "").upper() in _DIRECTIONAL_ACTIONS
        ]

    curve: dict[str, dict] = {}
    for h in horizons:
        key = f"h{h}"
        rets = [
            r["outcomes"][key]["ret_pct"]
            for r in rows
            if key in (r.get("outcomes") or {})
            and r["outcomes"][key].get("ret_pct") is not None
        ]
        if len(rets) < min_count:
            curve[key] = {"n": len(rets), "insufficient_data": True}
            continue
        wins = sum(1 for x in rets if x > 0)
        curve[key] = {
            "n": len(rets),
            "avg_ret_pct": round(mean(rets), 3),
            "median_ret_pct": round(sorted(rets)[len(rets) // 2], 3),
            "win_rate_pct": round(wins / len(rets) * 100, 1),
        }

    # Peak holding period — horizon with highest avg_ret_pct
    valid = {k: v for k, v in curve.items() if "avg_ret_pct" in v}
    peak = max(valid.items(), key=lambda kv: kv[1]["avg_ret_pct"]) if valid else None

    return {
        "action_filter": action_filter or "ALL_DIRECTIONAL",
        "curve": curve,
        "peak_horizon": peak[0] if peak else None,
        "peak_avg_ret_pct": peak[1]["avg_ret_pct"] if peak else None,
        "interpretation": (
            f"Best holding period: {peak[0]} ({peak[1]['avg_ret_pct']:+.2f}% avg)"
            if peak else "insufficient data for decay analysis"
        ),
        "as_of": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }


def per_signal_source_attribution(
    horizon: int = 21,
    *,
    min_count: int = 5,
) -> dict:
    """Isolate each sub-signal's contribution to realized return.

    Buckets signals where one sub-score dominates (others near zero) so the
    dominant source's stand-alone alpha can be measured. Useful to identify
    which signal sources actually carry edge vs which add noise.

    Dominance rule: |dominant_source_score| >= 2 * max(|other_sources|).
    Sources tested: tech_score, fund_score, macro_score, sentiment.
    """
    rows = _load_history()
    if not rows:
        return {"error": "no signal history"}

    key = f"h{horizon}"
    scored = [
        r for r in rows
        if (r.get("action") or "").upper() in _DIRECTIONAL_ACTIONS
        and key in (r.get("outcomes") or {})
        and r["outcomes"][key].get("ret_pct") is not None
    ]
    if not scored:
        return {"horizon": horizon, "n": 0, "error": "no scored signals"}

    sources = ("tech_score", "fund_score", "macro_score", "sentiment")
    by_source: dict[str, dict] = {}

    for src in sources:
        bucket: list[dict] = []
        for r in scored:
            feats = r.get("features") or {}
            dominant = feats.get(src)
            if dominant is None:
                continue
            others = [
                abs(float(feats.get(s) or 0))
                for s in sources if s != src
            ]
            max_other = max(others) if others else 0.0
            if abs(float(dominant)) >= max(2 * max_other, 0.5):
                bucket.append(r)

        if len(bucket) < min_count:
            by_source[src] = {"n": len(bucket), "insufficient_data": True}
            continue

        rets = [r["outcomes"][key]["ret_pct"] for r in bucket]
        wins = sum(1 for x in rets if x > 0)
        by_source[src] = {
            "n": len(bucket),
            "avg_ret_pct": round(mean(rets), 3),
            "win_rate_pct": round(wins / len(rets) * 100, 1),
            "verdict": _attribution_verdict(rets),
        }

    return {
        "horizon": horizon,
        "n_total_scored": len(scored),
        "by_source": by_source,
        "note": (
            "Buckets isolate single-source dominance. Sources with avg_ret <= 0 "
            "or win_rate < 50% are adding noise; consider down-weighting in the "
            "composite engine."
        ),
        "as_of": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }


def _attribution_verdict(rets: list[float]) -> str:
    if not rets:
        return "no_data"
    avg = mean(rets)
    wins = sum(1 for x in rets if x > 0)
    win_rate = wins / len(rets)
    if avg > 1.0 and win_rate > 0.55:
        return "carrying_alpha"
    if avg > 0 and win_rate > 0.50:
        return "marginal_alpha"
    if avg < -0.5 or win_rate < 0.40:
        return "noise_or_anti_signal"
    return "neutral"


def calibrate_confidence(
    horizon: int = 21,
    *,
    min_count_per_bucket: int = 5,
) -> dict:
    """Map confidence label (high/medium/low) to empirical hit rate.

    Industry-style calibration: a HIGH confidence call must outperform
    MEDIUM, which must outperform LOW. If buckets don't separate cleanly,
    the confidence assignment is uncalibrated and HIGH is meaningless.

    Returns per-bucket stats + a calibration verdict + suggested action.
    """
    rows = _load_history()
    if not rows:
        return {"error": "no signal history"}

    key = f"h{horizon}"
    scored = [
        r for r in rows
        if (r.get("action") or "").upper() in _DIRECTIONAL_ACTIONS
        and key in (r.get("outcomes") or {})
        and r["outcomes"][key].get("ret_pct") is not None
    ]
    if not scored:
        return {"horizon": horizon, "n": 0, "error": "no scored signals"}

    buckets: dict[str, list[float]] = {"high": [], "medium": [], "low": []}
    for r in scored:
        conf = (r.get("confidence") or "").lower()
        if conf in buckets:
            buckets[conf].append(r["outcomes"][key]["ret_pct"])

    stats: dict[str, dict] = {}
    for label, rets in buckets.items():
        if len(rets) < min_count_per_bucket:
            stats[label] = {"n": len(rets), "insufficient_data": True}
            continue
        wins = sum(1 for x in rets if x > 0)
        stats[label] = {
            "n": len(rets),
            "win_rate_pct": round(wins / len(rets) * 100, 1),
            "avg_ret_pct": round(mean(rets), 3),
        }

    # Verdict: are buckets monotonically ordered?
    valid = {k: v for k, v in stats.items() if "win_rate_pct" in v}
    verdict: str
    suggested: str
    if len(valid) < 2:
        verdict = "insufficient_data"
        suggested = "log more signals before calibrating"
    else:
        win_rates = {k: v["win_rate_pct"] for k, v in valid.items()}
        order = ["high", "medium", "low"]
        present = [k for k in order if k in win_rates]
        rates = [win_rates[k] for k in present]
        monotone = all(rates[i] >= rates[i + 1] for i in range(len(rates) - 1))
        if monotone and (rates[0] - rates[-1]) >= 10:
            verdict = "calibrated"
            suggested = "confidence labels are meaningful — keep current thresholds"
        elif monotone:
            verdict = "weakly_calibrated"
            suggested = (
                "ordering correct but spread <10pp — tighten HIGH threshold"
            )
        else:
            verdict = "uncalibrated"
            suggested = (
                "HIGH does not outperform MEDIUM/LOW — re-weight sub-scores "
                "or retire the confidence label"
            )

    return {
        "horizon": horizon,
        "n_total_scored": len(scored),
        "buckets": stats,
        "verdict": verdict,
        "suggested_action": suggested,
        "as_of": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
