"""Forward paper-trade logger and scorer.

Two concerns:
1. Log — append every skill recommendation to recommendations.jsonl with a
   full signal contract: horizon, thesis, invalidation, expected upside/
   downside, confidence source, benchmark prices at entry, model version,
   and the raw feature snapshot used to produce the call.
2. Score — mark-to-market every open recommendation daily, compute alpha
   vs SPY / QQQ / XEQT.TO (and optional sector ETF), and bucket results by
   action and conviction so a real signal scorecard becomes possible.

Output paths (gitignored):
  .claude/context/recommendations.jsonl
  .claude/context/scored_recommendations.jsonl
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path

from app.services import data_router

_log = logging.getLogger(__name__)
_LEGACY_TENANT_HASH = hashlib.sha1(b"legacy").hexdigest()[:16]

_CTX = Path(__file__).resolve().parents[2] / ".claude" / "context"
_REC_FILE = _CTX / "recommendations.jsonl"
_SCORED_FILE = _CTX / "scored_recommendations.jsonl"

_VALID_ACTIONS = {"BUY", "SELL", "HOLD", "ADD", "TRIM", "WATCH", "NO_EDGE"}
_VALID_CONV = {"HIGH", "MED", "LOW"}
_VALID_CONF_SRC = {"backtested", "live_validated", "experimental"}

DEFAULT_BENCHMARKS = ("SPY", "QQQ", "XEQT.TO")
DEFAULT_PRIMARY_BENCHMARK = "XEQT.TO"
MODEL_VERSION = "2026.05-v1"

# Per-process dedupe of already-logged rec IDs for today.
# Prevents duplicate writes when scheduler re-runs a skill within the day.
_TODAY_IDS: set[str] = set()
_TODAY_DATE: str | None = None


def _seen_today(rec_id: str) -> bool:
    """Return True if rec_id already written today (in this process or on disk).

    Lazy-loads today's IDs from disk on first call per UTC date, then tracks
    additions in-memory for the rest of the process lifetime.
    """
    global _TODAY_DATE
    today = date.today().isoformat()
    if _TODAY_DATE != today:
        _TODAY_DATE = today
        _TODAY_IDS.clear()
        if _REC_FILE.exists():
            try:
                with _REC_FILE.open("r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            row = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if row.get("date") == today and row.get("id"):
                            _TODAY_IDS.add(row["id"])
            except OSError:
                pass
    return rec_id in _TODAY_IDS


def _mark_logged(rec_id: str) -> None:
    _TODAY_IDS.add(rec_id)


def _safe_quote(ticker: str) -> tuple[float, str]:
    try:
        q = data_router.get_quote(ticker.upper())
        return float(q.get("price") or 0.0), q.get("source", "unknown")
    except Exception:
        return 0.0, "unavailable"


def _capture_benchmark_prices(symbols: tuple[str, ...]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for sym in symbols:
        price, source = _safe_quote(sym)
        out[sym] = {"price": round(price, 4), "source": source}
    return out


def log_recommendation(
    skill: str,
    ticker: str,
    action: str,
    conviction: str,
    rationale: str,
    target_pct: float | None = None,
    stop_pct: float | None = None,
    account: str | None = None,
    horizon_days: int | None = None,
    thesis: str | None = None,
    invalidation: str | None = None,
    expected_upside_pct: float | None = None,
    expected_downside_pct: float | None = None,
    confidence_source: str = "experimental",
    sector_etf: str | None = None,
    benchmark_symbol: str | None = None,
    features: dict | None = None,
    model_version: str | None = None,
) -> dict:
    """Append one recommendation to recommendations.jsonl with full signal contract.

    Benchmark prices (SPY/QQQ/XEQT.TO + optional sector_etf) are captured at
    entry so forward alpha is computable later. entry_price comes from
    data_router so it reflects the actual price at recommendation time.

    `benchmark_symbol` pins the PRIMARY benchmark used for alpha attribution
    on this rec. Defaults to sector_etf when supplied, else DEFAULT_PRIMARY_BENCHMARK.
    Returns the rec dict; if a same-day duplicate (skill+ticker+action) is detected
    the existing rec is returned with status='duplicate_skipped' and not appended.
    """
    action = action.upper()
    conviction = conviction.upper()
    if action not in _VALID_ACTIONS:
        raise ValueError(f"action must be one of {_VALID_ACTIONS}")
    if conviction not in _VALID_CONV:
        raise ValueError(f"conviction must be one of {_VALID_CONV}")
    if confidence_source not in _VALID_CONF_SRC:
        raise ValueError(f"confidence_source must be one of {_VALID_CONF_SRC}")

    rec_id = _make_id(skill, ticker, action)
    if _seen_today(rec_id):
        return {"id": rec_id, "status": "duplicate_skipped"}

    entry_price, source = _safe_quote(ticker)

    sector_upper = sector_etf.upper() if sector_etf else None
    primary_bench = benchmark_symbol.upper() if benchmark_symbol else (sector_upper or DEFAULT_PRIMARY_BENCHMARK)

    bench_symbols = DEFAULT_BENCHMARKS + ((sector_upper,) if sector_upper else ())
    if primary_bench not in bench_symbols:
        bench_symbols = bench_symbols + (primary_bench,)
    benchmarks_entry = _capture_benchmark_prices(bench_symbols)

    rec = {
        "id": rec_id,
        "date": date.today().isoformat(),
        "ts": time.time(),
        "skill": skill,
        "model_version": model_version or MODEL_VERSION,
        "ticker": ticker.upper(),
        "action": action,
        "conviction": conviction,
        "confidence_source": confidence_source,
        "horizon_days": horizon_days,
        "thesis": thesis,
        "invalidation": invalidation,
        "entry_price": round(entry_price, 4),
        "price_source": source,
        "target_pct": target_pct,
        "stop_pct": stop_pct,
        "expected_upside_pct": expected_upside_pct,
        "expected_downside_pct": expected_downside_pct,
        "account": account,
        "sector_etf": sector_upper,
        "benchmark_symbol": primary_bench,
        "benchmarks_entry": benchmarks_entry,
        "features": features or {},
        "rationale_hash": hashlib.sha256((rationale or "").encode()).hexdigest()[:16],
        "status": "open",
        "exit_price": None,
        "exit_date": None,
        "return_pct": None,
        "win": None,
    }
    _CTX.mkdir(parents=True, exist_ok=True)
    with _REC_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    _mark_logged(rec_id)
    _mirror_to_postgres(rec)
    return rec


async def _async_mirror_insert(dsn: str, rec: dict) -> None:
    import asyncpg

    conn = await asyncpg.connect(dsn, command_timeout=2, timeout=2)
    try:
        await conn.execute(
            """
            INSERT INTO recommendations (
              tenant_hash, date, ts, skill, model_version, ticker, action, conviction,
              horizon_days, thesis, invalidation, entry_price, target_pct, stop_pct,
              expected_upside_pct, expected_downside_pct, account, sector_etf,
              benchmark_symbol, benchmarks_entry, features, rationale_hash, status
            ) VALUES (
              $1, $2, $3, $4, $5, $6, $7, $8,
              $9, $10, $11, $12, $13, $14,
              $15, $16, $17, $18,
              $19, $20, $21, $22, $23
            )
            ON CONFLICT (tenant_hash, date, skill, ticker, action) DO NOTHING
            """,
            _LEGACY_TENANT_HASH,
            datetime.fromisoformat(rec["date"]).date(),
            datetime.fromtimestamp(float(rec["ts"]), tz=timezone.utc),
            rec.get("skill", ""),
            rec.get("model_version", MODEL_VERSION),
            rec.get("ticker", ""),
            rec.get("action", ""),
            rec.get("conviction", ""),
            rec.get("horizon_days"),
            rec.get("thesis"),
            rec.get("invalidation"),
            rec.get("entry_price"),
            rec.get("target_pct"),
            rec.get("stop_pct"),
            rec.get("expected_upside_pct"),
            rec.get("expected_downside_pct"),
            rec.get("account"),
            rec.get("sector_etf"),
            rec.get("benchmark_symbol"),
            json.dumps(rec.get("benchmarks_entry")) if rec.get("benchmarks_entry") is not None else None,
            json.dumps(rec.get("features")) if rec.get("features") is not None else None,
            rec.get("rationale_hash"),
            rec.get("status", "open"),
        )
    finally:
        await conn.close()


def _mirror_to_postgres(rec: dict) -> None:
    """Best-effort async mirror to Postgres.

    Fires daemon thread so the JSONL caller never blocks on DB. Failures are
    swallowed (JSONL stays source of truth). Disabled when POSTGRES_DSN unset
    or the import fails.
    """
    try:
        from app.core.config import settings

        dsn = settings.postgres_dsn
    except Exception:
        return
    if not dsn:
        return

    def _runner() -> None:
        try:
            asyncio.run(_async_mirror_insert(dsn, rec))
        except Exception as exc:
            _log.debug("postgres mirror skipped (%s): %s", rec.get("ticker"), exc)

    threading.Thread(target=_runner, daemon=True, name="pg-mirror").start()


def _benchmark_returns(rec: dict, quote_map: dict[str, float] | None = None) -> dict[str, float]:
    """Compute current return for each benchmark captured at entry.

    If quote_map provided, uses it (batch-prefetched). Otherwise falls back
    to per-symbol _safe_quote (slow path — kept for callers outside scorer).
    """
    out: dict[str, float] = {}
    benches = rec.get("benchmarks_entry") or {}
    for sym, entry in benches.items():
        entry_px = float(entry.get("price") or 0.0)
        if entry_px <= 0:
            continue
        if quote_map is not None:
            now_px = quote_map.get(sym.upper(), 0.0)
        else:
            now_px, _ = _safe_quote(sym)
        if now_px <= 0:
            continue
        out[sym] = (now_px - entry_px) / entry_px * 100
    return out


def score_recommendations(max_age_days: int = 180) -> dict:
    """Mark-to-market all open recommendations, close stops/targets, compute alpha.

    Writes updated records to scored_recommendations.jsonl. Returns summary
    stats including alpha vs each benchmark and per-action/per-conviction
    breakdowns.
    """
    if not _REC_FILE.exists():
        return {"error": "no recommendations.jsonl found"}

    recs = [json.loads(line) for line in _REC_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    cutoff_ts = time.time() - max_age_days * 86400
    recs = [r for r in recs if r.get("ts", 0) >= cutoff_ts]

    open_recs = [r for r in recs if r.get("status") == "open"]
    scored: list[dict] = []
    skipped = 0

    # Batch-fetch all quotes (tickers + benchmarks) in one call — replaces
    # N serial data_router.get_quote() calls (~5x faster on N>5 open recs).
    needed_syms: set[str] = set()
    for r in open_recs:
        if r.get("action") in ("HOLD", "WATCH", "NO_EDGE"):
            continue
        needed_syms.add(r["ticker"].upper())
        for bsym in (r.get("benchmarks_entry") or {}).keys():
            needed_syms.add(bsym.upper())
    quote_map: dict[str, float] = {}
    if needed_syms:
        try:
            batch = data_router.get_quotes_batch(sorted(needed_syms))
            for sym, q in batch.items():
                quote_map[sym.upper()] = float(q.get("price") or 0.0)
        except Exception:
            pass  # fall through to per-rec _safe_quote below

    for rec in open_recs:
        ticker = rec["ticker"]
        action = rec["action"]
        entry = float(rec.get("entry_price") or 0.0)

        if action in ("HOLD", "WATCH", "NO_EDGE"):
            # not actionable — keep in log but no P&L track
            continue

        if entry <= 0:
            skipped += 1
            continue

        current = quote_map.get(ticker.upper(), 0.0)
        if current <= 0:
            current, _ = _safe_quote(ticker)
        if current <= 0:
            skipped += 1
            continue

        if action in ("BUY", "ADD"):
            ret_pct = (current - entry) / entry * 100
        elif action in ("SELL", "TRIM"):
            ret_pct = (entry - current) / entry * 100
        else:
            ret_pct = 0.0

        bench_rets = _benchmark_returns(rec, quote_map=quote_map)
        # alpha = signal pnl - benchmark pnl under the same directional bet.
        # For BUY/ADD signal pnl = +asset_ret, bench pnl = +bench_ret  -> alpha = ret_pct - br.
        # For SELL/TRIM signal pnl = -asset_ret (= ret_pct as stored), bench pnl = -bench_ret -> alpha = ret_pct + br.
        alpha: dict[str, float] = {}
        for sym, br in bench_rets.items():
            if action in ("SELL", "TRIM"):
                alpha[sym] = round(ret_pct + br, 2)
            else:
                alpha[sym] = round(ret_pct - br, 2)

        stop = rec.get("stop_pct")
        target = rec.get("target_pct")
        horizon = rec.get("horizon_days")
        status = "open"

        age_days = (time.time() - rec.get("ts", time.time())) / 86400
        if stop is not None and ret_pct <= -abs(stop):
            status = "stopped_out"
        elif target is not None and ret_pct >= abs(target):
            status = "target_hit"
        elif horizon is not None and age_days >= horizon:
            status = "horizon_closed"

        primary_bench = rec.get("benchmark_symbol") or DEFAULT_PRIMARY_BENCHMARK
        primary_alpha = alpha.get(primary_bench)

        scored_rec = dict(rec)
        scored_rec.update(
            {
                "current_price": round(current, 4),
                "unrealized_pct": round(ret_pct, 2),
                "benchmark_returns_pct": {k: round(v, 2) for k, v in bench_rets.items()},
                "alpha_pct": alpha,
                "primary_benchmark_alpha_pct": primary_alpha,
                "beat_primary_benchmark": primary_alpha > 0 if primary_alpha is not None else None,
                "win": ret_pct > 0,
                "beat_spy": alpha.get("SPY", 0) > 0 if "SPY" in alpha else None,
                "beat_xeqt": alpha.get("XEQT.TO", 0) > 0 if "XEQT.TO" in alpha else None,
                "age_days": round(age_days, 1),
                "status": status,
                "scored_at": time.time(),
            }
        )
        if status in ("stopped_out", "target_hit", "horizon_closed"):
            scored_rec["exit_price"] = round(current, 4)
            scored_rec["exit_date"] = date.today().isoformat()
            scored_rec["return_pct"] = round(ret_pct, 2)

        scored.append(scored_rec)

    _CTX.mkdir(parents=True, exist_ok=True)
    with _SCORED_FILE.open("w", encoding="utf-8") as f:
        for r in scored:
            f.write(json.dumps(r) + "\n")

    return _summary(scored, skipped)


def get_track_record(windows: list[int] | None = None) -> dict:
    """Rolling per-window stats from scored file: 7/30/90/180d default."""
    if windows is None:
        windows = [7, 30, 90, 180]
    if not _SCORED_FILE.exists():
        return {"error": "no scored_recommendations.jsonl — run score_recommendations first"}

    recs = [json.loads(line) for line in _SCORED_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    now = time.time()
    out: dict = {"windows": {}}

    for days in windows:
        cutoff = now - days * 86400
        window_recs = [r for r in recs if r.get("ts", 0) >= cutoff]
        out["windows"][f"{days}d"] = _summary(window_recs, 0)

    out["total_logged"] = len(recs)
    out["as_of"] = now
    out["model_version"] = MODEL_VERSION
    return out


def _bucket_init() -> dict:
    return {"count": 0, "wins": 0, "returns": [], "alpha_spy": [], "alpha_xeqt": [], "target_hits": 0, "stop_hits": 0}


def _bucket_finalize(d: dict) -> dict:
    n = d["count"]
    if n == 0:
        return {"count": 0}
    return {
        "count": n,
        "win_rate_pct": round(d["wins"] / n * 100, 1),
        "avg_return_pct": round(sum(d["returns"]) / len(d["returns"]), 2) if d["returns"] else None,
        "avg_alpha_vs_spy_pct": round(sum(d["alpha_spy"]) / len(d["alpha_spy"]), 2) if d["alpha_spy"] else None,
        "avg_alpha_vs_xeqt_pct": round(sum(d["alpha_xeqt"]) / len(d["alpha_xeqt"]), 2) if d["alpha_xeqt"] else None,
        "target_hit_rate_pct": round(d["target_hits"] / n * 100, 1),
        "stop_hit_rate_pct": round(d["stop_hits"] / n * 100, 1),
    }


def _accumulate(d: dict, r: dict) -> None:
    d["count"] += 1
    if r.get("win"):
        d["wins"] += 1
    if r.get("unrealized_pct") is not None:
        d["returns"].append(r["unrealized_pct"])
    alpha = r.get("alpha_pct") or {}
    if "SPY" in alpha:
        d["alpha_spy"].append(alpha["SPY"])
    if "XEQT.TO" in alpha:
        d["alpha_xeqt"].append(alpha["XEQT.TO"])
    if r.get("status") == "target_hit":
        d["target_hits"] += 1
    elif r.get("status") == "stopped_out":
        d["stop_hits"] += 1


def _summary(scored: list[dict], skipped: int) -> dict:
    if not scored:
        return {"count": 0, "skipped": skipped}

    overall = _bucket_init()
    by_conv: dict[str, dict] = {}
    by_action: dict[str, dict] = {}
    by_skill: dict[str, dict] = {}
    by_conf_src: dict[str, dict] = {}

    for r in scored:
        _accumulate(overall, r)
        for key_field, bucket in (
            (r.get("conviction", "?"), by_conv),
            (r.get("action", "?"), by_action),
            (r.get("skill", "?"), by_skill),
            (r.get("confidence_source", "?"), by_conf_src),
        ):
            if key_field not in bucket:
                bucket[key_field] = _bucket_init()
            _accumulate(bucket[key_field], r)

    return {
        **_bucket_finalize(overall),
        "skipped": skipped,
        "by_conviction": {k: _bucket_finalize(v) for k, v in by_conv.items()},
        "by_action": {k: _bucket_finalize(v) for k, v in by_action.items()},
        "by_skill": {k: _bucket_finalize(v) for k, v in by_skill.items()},
        "by_confidence_source": {k: _bucket_finalize(v) for k, v in by_conf_src.items()},
    }


def _make_id(skill: str, ticker: str, action: str) -> str:
    raw = f"{skill}:{ticker}:{action}:{date.today().isoformat()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def batch_log_recommendations(
    skill: str,
    recs: list[dict],
    *,
    confidence_source: str = "experimental",
    sector_etf_by_symbol: dict[str, str] | None = None,
    benchmark_by_symbol: dict[str, str] | None = None,
    model_version: str | None = None,
) -> dict:
    """Bulk-log recommendations with one shared benchmark price fetch.

    Each rec dict must contain at minimum: symbol, action, conviction (or
    confidence), entry_price (or current_price), and optional thesis,
    horizon_days, target_pct, stop_pct, expected_upside_pct,
    expected_downside_pct, features, account.

    Skips actions in {HOLD, WATCH, NO_EDGE} unless explicitly opted in
    (these don't represent trade decisions worth tracking forward).
    Skips per-day duplicates by (skill, ticker, action) id to keep scheduler
    re-runs idempotent. Each logged rec carries an explicit benchmark_symbol
    for downstream alpha attribution; precedence is benchmark_by_symbol >
    sector_etf > DEFAULT_PRIMARY_BENCHMARK.

    Returns count summary so the caller can audit the cycle.
    """
    if not recs:
        return {"logged": 0, "skipped": 0, "duplicates": 0}

    sector_etf_by_symbol = sector_etf_by_symbol or {}
    benchmark_by_symbol = benchmark_by_symbol or {}
    benchmarks_entry = _capture_benchmark_prices(DEFAULT_BENCHMARKS)
    aux_cache: dict[str, dict] = {}

    logged = 0
    skipped = 0
    duplicates = 0
    failed: list[dict] = []

    _CTX.mkdir(parents=True, exist_ok=True)

    with _REC_FILE.open("a", encoding="utf-8") as f:
        for r in recs:
            action = (r.get("action") or "").upper()
            if action in ("HOLD", "WATCH", "NO_EDGE"):
                skipped += 1
                continue
            if action not in _VALID_ACTIONS:
                failed.append({"reason": f"invalid action {action}", "rec": r})
                continue

            ticker = (r.get("symbol") or r.get("ticker") or "").upper()
            if not ticker:
                failed.append({"reason": "missing symbol", "rec": r})
                continue

            rec_id = _make_id(skill, ticker, action)
            if _seen_today(rec_id):
                duplicates += 1
                continue

            conviction = (r.get("conviction") or r.get("confidence") or "MED").upper()
            if conviction in ("HIGH", "MEDIUM", "MED", "LOW"):
                conviction = "MED" if conviction == "MEDIUM" else conviction
            if conviction not in _VALID_CONV:
                conviction = "MED"

            entry_price = float(r.get("entry_price") or r.get("current_price") or 0.0)
            price_source = r.get("price_source") or "rec_cycle"

            sector_etf = sector_etf_by_symbol.get(ticker)
            primary_bench = benchmark_by_symbol.get(ticker) or sector_etf or DEFAULT_PRIMARY_BENCHMARK

            bench_entry = dict(benchmarks_entry)
            for aux in {sector_etf, primary_bench}:
                if not aux or aux in bench_entry:
                    continue
                if aux not in aux_cache:
                    aux_cache[aux] = _capture_benchmark_prices((aux,))[aux]
                bench_entry[aux] = aux_cache[aux]

            rationale = r.get("thesis") or " ".join(r.get("reasons") or []) or ""

            rec = {
                "id": rec_id,
                "date": date.today().isoformat(),
                "ts": time.time(),
                "skill": skill,
                "model_version": model_version or MODEL_VERSION,
                "ticker": ticker,
                "action": action,
                "conviction": conviction,
                "confidence_source": confidence_source,
                "horizon_days": r.get("horizon_days"),
                "thesis": (r.get("thesis") or rationale)[:500],
                "invalidation": r.get("invalidation"),
                "entry_price": round(entry_price, 4),
                "price_source": price_source,
                "target_pct": r.get("target_pct"),
                "stop_pct": r.get("stop_pct"),
                "expected_upside_pct": r.get("expected_upside_pct"),
                "expected_downside_pct": r.get("expected_downside_pct"),
                "account": r.get("account"),
                "sector_etf": sector_etf,
                "benchmark_symbol": primary_bench,
                "benchmarks_entry": bench_entry,
                "features": r.get("features") or {},
                "rationale_hash": hashlib.sha256(rationale.encode()).hexdigest()[:16],
                "status": "open",
                "exit_price": None,
                "exit_date": None,
                "return_pct": None,
                "win": None,
            }
            f.write(json.dumps(rec) + "\n")
            _mark_logged(rec_id)
            logged += 1

    return {"logged": logged, "skipped": skipped, "duplicates": duplicates, "failed": failed}


def get_ticker_history(symbol: str, n: int = 3) -> list[dict]:
    """Return last N scored recommendations for symbol, most recent first.

    Each entry: {date, action, conviction, entry_price, return_pct, alpha_xeqt, status, rationale}.
    Reads from recommendations.jsonl (scored records have return_pct set).
    Returns [] if no history found.
    """
    if not _REC_FILE.exists():
        return []
    sym = symbol.upper()
    rows: list[dict] = []
    try:
        lines = _REC_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("ticker", "").upper() != sym:
            continue
        if r.get("return_pct") is None and r.get("status") == "open":
            # include open recs without return yet
            pass
        rows.append(
            {
                "date": r.get("date", ""),
                "action": r.get("action", ""),
                "conviction": r.get("conviction", ""),
                "entry_price": r.get("entry_price"),
                "return_pct": r.get("return_pct"),
                "alpha_xeqt": (r.get("alpha") or {}).get("XEQT.TO"),
                "status": r.get("status", "open"),
                "rationale": (r.get("rationale") or "")[:120],
            }
        )
        if len(rows) >= n:
            break
    return rows
