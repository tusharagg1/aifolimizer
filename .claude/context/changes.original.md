# aifolimizer — Change Log

Append-only. Most recent at top.

---

## 2026-05-18 — Data Layer + Accuracy/Benchmarking Pass (Phase 1-6)

### Why
Competitive gap: no track record, single yfinance source, no alpha measurement, no trust signal. Added multi-source fallback, historical backtest of all 13 skills, forward paper-trade pipeline, alpha vs benchmarks, public TRACK_RECORD.md.

### Phase 1 — Multi-Source Data Router
- `backend/app/services/data_sources/` — new package with ABC + 5 adapters:
  - `base.py` — `DataSource` ABC, `PriceBar`, `Quote`, `Fundamentals`, `SourceUnavailable`
  - `yfinance_src.py` — primary (no key)
  - `stooq_src.py` — EOD fallback (needs `STOOQ_KEY`, free captcha)
  - `alphavantage_src.py` — fundamentals fallback (free 25/day, `ALPHA_VANTAGE_KEY`)
  - `finnhub_src.py` — quote + metrics fallback (free 60/min, `FINNHUB_KEY`)
  - `tiingo_src.py` — history fallback (free 50/hr, `TIINGO_KEY`)
- `backend/app/services/data_cache.py` — SQLite disk cache (`.cache/data.sqlite`, gitignored). Tables: quotes, history, fundamentals, source_stats. TTL-checked. `log_source_call` records every provider call for reliability reporting.
- `backend/app/services/data_router.py` — fallback chain router. `get_quote` → `get_history` → `get_fundamentals` each try chain in order, hit disk cache first. `get_quotes_batch` uses `yf.download` for N symbols — **13.5x faster** than serial (413ms vs 5585ms for 8 symbols). `prewarm()` batch-warms on startup.
- `.env` — added `ALPHA_VANTAGE_KEY`, `FINNHUB_KEY`, `TIINGO_KEY`, `STOOQ_KEY` slots (all blank, free-tier).
- `.gitignore` — added `backend/.cache/`, `recommendations.jsonl`, `scored_recommendations.jsonl`, `portfolio_history.jsonl`, `track_record_full.jsonl`.
- MCP: 2 new tools — `get_quote_with_source`, `get_data_source_reliability`.

### Phase 2 — Skill Backtester (Historical KPIs)
- `backend/app/services/skill_backtest.py` — codifies all 13 skills as deterministic Python rules (sma50, sma200, rsi_swing, macd, golden_cross, bollinger_revert, vol_cluster_avoid, momentum_faber, consensus_fade, buy_hold). `backtest_skill(skill, universe, lookback_days)` → `SkillBacktest` with CAGR, Sharpe, Sortino, max DD, hit-rate, num_trades, alpha vs SPY+XEQT. `backtest_all_skills()` runs all 13, persists JSON to `.cache/backtests/`.
- `.claude/context/backtest_results.md` — first KPI table (3yr, AAPL/MSFT/NVDA/XEQT/VFV universe).
- MCP: `get_skill_track_record(universe, lookback_days, fresh)`.

### Phase 3 — Forward Paper-Trade Pipeline
- `backend/app/services/paper_trade.py` — `log_recommendation(skill, ticker, action, conviction, rationale, target_pct, stop_pct)` appends to `recommendations.jsonl` with live entry price. `score_recommendations(max_age_days)` marks-to-market all open recs, flags stop-out/target-hit, writes `scored_recommendations.jsonl`. `get_track_record(windows)` returns rolling 7/30/90d win-rate + avg return by conviction.
- MCP: `log_recommendation`, `score_recommendations`, `get_live_track_record`.

### Phase 4 — Alpha Attribution + AUM Bench
- `backend/app/services/alpha_attribution.py` — `snapshot_equity(total_cad)` appends daily NAV to `portfolio_history.jsonl` (idempotent per day). `get_alpha_attribution(lookback_days)` loads snapshots, fetches SPY/XEQT/TSX/QQQ bars via router, computes annualized return, alpha, beta, R², info ratio, tracking error. Includes `_WS_MANAGED` dict of Wealthsimple Managed published profile returns (conservative/balanced/growth/aggressive/halal_growth, 1y/3y/5y).
- `main.py` — pre-warms quote cache for 10 common symbols on startup (background task, non-blocking).
- MCP: `snapshot_portfolio_equity`, `get_alpha_attribution`.

### Phase 5 — Trust Signal
- `backend/app/services/trust_report.py` — `generate_report()` writes `TRACK_RECORD.md` (public, committable) + `track_record_full.jsonl` (gitignored). Includes methodology, data-source table, backtest KPIs, live rec stats, source reliability, WS Managed comparison, audit trail.
- `TRACK_RECORD.md` — first version committed (git timestamp = tamper-evident).
- MCP: `generate_trust_report`.

### Phase 6 — Performance
- `data_router.get_quotes_batch` — 13.5x speedup (413ms vs 5585ms serial). Disk-cached, falls back to serial per-symbol on parse failure.
- MCP: `get_quotes_batch`.
- Startup pre-warm via `@app.on_event("startup")` (non-blocking `create_task`).

### MCP tool count: 22 → 32 (+10 new tools)

---

## 2026-05-17 — Optimization pass (Tier 1+2+3 — full audit ship)

### Why
Audit surfaced: crowding not on UI, alerts had no Task Scheduler, positioning feature without crowd_fade backtest, no PII filter tests despite NON-NEGOTIABLE rule. Pass closes all gaps end-to-end.

### Tier 1 — visibility + safety guardrails

- `.claude/skills/daily-briefing/SKILL.md` — morning-digest skill. Composes 7 MCP tools into ≤400-word brief. Sections: headline, what-changed, focus list, risks on radar, skipped. Auto-triggers on "morning briefing", "daily digest", "what changed overnight?". MCP `list_analysis_modes` 12 → 13 skills.
- **Crowding on dashboard**: `GET /ws/crowding` (top_n=15) wired to `positioning.get_positioning`. `PortfolioTable` "Crowding" column renders `consensus / neutral / contrarian · NN` badge (rose/slate/emerald) with hover tooltip showing inst%/short%/analysts/news 7d-30d counts. `lib/api.ts` gains `CrowdingMap` + `CrowdingSignal` types + `wsGetCrowding`. Dashboard fetches crowding in parallel on session change + refresh. Skills panel gained `/daily-briefing`, `/cash-deployment`, `/stock-compare`, `/earnings-postmortem`.
- **Alerts scheduler**: `backend/scripts/schedule_alerts.ps1` — registers per-user Scheduled Task running `run_alerts.py` every 30 min Mon-Fri 9:30–16:00. Flags: `-DryRun`, `-Unregister`. No admin required. `run_alerts.py` snapshots crowding for top 15 holdings (idempotent per-day) → builds regime-shift dataset for free.
- **pii_filter tests**: `backend/tests/test_pii_filter.py` — 5 tests covering `filter_portfolio` + `filter_user_context`. Asserts PII keys never appear at any nesting depth. 5/5 passing pytest 9.0.3.
- `backend/requirements.txt` — adds `pytest>=9.0.0` + `diskcache>=5.6.0`.

### Tier 2 — validate positioning thesis + honest backtests

- `backend/app/services/backtest.py`:
  - 2 new strategies: `crowd_fade` (sma_cross long-only, skip consensus-crowded; flat 0% on skip) + `crowd_buy` (sma_cross only on contrarian-flagged symbols).
  - `tx_cost_bps` param (default 5 bps/leg) — deducted on every entry+exit+close-out. Pass `tx_cost_bps=0` for old behavior.
  - `_run_strategy_on_window` helper — single dispatch for all 5 strategies. Enables walk-forward reuse.
  - Smoke: AAPL 180d, `crowd_fade` skipped → 0% return → -12.35% delta vs buy_hold (opportunity cost surfaced).
- `backend/app/services/positioning.py`:
  - `snapshot_to_history(symbols)` — appends `{date, symbol, crowding_score, crowding_label}` JSONL to crowding_history.jsonl. Idempotent per-day.
  - `detect_regime_shifts(symbols, lookback_days=30, score_delta_threshold=25.0)` — compares first vs last score in window, returns sorted by |delta|.
  - 2 new MCP tools: `snapshot_positioning_history` + `get_crowding_shifts`.

### Tier 3 — honest math + cross-process cache

- `backend/app/services/backtest.py` — `walk_forward=True` splits window into in-sample (first `train_frac=0.7`) + out-of-sample. Output adds `in_sample`, `out_of_sample`, `oos_minus_is_pct`. RSI/SMA params fixed (no fit) → exposes regime decay, not parameter overfit. Cache key includes walk_forward + train_frac.
- `backend/app/services/cache_layer.py` — thin `diskcache.Cache` at `.claude/context/.diskcache/` (200 MB cap, gitignored). API: `cache_get(ns, key)`, `cache_set(ns, key, value, ttl_seconds)`. Pickled, SQLite-backed, thread+process-safe. Failures swallowed.
- `positioning.py` + `fundamentals.py` — L1 (in-process dict) + L2 (diskcache, shared FastAPI ↔ MCP). Cold MCP start hits L2 if FastAPI warmed within 6h TTL.

### MCP tool count
17 → 20 (new: `snapshot_positioning_history`, `get_crowding_shifts`).

### Verified
- pytest 5/5 (pii_filter)
- backtest smoke: `crowd_fade` + `tx_cost_bps=5` + `walk_forward=True` produce expected shapes
- positioning snapshot: idempotent, regime detector reads back correctly
- frontend: PortfolioTable compiles, dashboard fetch wires in parallel

### Skipped
- LLM router untouched (user opted to keep 4-provider fallback)
- Skill consolidation refactor (low value / high refactor risk)

### Next
- Restart MCP server → discover `snapshot_positioning_history` + `get_crowding_shifts`
- Run `.\scripts\schedule_alerts.ps1` once to activate daily crowding snapshot
- After a week of snapshots, `get_crowding_shifts` surfaces real regime changes for daily-briefing

---

## 2026-05-16 — Positioning / crowding signals (AI-consensus risk guard)

### Why
Goldman/BlackRock 2025: AI-driven retail+quant flows pile into same names → late entries into consensus trades have negative expected alpha. Defensive guard for stock-analysis/cash-deployment/adversarial-research.

### Added
- `backend/app/services/positioning.py` — per-symbol crowding signal:
  - `institutional_ownership_pct`, `short_pct_float`, `insider_ownership_pct`, `analyst_count`, `analyst_recommendation` (yfinance.info)
  - `headlines_7d`, `headlines_30d`, `headline_velocity_ratio` (per-day 7d vs 30d ratio)
  - `crowding_score` 0-100 — weighted (inst 35%, short 20%, analyst 20%, news 25%)
  - `crowding_label` `consensus` (≥70) / `neutral` / `contrarian` (≤30)
  - `consensus_flag`, `contrarian_flag` booleans
  - Cache 6h, parallel fetch ThreadPoolExecutor(8)
- `backend/mcp_server.py` — `get_positioning_signals(account_id, symbols)`. Defaults to top 15 holdings if `symbols=[]`. Tool count 17 → 18.
- Skill wiring:
  - `stock-analysis` — Stage 7 tool call, CROWDING output section (items 19-22), 3 new gotchas
  - `cash-deployment` — Stage 7 tool call, Setup Score /5 → /6, new "Consensus-crowded" disqualified bucket, 2 gotchas
  - `adversarial-research` — Stage 1 6th tool call, **third Consensus sub-agent** in Stage 2, Stage 4 adds "Consensus / crowding read" line, 3 gotchas
- `CLAUDE.md` — investor profile gains "Crowding awareness" rule; tool+skill tables updated

### Smoke test
- AAPL: inst 65.7%, short 0.9%, 43 analysts, velocity 4.29 → crowding 85.0 → `consensus` ✓
- NVDA: inst 70.6%, short 1.2%, 57 analysts, velocity 4.29 → crowding 88.3 → `consensus` ✓
- XEQT.TO: all fields null (ETF gap) → crowding 39.0 → `neutral` ✓

### Known limits
- yfinance.news max ~10 articles → velocity ratio caps artificially high (consistent bias). Flagged in gotchas.
- TSX/.TO sparse on institutional+analyst fields — label unreliable when 3+ inputs null.
- Crowding ≠ overvaluation. Adjusts conviction, doesn't invert call.
- Reddit/X not measured — retail surge under-counted.

---

## 2026-05-16 — Backtesting service + `backtest_portfolio` MCP tool

### Added
- `backend/app/services/backtest.py` — per-position rule-replay over historical OHLCV. Strategies:
  - `buy_hold` — passive baseline
  - `rsi_swing` — buy RSI<30, sell RSI>70
  - `sma_cross` — long when close > SMA50
- Metrics per (symbol, strategy): `total_return_pct`, `cagr_pct`, `sharpe` (rf=0, ann.√252), `max_drawdown_pct`, `num_trades`, `days`.
- Portfolio aggregation: weighted total/CAGR per strategy, worst single-position drawdown.
- `delta_vs_buy_hold_pct` — negative = active rules underperformed passive.
- Cache: 1h per `(symbol, strategy, lookback_days)`.
- `backend/mcp_server.py` — `backtest_portfolio(account_id, symbols, lookback_days, strategies, top_n)`. Defaults to top 15 holdings, 365d, all 3 strategies. `lookback_days` clamped 30..730.

### Smoke test
- AAPL 365d: buy_hold +42.7% (sharpe 1.7, DD -13.8%); rsi_swing +11.9% (-30.8 vs buy_hold); sma_cross +32.9% (-9.8). Both active strategies lose to passive — expected for momentum names in uptrend.

### Skipped
- Transaction costs / slippage
- Position sizing / stop-loss layers
- Walk-forward / out-of-sample split

---

## 2026-05-16 — Alerts service + ntfy.sh push + 2 new MCP tools

### Added
- `backend/app/services/alerts.py` — rule evaluator (6 rules) + ntfy.sh dispatcher + JSONL history. Dedup: same `(rule, symbol, day)` fires once. State `.claude/context/alerts_state.json` (auto-trimmed 7d). History `.claude/context/alerts.jsonl` (append-only).
- `backend/scripts/run_alerts.py` — CLI runner. `--dry-run` skips push, still logs. `--account TFSA` filters.
- MCP: `get_triggered_alerts(since_hours, limit)` reads history; `run_alerts_now(account_id, price_drop_pct, dry_run)` evaluates live.

### Rules
- `price_drop_intraday` (default −5%)
- `rsi_oversold` (≤30) / `rsi_overbought` (≥75) on top 15 holdings
- `earnings_imminent` (next 3 days)
- `concentration_single` (>10%) / `concentration_sector` (>35%)

### Config
- `NTFY_TOPIC` in `backend/.env`. If unset, alerts only logged. Treat as private.
- ntfy.sh free tier — no signup, install ntfy mobile app.

### Schedule
- Manual: `cd backend && .venv/Scripts/python scripts/run_alerts.py`
- Cron (Linux/Mac) or Task Scheduler (Windows) every 1h during market hours.

### Smoke test
- day_change_pct=-7.5 + weight=15 fires `price_drop_intraday` + `concentration_single`. 2 history entries, 0 deduped.

---

## 2026-05-16 — New skill: `cash-deployment`

### Added
- `.claude/skills/cash-deployment/SKILL.md` — routes uninvested cash to holdings ranked by setup quality. Excludes concentration-flagged, stage 3/4, overbought, deteriorating. Outputs Setup Score /5 table + dollar/share allocation.
- `backend/mcp_server.py` — `list_analysis_modes` → 12 skills.
- Pure reuse: calls `get_profile`, `get_portfolio`, `get_concentration_warnings`, `get_fundamentals`, `get_technicals`.

### Triggers
"where do I put my cash?", "I have $X to invest", "deploy my cash", "add to my best names", "what should I buy with my settled funds?"

### Gotchas
- Cash is account-specific — no cross-account deploy without contribution-room impact
- USD-in-CAD-account FX spread (~1.5%) for .TO buys
- Settled vs unsettled cash (T+1 on equity sales)
- Superficial-loss-rule check if cash came from tax-loss sale
- Cap any single add at 5% even for "aggressive growth" lens
- Don't double-count recurring auto-deposits going into same ticker

---

## 2026-05-16 — New skill: `earnings-postmortem` + MCP tool `get_earnings_results`

### Added
- `.claude/skills/earnings-postmortem/SKILL.md` — post-report breakdown. Covers headline beat/miss, 4-quarter trend, guidance shift, analyst reaction, valuation re-rate, Canadian tax-aware action rec.
- `backend/mcp_server.py` — `get_earnings_results(account_id, symbols, quarters=4)`. Cached 12h.
- `backend/app/services/fundamentals.py` — `get_earnings_history(symbols, quarters)` via yfinance `Ticker.earnings_history`. Parallel ThreadPoolExecutor(max_workers=8). Output: `{quarter, eps_actual, eps_estimate, eps_difference, surprise_pct, outcome}`.
- MCP `list_analysis_modes` → 11 skills.

### Triggers
"did X beat?", "what did Y report?", "how did earnings go?", pasted earnings reports, "reported", "earnings call", "Q1 results"

### Smoke test
`get_earnings_history(['AAPL', 'MSFT'], 4)` → 4 quarters each, all "beat", surprise_pct 3–13%. Source: yfinance Ticker.earnings_history (no lxml dependency).

### Gotchas
- EPS only — no revenue in earnings_history. Revenue beats need WebSearch.
- TSX (.TO) coverage sparse — fallback to WebSearch + IR press release.
- "Beat" strict to EPS — company can beat EPS via buybacks while missing revenue.
- Pre-earnings revisions matter: beat vs lowered estimate weaker than vs raised.
- Forward guidance NOT in yfinance — WebSearch required.

---

## 2026-05-16 — New skill: `stock-compare`

### Added
- `.claude/skills/stock-compare/SKILL.md` — Goldman/Citadel side-by-side matchup. Strategy lens (growth/income/value) + horizon. Reuses `get_fundamentals`, `get_technicals`, `get_news_headlines` with two tickers. No new MCP tool.
- Output: verdict-first → side-by-side matrix (15 rows) → moat → catalysts/risks → valuation → TA setup → Canadian tax-aware placement rec.
- Gotchas: cache-staleness symmetry, mismatched fiscal years, asymmetric analyst-target upside, US/.TO coverage gap, US-div withholding in Non-Reg, beta benchmark mismatch.
- `backend/mcp_server.py` — `list_analysis_modes` → 10 skills.

### Triggers
"X vs Y", "which is better A or B", "should I pick X or Y", side-by-side matchup requests

---

## 2026-05-14 — Phase 6: Performance pass

### Backend
- `app/api/ws.py`
  - `_PORTFOLIO_CACHE` key → `(session_id, account_id)` — per-tab caching
  - `asyncio.Lock` per cache key with double-checked locking — concurrent fetches dedupe to one round-trip
  - `/portfolio` routed through `_get_portfolio` (was bypassing cache)
- `app/services/market_data.py`
  - `_TICKER_CACHE` (5-min TTL) for `yf.Ticker.info` + `fast_info`. Measured: 2.0s → 0.0s cached.
- `app/services/technicals.py`
  - `get_technicals` batches into one `yf.download(group_by="ticker")`. Measured: 5 syms in 0.5s vs ~1.4s serial.
- `app/services/fundamentals.py`
  - `ThreadPoolExecutor(max_workers=8)` for uncached symbols. 3 HTTP calls per symbol overlap. Measured: 5 syms in 1.2s.

### Frontend
- `components/CountdownLabel.tsx` — isolates 5s tick from dashboard tree (was re-rendering all charts/tables every 5s).
- `React.memo` on: `AllocationChart`, `HealthScoreWidget`, `MacroWidget`, `BenchmarkWidget`, `OptimizerWidget`, `AlertsPanel`, `RecommendationsPanel`.
- `lib/api.ts` — all `wsGet*` helpers accept optional `signal?: AbortSignal`.
- `app/dashboard/page.tsx`
  - Per-loader `AbortController` — new fetch cancels prior in-flight (fixes account-tab race).
  - Stale-while-revalidate: skeleton only on initial load.
  - Cleanup effect aborts all in-flight fetches on unmount.

---

## 2026-05-14 — Phase 5: Multi-Provider LLM Narrative Layer

### Goal
AI-generated narrative per recommendation card — no Anthropic key. Router auto-selects best free provider at runtime.

### New service
- `backend/app/services/llm_router.py`
  - 4 providers in priority order: GitHub Models → Gemini → OpenRouter → Qwen
  - All free tiers (GitHub Pro qualifies)
  - Per-provider: 2 consecutive failures → 5-min cooldown → retry
  - 30-min narrative cache keyed by (symbol, score, market_regime)
  - `generate_narratives_batch()`: concurrent, semaphore (4 max)
  - Graceful: returns `None` per symbol when all providers fail

### Updated `backend/app/core/config.py`
- Added: `github_token`, `google_api_key`, `openrouter_api_key`, `dashscope_api_key` (all optional)

### New endpoints
- `GET /ws/ai-narratives` — `{narratives: {symbol: text}, providers: [...]}`
- `GET /ws/llm-status` — available providers

### Updated frontend
- `api.ts`: `NarrativesResponse`, `wsGetNarratives()`, `wsGetLlmStatus()`
- `RecommendationsPanel.tsx`: AI narrative per card (italic, indigo left-border). Pulse skeleton while loading. Provider badge.
- `dashboard/page.tsx`: Narratives load 3s after render; re-fetch on refresh with same stagger.

### .env additions needed (at least one):
```
GITHUB_TOKEN=ghp_...           # GitHub Pro — best free option
GOOGLE_API_KEY=AIza...         # Google AI Studio free tier
OPENROUTER_API_KEY=sk-or-...   # OpenRouter free models
DASHSCOPE_API_KEY=sk-...       # Qwen via Aliyun
```

---

## 2026-05-14 — Phase 4: Auto-Recommendation Dashboard

### Goal
Always-on BUY/SELL/HOLD/WATCH without manual Claude commands. Rule-based engine, no Anthropic API key.

### New backend service
- `backend/app/services/recommendations.py` — scoring engine (0-10 per position)
  - Technical: Minervini stage, RSI, MACD histogram, SMA200 trend, 52w range
  - Fundamental: analyst rec/target, EPS growth, short interest, revenue growth
  - Macro: market regime (bull/bear × fear), VIX, Fear & Greed
  - Position: weight concentration, total return
  - Thresholds: ≥7.5=BUY, ≥5.5=HOLD, ≥3.5=WATCH, <3.5=SELL
  - ETFs skip fundamental signals

### Updated services
- `macro.py` — `fear_and_greed()` (CNN Fear & Greed, free HTTP, 1h cache). Merged into `market_breadth()`.
- `market_data.py` — `day_change_cad` on `PortfolioSummary`.
- `portfolio.py` — `day_change_cad: float = 0.0`.

### New REST endpoints
- `GET /ws/recommendations` — sorted SELL→BUY→WATCH→HOLD
- `GET /ws/macro` — market breadth + FRED snapshot

### New frontend components
- `RecommendationsPanel.tsx` — grouped by action, color-coded cards. Score bar, analyst upside%, Minervini badge, RSI badge, top 3 reasons.
- `MacroWidget.tsx` — regime badge + signal text. VIX, SPY vs SMA200, Fear & Greed, FRED rates.

### Dashboard layout
1. Summary cards: Portfolio Value, Day Change (CAD), Total Return, Book Cost, Cash
2. Health score + Macro + Allocation chart (3-col)
3. Recommendations panel (full width)
4. Alerts panel
5. Holdings table
6. Price chart
7. Skills panel (collapsible, default collapsed)

### Updated `frontend/lib/api.ts`
- `Recommendation`, `MacroSnapshot` interfaces
- `PortfolioSummary.day_change_cad`
- `wsGetRecommendations()` + `wsGetMacro()`

---

## 2026-05-14 — Phase 3: Market Breadth + Minervini Stage Analysis

### New MCP tool
- `get_market_breadth()` — VIX, SPY vs SMA200, composite market_regime + regime_signal. Cached 1h. No key.

### Updated services
- `macro.py` — `market_breadth()` using yfinance `^VIX` + SPY 1y daily OHLCV.
- `technicals.py` — Minervini: `stage` (1-4), `minervini_score` (0-7), `sma_150`, `sma_200_slope_pct`, `week52_high`, `week52_low`, `pct_from_52w_high/low`.

### New REST endpoint
- `GET /ws/market-breadth`

### Updated skills
- `macro-impact` — step 4 calls `get_market_breadth`; step 7 uses `market_regime` for risk stance.
- `stock-analysis` — technical section includes Minervini stage/score + 52w context.
- `sector-rotation` — step 4 calls `get_market_breadth`; rotation conviction calibrated to regime.

### Source
Evaluated claudemarketplaces.com skills — all redundant with yfinance/ta-lib stack. Only unique value: market breadth + Minervini.

---

## 2026-05-14 — Phase 2: Real-time Dashboard + Multi-agent Auto-analysis

### New backend services
- `health_score.py` — rule-based health score (0-100, grade A-F). No external calls.
- `crypto_data.py` — CoinGecko v3 free, no key. Live CAD prices, 24h/7d/30d change, ATH drawdown, 20 crypto symbols. 5-min cache.

### New REST endpoints
- `GET /ws/health-score`, `GET /ws/alerts`, `GET /ws/crypto`

### New MCP tools
- `get_crypto_data(account_id, symbols)` — symbols=[] auto-detects from portfolio.

### New frontend components
- `HealthScoreWidget.tsx` — grade badge + 5-dimension breakdown.
- `AlertsPanel.tsx` — dismissable alert cards (high/warning/info).

### Updated dashboard
- Health widget in summary grid. Alerts auto-load. Auto-refresh 5 min. Skill panel click-to-copy. Parallel loads.

### Updated all 9 skills
- All call `mcp__aifolimizer__get_profile` as step 1. Account types + capital always from `get_profile`.

### New tooling
- `backend/scripts/build_skills.py` — lists MCP tools + skill health, scaffolds new SKILL.md.

---

## 2026-05-14 — Phase 1 Enhancement: Data Foundation

### Added
- `fundamentals.py` — yfinance.info: P/E, EPS, div yield, payout, market cap, earnings date, analyst targets, beta, short interest. 6h cache.
- `technicals.py` — `ta` lib: SMA20/50/200, RSI(14), MACD, Bollinger Bands, volume SMA, trend signal. 1h cache.
- `news.py` — yfinance news, 5 articles/ticker, 30-min cache.
- `mcp_server.py` — 4 new tools: `get_fundamentals`, `get_technicals`, `get_earnings_calendar`, `get_news_headlines`.
- `app/api/ws.py` — 4 new endpoints: `/ws/fundamentals`, `/ws/technicals`, `/ws/earnings-calendar`, `/ws/price-history`.
- `backend/requirements.txt` — `ta>=0.11.0` (NOT pandas-ta — incompatible with Python 3.14).
- `.claude/skills/` — 8 skills moved from `~/.claude/skills/` to project-level.
- `.claude/context/` — created (changes.md + architecture.md).

### Updated skills
- `stock-analysis` — calls `get_fundamentals` + `get_technicals` + `get_news_headlines`.
- `earnings-analyzer` — calls `get_earnings_calendar` + `get_fundamentals`.
- `dividend-strategy` — calls `get_fundamentals` for div yield/payout.

### New skills
- `adversarial-research` — parallel bull/bear sub-agent pipeline, probability-weighted synthesis.

---

## 2026-05-XX — MVP Build (Initial)

### Built
- `wealthsimple.py` — MFA-aware login, 8h token TTL in RAM.
- `pii_filter.py` — strips account IDs, names, emails before MCP response.
- `mcp_server.py` — 9 tools: get_profile, get_portfolio, get_xray, get_concentration_warnings, get_tax_loss_candidates, get_risk_metrics, get_correlation_matrix, get_macro_snapshot, list_analysis_modes.
- FastAPI REST API (`main.py` + `app/api/ws.py`) — login, OTP, portfolio, profile endpoints.
- `market_data.py` — live prices, sectors, day change.
- `macro.py` — FRED: Fed funds, 10Y, CPI, CAD/USD, BoC rate. 12h cache.
- `quant.py` — Sharpe, Sortino, VaR 95%, correlation matrix, pure Python.
- `portfolio_analytics.py` — ETF X-ray, concentration warnings, tax-loss candidates.
- 8 institutional analysis skills at `~/.claude/skills/` — BlackRock, Bridgewater, Goldman+Citadel, McKinsey, Harvard, JPMorgan, Renaissance, Canadian tax-loss.
- Next.js 14 dashboard — login (MFA), portfolio table, allocation chart, skill directory.
