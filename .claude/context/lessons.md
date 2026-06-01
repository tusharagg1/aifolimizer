# aifolimizer - Lessons

Append-only. Short rule + source incident per entry. Read at session startup.

---

## Python

- **Local-variable scoping is whole-function.** If `name = ...` appears anywhere in function body, every earlier reference treated as local → `UnboundLocalError`. Scan whole function for assignments before referencing name. *(Source: `recommendations.py:407` referenced `em` before line 419 assigned it - crashed `_score_position` every position, emptied recs panel.)*

- **Cache failure modes.** When wrapping external call in cache, decide: store empty on failure (fast recovery, data gap for TTL) or skip caching on failure (retry next call). Pick deliberately. *(Source: `_ticker_meta` 5-min cache stores empty meta if yfinance fails - masks outages full 5 min.)*

- **Locks need double-check.** When using `asyncio.Lock` to dedupe concurrent fetches, re-check cache inside lock - another waiter may have populated while waiting. *(Source: `_get_portfolio` lock pattern.)*

## yfinance

- **Batch when possible.** `yf.download([syms], group_by="ticker", threads=True)` one HTTP regardless of symbol count. Serial loops easy bottleneck.

- **`group_by="ticker"` returns MultiIndex even for 1 symbol.** Slicer code must handle both single-level and MultiIndex columns. *(Source: technicals batch refactor - single-symbol path returned `'Close'` KeyError until `_slice_symbol` handled both.)*

- **`yf.Ticker(sym).info` is 1 HTTP call.** `.calendar` and `.dividends` each another. Three HTTP calls per symbol if touching all three.

## Frontend (React / Next.js) - ARCHIVED

> Frontend removed; project backend-only now (Claude Code / Claude Desktop drive analysis via MCP). Lessons kept in case dashboard revived.

- **AllocationChart sums must use `_cad` suffix.** Mixing native `market_value` (USD or CAD) with CAD total under-sizes USD slices ~1.38x. *(Source: pre-existing bug in `AllocationChart.tsx:20`.)*

- **`useEffect(() => () => abort, [])` interacts with strict mode.** Empty-deps cleanup runs at dev strict-mode unmount, aborts in-flight fetches. Verify second mount re-fires loaders.

- **`React.memo` only helps if parent passes stable prop refs.** Arrays/objects rebuilt every render bypass memo. For props from `setState(apiResponse)`, ref changes per fetch - memo skips re-renders when no fetch happened (e.g. countdown ticks).

- **SetState in effect body trips React 19 lint.** Use derived `now` state ticked by interval, compute remaining inline. *(Source: `CountdownLabel.tsx` first version.)*

## Workflow

- **Compile-clean ≠ working.** Import-check + tsc + lint pass without exercising code. Call changed function with realistic input before declaring done. *(Source: 9-step perf pass shipped clean lints; `em` UnboundLocalError caught only by one mock-portfolio invocation.)*

- **Pre-existing bugs surface during refactors.** Fresh integration tests (cache cleared, services restarted) expose latent bugs hidden behind cached responses. Budget time.

- **Skip skeletons on background refresh.** Set `loading=true` only when state null - prevents flicker on refresh while still showing skeleton on first load. Don't blanket-apply - account-switch needs skeleton.

## Cross-cutting

- **Shipped ≠ user-facing.** Backend feature returning data via MCP/REST but not on dashboard invisible to user. When new field added, ask "does UI surface this?" before declaring done. *(Source: crowding score shipped May 16 via positioning.py + MCP, but PortfolioTable didn't render full day until audit caught gap.)*

- **Scheduling is part of feature.** `run_alerts.py` nobody runs is dead code. Same for crowding snapshots - without scheduler, regime-shift detector has nothing to detect. Ship scheduler artifacts in same PR as underlying logic. *(Source: alerts shipped May 16, no scheduler until May 17 audit.)*

- **Honest backtests need fees + walk-forward as default flags.** Zero-friction backtests overstate strategy returns 5-30%+ in regime-favorable lookbacks. Every strategy must accept `tx_cost_bps` and `walk_forward`.

- **Two-tier cache pattern.** L1 = in-process dict (hot path). L2 = diskcache (survives restarts + cross-process FastAPI ↔ MCP sharing). Never replace L1 with L2 - pickled SQLite reads 1000x slower. Always L1 → L2 → fetch.

- **Multi-process app = multiple cold starts.** MCP and FastAPI two Python processes with independent dict caches. In-process cache only helps process that fetched. Cross-process diskcache makes second process see first's work.

- **Verify isinstance/subclass claims with repro before writing into commit messages.** np.float64 IS subclass of float (issubclass(np.float64, float) is True). `isinstance(x, (int, float))` check accepts numpy scalars. *(Source: 8a3ac35 commit message claimed isinstance silently rejected np.float64 - false. New try-cast harmless superset, premise wrong.)*

- **Cross-currency invariants: read producer before writing "fix" math in consumer.** wealthsimple.py:369 stores `acc["cash"] = cad_cash + usd_cash * fx` (CAD-equivalent total) before per_account propagation. `cash_available_usd` RAW USD kept for per-currency display only - adding to `cash_available` double-counts. fa17419 added comment claiming wrong invariant; d7b6ffd weaponized comment to introduce high-severity double-count. e3a65d8 reverted both. Always trace producer → field semantics → all consumers before adding currency arithmetic. *(Source: cash_pct double-count saga, May 29 2026.)*

- **Caveat for self-correcting fixes: comment IS part of fix.** Wrong comment near math worse than no comment - actively misleads next reviewer/editor. Omit invariant claim or verify across all producer/consumer call sites before committing.

- **MCP server cold-start gates session tool exposure.** Eager top-level imports of heavy modules (yfinance, ta, pandas) in MCP server entry point can push cold start past Claude Code's handshake window. `claude mcp list` may still show ✓ Connected later, but tools never made it into session's tool schema → invocations fail with "tool not found." Fix: `_LazyModule` proxy defers `importlib.import_module` until first attribute access; tool bodies unchanged. *(Source: aifolimizer mcp_server.py 5.0s → 1.1s cold start, May 29 2026.)*

- **Pivot ≠ spot. Never infer current_price from pivot levels.** `get_technicals` pivots are computed from PRIOR session H/L/C — NOT the live quote. When `current_price` is null (partial yfinance fetch / pre-market / data race), call `get_quote_with_source` for spot before producing any trade plan. *(Source: stock-analysis incident Jun 2026 — inferred spot from pivot, real spot ~9% lower after a sector-gap event. Trade plan stale.)*

- **`claude mcp list` shows server health, not session schema availability.** Connected server can have zero tools exposed in current session if schema fetch raced startup. Verify with actual tool call, not list output.

## Strategy / Behavioral Analysis

- **Bias audit must not symmetrize all biases.** When the original analysis cites concrete evidence (specific track-record patterns, cycle math, horizon-bounded math), an abstract "recency anchor" critique should not silently soften the verdict. Weigh evidence quality, don't treat both sides as equal.

- **Specific evidence beats abstract framework.** When two analyses contradict, weight the one grounded in concrete data over the one citing only general behavioral-finance framework names.

- **Plan flip between iterations damages trust more than a single wrong call.** Restoring an earlier verdict requires explicit acknowledgment + change log + lesson, never a silent revert.

- **Behavioral guardrails must err harder toward mechanical discipline when the user's revealed pattern is hold-and-hope.** Generic "don't sell at the bottom" advice is dangerous for hold-pattern investors. Mechanical sell rules beat nuanced "what if it recovers" hedging for that risk profile.

- **Self-learning: name the specific bias dynamic, not the generic principle.** "Audit overcorrected the original verdict without re-weighing concrete evidence" is useful. "Be consistent" is not.

- **Repo files stay clean of session-specific data.** No tickers, no $ amounts, no holdings, no drawdown %, no balances, no account labels. Lessons capture process rules. Session context belongs in transient memory.