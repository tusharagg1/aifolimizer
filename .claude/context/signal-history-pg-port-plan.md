# signal_history JSONL→PG port — implementation plan

Status: in progress (2026-06-11). Author decision: docker-always-on, single-user.
Goal: make Postgres `signal_history` the single source of truth for BOTH signal
snapshots AND realized returns, then retire the parallel JSONL store.

## Why (verified findings)

1. Two parallel stores today:
   - PG `signal_history`: written by `scheduler.py` `_persist_integrated_signals`
     → `signals_repo.insert_signal`. Keyed by real `tenant_hash`, full `ts`.
     Read by `get_integrated_signals`, `get_signal_history`, `weights_tuner`.
   - JSONL `.claude/context/signal_history.jsonl`: written by sync
     `recommendations.get_recommendations` → `signal_history.log_signal`
     (ThreadPool). Nested `outcomes.h{1,3,5,10,21,42,63}`. Read by 5 analytics
     fns → 6 MCP tools.
2. **PG `realized_return_{1,5,21,63}d` are NEVER filled.** `backfill_realized_returns`
   returns candidate rows only; no caller computes/writes returns. Confirmed in
   changes.md:29 — calibration.py was deliberately pointed back at JSONL for this.
   ⇒ JSONL `score_horizons` (fetches bars, computes returns) is the ONLY real
   realized-outcome source. PG analytics (continuous aggregate, weights_tuner
   attribution_by_source) are starved/null today.

## Design decisions

- **Scheduler is the sole writer.** Interactive sync `log_signal` write is removed
  (guarded: JSONL only when `get_pool() is None`). This dissolves the
  sync-thread → async-pool boundary problem entirely.
- **Store entry_price in PG** so the backfill reproduces the JSONL return
  definition exactly (`ret = (close[t+H] - entry)/entry`, sign-flipped SELL/TRIM).
- **7 horizons**: add `realized_return_{3,10,42}d` columns to match JSONL set.
- Reuse the proven compute: `signal_history._close_at_offset` +
  `data_router.get_history` (sync; run in executor from scheduler).
- Single-tenant: interactive/migration use the `legacy` tenant_hash
  (`sha1(b"legacy")[:16]`), already in migrate script.

## Workstreams (execute in order; TDD each)

### 1. Schema (schema.sql + signals_repo allow-set)
- `ALTER TABLE signal_history ADD COLUMN IF NOT EXISTS entry_price NUMERIC;`
- `... ADD COLUMN IF NOT EXISTS realized_return_3d NUMERIC;` (+10d, +42d)
- Extend the `{realized_return_1d,5d,21d,63d}` allow-sets in signals_repo
  (`backfill_realized_returns`, `attribution_by_source`) to include 3/10/42.
- Idempotent — safe re-run on container init.

### 2. Scheduler persists entry_price
- `_persist_integrated_signals`: pass `entry_price=rec.get("current_price")`.
- `insert_signal`: add `entry_price` param + column in INSERT.

### 3. PG realized-return backfill (the net-new infra)
New `signals_repo` fns:
- `rows_needing_backfill(horizon_days, batch_limit)` → rows where
  `realized_return_{H}d IS NULL AND ts < now() - H days` with
  `tenant_hash, symbol, ts, entry_price, action`.
- `set_realized_return(tenant_hash, symbol, ts, horizon_days, ret_pct)` →
  atomic `UPDATE ... SET realized_return_{H}d = $`.
New service `signal_backfill.py` (pure-ish orchestrator):
- group candidates by symbol, fetch bars once/symbol (`get_history`),
  `_close_at_offset(bars, ts.date(), H)`, compute ret, sign-flip SELL/TRIM,
  call `set_realized_return`. Mirrors `score_horizons` logic exactly.
- Pure helper `compute_return(entry, exit_close, action)` → unit-testable.

### 4. Wire backfill into scheduler/worker nightly job
- Add task calling `signal_backfill.run(horizons=(1,3,5,10,21,42,63))` after
  signal persistence (or its own daily slot). get_history is sync → executor.

### 5. Port 5 analytics readers to SQL
Rewrite to query PG (new `signals_repo` read fns returning rows with
realized_return_* + features), keeping the SAME return shape:
- `accuracy_report` (get_signal_accuracy)
- `signal_decay_curve` (get_signal_decay_curve) — needs all 7 cols
- `per_signal_source_attribution` (get_signal_source_attribution)
- `calibrate_confidence` (calibrate_confidence_labels)
- `calibrate_thresholds` (calibrate_signal_thresholds)
- `score_horizons` MCP tool (score_signal_horizons) → delegates to backfill (#3).
Win = `realized_return_{H}d > 0` (already sign-flipped at backfill).

### 6. MCP tool rewiring (mcp_server.py)
Point the 6 tools at the PG-backed fns. Keep `init_pool()` guard + JSONL
fallback when pool is None (no-docker safety).

### 7. Guard interactive JSONL write
`recommendations.py:996`: only `log_signal` when `get_pool() is None`.

### 8. One-time migration (migrate_jsonl_to_postgres.py)
Extend `import_signal_history`: also carry `entry_price` and map
`outcomes.h{N}.ret_pct` → `realized_return_{N}d` (the ones with matching cols).

### 9. Tests + quality gates
- `compute_return` pure unit tests (long win/loss, short sign-flip).
- backfill orchestrator with monkeypatched `get_history` + fake repo (pattern
  from test_weights_tuner.py — monkeypatch repo fns; no live PG needed).
- analytics-over-PG with a fake pool / injected rows.
- `pytest` (from repo root) + `ruff check .` + `pyright`. Import-check
  mcp_server + scheduler. Exercise backfill compute with real-ish bars.

## Rollback
Every read path keeps `if get_pool() is None: <JSONL fallback>`. Schema ALTERs
are additive (no drops). JSONL files untouched until #7; even then, only the
write is guarded, files remain for migration/audit.
