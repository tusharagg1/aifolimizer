# aifolimizer — Change Log

Append-only. Most recent at top.

---

## 2026-05-29 — Claude-run skill automation (headless + fault-tolerant)

### Why
Reasoning skills only ran when interactively in Claude Code. Goal: run them
automatically *by Claude*, push to Telegram, survive reboots; free-LLM agent
route kept as the fallback if Claude Pro is lost.

### Built / changed
- **Unified WS session file** (fault-tolerance must-fix): `mcp_server._SESSION_FILE`
  and `mcp_login.py` now both use `~/.aifolimizer/ws_session.json` — the same file
  `wealthsimple._persist_session` rewrites on token refresh. Prevents a rotated
  refresh token from orphaning the file the MCP server reads (headless runs now
  survive for the full refresh-token lifetime; MFA only on first login / forced
  re-auth). `mcp_login.py` writes the canonical `{email, session_json, saved_utc}`
  schema + chmod 0600.
- **main.py lifespan**: calls `wealthsimple.restore_session()` at startup so the
  scheduler re-seeds the session after a restart and keeps the token warm.
- **MCP tools**: `get_earnings_calendar(account_id, symbols=[])` now unions
  watchlist/extra symbols + adds `held` flag (Option A). New `get_watchlist`.
  New `get_trade_ideas(top_n, include_watchlist, min_risk_reward)` — reuses
  `recommendations.get_recommendations` (no duplicated scoring), filters to
  actionable + R:R floor, returns entry/stop/target/RR/conviction.
- **New skills**: `top-trades-today` (composer: get_trade_ideas + crowding +
  catalyst guards), `position-review` (router -> earnings-analyzer /
  earnings-postmortem / adversarial-research / stock-analysis -> HOLD/TRIM/SELL,
  logs decisions; respects subagent-nesting limit in sweeps).
- **Automation scripts**: `backend/scripts/send_telegram.py` (plain-text,
  4096-char chunked; verified real send), `backend/scripts/run_skill_fallback.py`
  (free-LLM tier via agent_registry runner), `scripts/run-claude-skill.ps1`
  (Claude primary -> free-LLM fallback -> Telegram, WS-session preflight, run log),
  `scripts/register-skill-task.ps1`, `scripts/install-backend-service.ps1` (NSSM),
  `scripts/AUTOMATION.md` runbook.

### Resilience model
Two-tier: **Claude** (`claude -p`, Pro) primary; **free LLMs** (existing backend
agent route) fallback when Pro/auth unavailable. New composer skills have no
free-LLM runner (Claude-only). Keep agent_registry + skill_llm_runner.

### Verified
Import/compile-clean (mcp_server, main, mcp_login, both py scripts); PS scripts
parse; send_telegram real send EXIT=0; fallback exits 4 cleanly with no session.
Live `get_trade_ideas` / full `claude -p` run pending user MFA login (Phase 0).

### Known follow-ups
MCP cold import ~5s (eager service imports) -> `mcp list` health-check can time
out; harmless for `claude -p`. Lazy-import pass = perf-optimizer task. Optional
phases not built: MFA-relay over Telegram, watchlist earnings in daily-briefing,
event-driven Claude skills, hosted backend.

---

## 2026-05-28 — Backtest + Technicals + Geopolitical Upgrades

### What

**backtest.py + MCP `backtest_portfolio`:**
- `profit_factor` (gross_profit / gross_loss) added to per-symbol output for all signal strategies
- `insufficient_trades_warning` flag (True when num_trades < 150 — statistically insufficient)
- `insufficient_trades_count` added to portfolio_totals per strategy
- `exclude_weekdays: list[int]` param — skip entries on specified weekdays (0=Mon). Pass `[0]` to test "no Monday entries" filter from backtesting research
- `max_hold_days: int` param — force-exit positions after N calendar days regardless of signal. Adds time-based exits to reduce overnight/gap exposure
- Both params wired through entire call chain and cache key

**technicals.py:**
- `_candle_patterns()` — detects doji, hammer, shooting star, bullish/bearish engulfing, marubozu on last 2 bars. Returns `{detected: [...], signal: bullish|bearish|indecision|neutral}`. Added to `_compute_from_df` output as `candle_patterns` field
- `get_technicals_mtf()` — multi-timeframe analysis. Fetches 1d/1wk/1mo data per symbol via yfinance, runs `_compute_from_df` for each TF, returns key signals per TF + `mtf_confluence` dict (`trend_alignment`, `signal_alignment`, `overall`). Cached 1h
- New MCP tool `get_technicals_mtf` with `timeframes: list[str]` param

**geopolitical.py (new service):**
- `get_geopolitical_signals(lookback_hours=24)` — queries GDELT 2.0 Doc API (free, no key) for conflict/trade/sanctions/energy themes
- Returns `global_tension_index` (0-100), per-region scores (Americas, Europe, Asia_Pacific, Middle_East, Emerging), `hot_regions` (score >= 60), `categories_detected`, `market_implications` (ETF/sector impacts)
- New MCP tool `get_geopolitical_signals` — use alongside `get_macro_snapshot` in macro-impact analysis

### Why
Multiple external sources (GeoTrade architecture, backtesting research, ICT curriculum) independently identified: (1) missing profit_factor/trade-count quality gates, (2) single-timeframe blind spot, (3) zero geopolitical risk signal. All gaps closed without new paid data sources.

---

## 2026-05-28 — Quant Anomaly Skills: PEAD + Momentum + TOTM

### What
3 evidence-based market anomaly signals added as skills:

- **`pead-tracker`** — Post-Earnings Announcement Drift (Bernard & Thomas 1989). Scans holdings for earnings surprises in last 85 calendar days, computes remaining drift window (60 trading days), estimates residual edge by firm size (large 2.8%, mid 4.3%, small 5.1%). Uses `get_earnings_results` + `get_technicals` + `get_fundamentals`.
- **`momentum-scanner`** — 12-Month Momentum (Jegadeesh & Titman 1993) + 52-Week High Effect (George & Hwang 2004). Ranks all holdings by composite score (50% 12m return from `backtest_portfolio`, 30% 52wk high proximity from `get_technicals`, 20% Minervini score). Flags laggards as trim candidates; crowding-suppresses add signals.
- **Turn-of-Month signal in `daily-briefing`** — McConnell & Xu (1897–2005): all positive equity returns concentrated in last trading day of month + first 3 trading days. Added TOTM window check to catalyst day section.

### Why
Portfolio analytics lacked systematic exploitation of documented academic anomalies. All three use existing MCP tools with zero new data sources.

---

## 2026-05-18 — Data Layer + Accuracy/Benchmarking Pass (Phase 1-6)

### Why
No track record, single yfinance source, no alpha measurement, no trust signal. Added multi-source fallback, historical backtest of all 13 skills, forward paper-trade pipeline, alpha vs benchmarks, public TRACK_RECORD.md.

### Phase 1 — Multi-Source Data Router
- `backend/app/services/data_sources/` — new package with ABC + 5 adapters:
  - `base.py` — `DataSource` ABC, `PriceBar`, `Quote`, `Fundamentals`, `SourceUnavailable`
  - `yfinance_src.py` — primary (no key)
  - `stooq_src.py` — EOD fallback (`STOOQ_KEY`, free captcha)
  - `alphavantage_src.py` — fundamentals fallback (free 25/day, `ALPHA_VANTAGE_KEY`)
  - `finnhub_src.py` — quote + metrics fallback (free 60/min, `FINNHUB_KEY`)
  - `tiingo_src.py` — history fallback (free 50/hr, `TIINGO_KEY`)
- `backend/app/services/data_cache.py` — SQLite disk cache (`.cache/data.sqlite`, gitignored). Tables: quotes, history, fundamentals, source_stats. TTL-checked. `log_source_call` records every provider call for reliability reporting.
- `backend/app/services/data_router.py` — fallback chain router. `get_quote` → `get_history` → `get_fundamentals` each try chain in order, hit disk cache first. `get_quotes_batch` uses `yf.download` for N symbols — **13.5x faster** than serial (413ms vs 5585ms for 8 symbols). `prewarm()` batch-warms on startup.
- `.env` — added `ALPHA_VANTAGE_KEY`, `FINNHUB_KEY`, `TIINGO_KEY`, `STOOQ_KEY` slots (all blank, free-tier).
- MCP: 2 new tools — `get_quote_with_source`, `get_data_source_reliability`.

### Phase 2 — Skill Backtester (Historical KPIs)
- `backend/app/services/skill_backtest.py` — codifies all 13 skills as deterministic Python rules. `backtest_skill(skill, universe, lookback_days)` → `SkillBacktest` with CAGR, Sharpe, Sortino, max DD, hit-rate, num_trades, alpha vs SPY+XEQT. `backtest_all_skills()` runs all 13, persists JSON to `.cache/backtests/`.
- MCP: `get_skill_track_record(universe, lookback_days, fresh)`.

### Phase 3 — Forward Paper-Trade Pipeline
- `backend/app/services/paper_trade.py` — `log_recommendation` appends to `recommendations.jsonl` with live entry price. `score_recommendations` marks-to-market open recs, flags stop-out/target-hit. `get_track_record` returns rolling 7/30/90d win-rate + avg return by conviction.
- MCP: `log_recommendation`, `score_recommendations`, `get_live_track_record`.

### Phase 4 — Alpha Attribution + AUM Bench
- `backend/app/services/alpha_attribution.py` — `snapshot_equity` appends daily NAV to `portfolio_history.jsonl` (idempotent per day). `get_alpha_attribution` computes annualized return, alpha, beta, R², info ratio, tracking error vs SPY/XEQT/TSX/QQQ. Includes `_WS_MANAGED` published profile returns (conservative/balanced/growth/aggressive/halal_growth, 1y/3y/5y).
- `main.py` — pre-warms quote cache for 10 common symbols on startup (background task, non-blocking).
- MCP: `snapshot_portfolio_equity`, `get_alpha_attribution`.

### Phase 5 — Trust Signal
- `backend/app/services/trust_report.py` — writes `TRACK_RECORD.md` (public) + `track_record_full.jsonl` (gitignored). Includes methodology, data-source table, backtest KPIs, live rec stats, source reliability, WS Managed comparison, audit trail.
- MCP: `generate_trust_report`.

### Phase 6 — Performance
- `data_router.get_quotes_batch` — 13.5x speedup (413ms vs 5585ms serial). Disk-cached, falls back to serial on parse failure.
- MCP: `get_quotes_batch`.
- Startup pre-warm via `@app.on_event("startup")` (non-blocking `create_task`).

### MCP tool count: 22 → 32 (+10)

---

## 2026-05-17 — Optimization Pass (Tier 1+2+3)

### Why
Audit surfaced: crowding not on UI, alerts had no Task Scheduler, positioning feature without crowd_fade backtest, no PII filter tests.

### Tier 1 — Visibility + Safety
- `.claude/skills/daily-briefing/SKILL.md` — morning digest, 7 MCP tools, ≤400 words. Auto-triggers on "morning briefing", "daily digest", "what changed overnight?".
- **Crowding on dashboard**: `GET /ws/crowding` (top_n=15). `PortfolioTable` "Crowding" column renders `consensus / neutral / contrarian · NN` badge (rose/slate/emerald) + hover tooltip (inst%/short%/analysts/news). Dashboard fetches crowding in parallel on session change + refresh.
- **Alerts scheduler**: `backend/scripts/schedule_alerts.ps1` — registers Windows Scheduled Task running `run_alerts.py` every 30 min Mon-Fri 9:30–16:00. Flags: `-DryRun`, `-Unregister`. No admin required. Snapshots crowding for top 15 holdings (idempotent per-day) → regime-shift dataset.
- **pii_filter tests**: `backend/tests/test_pii_filter.py` — 5 tests, `filter_portfolio` + `filter_user_context`. Asserts PII keys never appear at any nesting depth. 5/5 passing pytest 9.0.3.
- `backend/requirements.txt` — adds `pytest>=9.0.0` + `diskcache>=5.6.0`.

### Tier 2 — Validate Positioning Thesis
- `backend/app/services/backtest.py`:
  - 2 new strategies: `crowd_fade` (sma_cross, skip consensus-crowded) + `crowd_buy` (sma_cross, contrarian-only).
  - `tx_cost_bps` param (default 5 bps/leg).
  - `_run_strategy_on_window` helper for walk-forward reuse.
- `backend/app/services/positioning.py`:
  - `snapshot_to_history(symbols)` — appends `{date, symbol, crowding_score, crowding_label}` JSONL (idempotent per-day).
  - `detect_regime_shifts(symbols, lookback_days=30, score_delta_threshold=25.0)` — compares first vs last score in window.
  - 2 new MCP tools: `snapshot_positioning_history` + `get_crowding_shifts`.

### Tier 3 — Honest Math + Cross-Process Cache
- `backtest.py` — `walk_forward=True` splits window: in-sample (first `train_frac=0.7`) + out-of-sample. Output adds `in_sample`, `out_of_sample`, `oos_minus_is_pct`.
- `backend/app/services/cache_layer.py` — thin `diskcache.Cache` at `.claude/context/.diskcache/` (200 MB cap, gitignored). `cache_get/cache_set(ns, key, value, ttl_seconds)`. Pickled, SQLite-backed, thread+process-safe.
- `positioning.py` + `fundamentals.py` — L1 (in-process dict) + L2 (diskcache, shared FastAPI ↔ MCP). Cold MCP start hits L2 if FastAPI warmed within 6h TTL.

### MCP tool count: 17 → 20

### Verified
- pytest 5/5 (pii_filter)
- backtest smoke: `crowd_fade` + `tx_cost_bps=5` + `walk_forward=True` correct shapes
- positioning snapshot: idempotent, regime detector reads back correctly
- frontend: PortfolioTable compiles, dashboard fetch parallel

---

## 2026-05-16 — Positioning / Crowding Signals

### Why
Goldman/BlackRock 2025: AI-driven flows pile into same names → late entries into consensus trades have negative expected alpha. Guard for stock-analysis/cash-deployment/adversarial-research.

### Added
- `backend/app/services/positioning.py` — per-symbol crowding signal:
  - `institutional_ownership_pct`, `short_pct_float`, `insider_ownership_pct`, `analyst_count`, `analyst_recommendation`
  - `headlines_7d`, `headlines_30d`, `headline_velocity_ratio`
  - `crowding_score` 0-100 — weighted (inst 35%, short 20%, analyst 20%, news 25%)
  - `crowding_label`: `consensus` (≥70) / `neutral` / `contrarian` (≤30)
  - Cache 6h, parallel fetch ThreadPoolExecutor(8)
- MCP: `get_positioning_signals(account_id, symbols)`. Defaults to top 15 holdings if `symbols=[]`. Tool count 17 → 18.
- Skill wiring: stock-analysis (Stage 7), cash-deployment (Setup Score /5→/6, consensus disqualified bucket), adversarial-research (Stage 1 6th tool, Consensus sub-agent in Stage 2).

### Smoke test
- AAPL: inst 65.7%, short 0.9%, 43 analysts → crowding 85.0 → `consensus` ✓
- NVDA: inst 70.6%, short 1.2%, 57 analysts → crowding 88.3 → `consensus` ✓
- XEQT.TO: all null (ETF gap) → crowding 39.0 → `neutral` ✓

### Known limits
- yfinance.news max ~10 articles → velocity ratio caps artificially high (consistent bias)
- TSX/.TO sparse on institutional+analyst fields — label unreliable when 3+ inputs null
- Crowding ≠ overvaluation. Adjusts conviction, doesn't invert call.

---

## 2026-05-16 — Backtesting Service + `backtest_portfolio` MCP tool

### Added
- `backend/app/services/backtest.py` — per-position rule-replay over historical OHLCV. Strategies: `buy_hold`, `rsi_swing` (RSI<30 buy/RSI>70 sell), `sma_cross` (close > SMA50).
- Metrics: `total_return_pct`, `cagr_pct`, `sharpe`, `max_drawdown_pct`, `num_trades`, `days`.
- Portfolio aggregation: weighted total/CAGR per strategy, worst single-position drawdown. `delta_vs_buy_hold_pct`.
- Cache: 1h per `(symbol, strategy, lookback_days)`.
- MCP: `backtest_portfolio(account_id, symbols, lookback_days, strategies, top_n)`. Defaults: top 15 holdings, 365d, all 3 strategies. `lookback_days` clamped 30..730.

### Smoke test
AAPL 365d: buy_hold +42.7% (sharpe 1.7, DD -13.8%); rsi_swing +11.9% (-30.8 vs buy_hold); sma_cross +32.9% (-9.8). Both active lose to passive — expected for momentum names in uptrend.

---

## 2026-05-16 — Alerts Service + ntfy.sh Push

### Added
- `backend/app/services/alerts.py` — 6 rules + ntfy.sh dispatcher + JSONL history. Dedup: same `(rule, symbol, day)` fires once. State `.claude/context/alerts_state.json` (auto-trimmed 7d). History `.claude/context/alerts.jsonl`.
- `backend/scripts/run_alerts.py` — CLI runner. `--dry-run` skips push. `--account TFSA` filters.
- MCP: `get_triggered_alerts(since_hours, limit)` + `run_alerts_now(account_id, price_drop_pct, dry_run)`.

### Rules
`price_drop_intraday` (−5%), `rsi_oversold` (≤30), `rsi_overbought` (≥75), `earnings_imminent` (next 3 days), `concentration_single` (>10%), `concentration_sector` (>35%)

### Config
`NTFY_TOPIC` in `backend/.env`. Unset → alerts only logged. ntfy.sh free tier, no signup.

---

## 2026-05-16 — New skill: `cash-deployment`

### Added
- `.claude/skills/cash-deployment/SKILL.md` — routes uninvested cash to holdings ranked by setup quality. Excludes concentration-flagged, stage 3/4, overbought, deteriorating. Outputs Setup Score /5 table + dollar/share allocation.
- MCP `list_analysis_modes` → 12 skills.
- Triggers: "where do I put my cash?", "I have $X to invest", "deploy my cash", "what should I buy with my settled funds?"

---

## 2026-05-16 — New skill: `earnings-postmortem` + MCP `get_earnings_results`

### Added
- `.claude/skills/earnings-postmortem/SKILL.md` — post-report breakdown: headline beat/miss, 4-quarter trend, guidance shift, analyst reaction, valuation re-rate, Canadian tax-aware action rec.
- MCP: `get_earnings_results(account_id, symbols, quarters=4)`. Cached 12h.
- `backend/app/services/fundamentals.py` — `get_earnings_history(symbols, quarters)` via yfinance `Ticker.earnings_history`. Parallel ThreadPoolExecutor(max_workers=8).
- Triggers: "did X beat?", "what did Y report?", "how did earnings go?", "Q1 results"
- Smoke: AAPL/MSFT 4 quarters each, all "beat", surprise_pct 3–13%.
- Gotcha: EPS only — no revenue. TSX/.TO coverage sparse.

---

## 2026-05-16 — New skill: `stock-compare`

### Added
- `.claude/skills/stock-compare/SKILL.md` — Goldman/Citadel side-by-side matchup. Strategy lens (growth/income/value) + horizon. Reuses `get_fundamentals`, `get_technicals`, `get_news_headlines` for two tickers.
- Output: verdict-first → 15-row matrix → moat → catalysts/risks → valuation → TA setup → Canadian tax-aware placement.
- MCP `list_analysis_modes` → 10 skills.
- Triggers: "X vs Y", "which is better A or B", "should I pick X or Y"

---

## 2026-05-14 — Phase 6: Performance Pass

### Backend
- `app/api/ws.py` — `_PORTFOLIO_CACHE` key → `(session_id, account_id)` per-tab. `asyncio.Lock` per key with double-checked locking — concurrent fetches dedupe to one round-trip.
- `app/services/market_data.py` — `_TICKER_CACHE` (5-min TTL) for `yf.Ticker.info` + `fast_info`. 2.0s → 0.0s cached.
- `app/services/technicals.py` — batches into one `yf.download(group_by="ticker")`. 5 syms in 0.5s vs ~1.4s serial.
- `app/services/fundamentals.py` — `ThreadPoolExecutor(max_workers=8)` for uncached symbols. 5 syms in 1.2s.

### Frontend
- `components/CountdownLabel.tsx` — isolates 5s tick (was re-rendering all charts/tables every 5s).
- `React.memo` on: `AllocationChart`, `HealthScoreWidget`, `MacroWidget`, `BenchmarkWidget`, `OptimizerWidget`, `AlertsPanel`, `RecommendationsPanel`.
- `lib/api.ts` — all `wsGet*` helpers accept optional `signal?: AbortSignal`.
- `app/dashboard/page.tsx` — per-loader `AbortController` (new fetch cancels prior in-flight). Stale-while-revalidate: skeleton only on initial load. Cleanup effect aborts in-flight on unmount.

---

## 2026-05-14 — Phase 5: Multi-Provider LLM Narrative Layer

### Goal
AI-generated narrative per recommendation card — no Anthropic key. Router auto-selects best free provider at runtime.

### New service
- `backend/app/services/llm_router.py` — 4 providers: GitHub Models → Gemini → OpenRouter → Qwen. Per-provider: 2 consecutive failures → 5-min cooldown → retry. 30-min narrative cache keyed by (symbol, score, market_regime). `generate_narratives_batch()`: concurrent, semaphore (4 max). Returns `None` per symbol when all providers fail.

### Updated `backend/app/core/config.py`
Added: `github_token`, `google_api_key`, `openrouter_api_key`, `dashscope_api_key` (all optional)

### Endpoints
- `GET /ws/ai-narratives` — `{narratives: {symbol: text}, providers: [...]}`
- `GET /ws/llm-status` — available providers

### Frontend
- `RecommendationsPanel.tsx`: AI narrative per card (italic, indigo left-border). Pulse skeleton while loading. Provider badge.
- `dashboard/page.tsx`: Narratives load 3s after render; re-fetch on refresh with same stagger.

### .env additions (at least one required):
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
- `backend/app/services/recommendations.py` — scoring engine (0-10):
  - Technical: Minervini stage, RSI, MACD histogram, SMA200 trend, 52w range
  - Fundamental: analyst rec/target, EPS growth, short interest, revenue growth
  - Macro: market regime (bull/bear × fear), VIX, Fear & Greed
  - Position: weight concentration, total return
  - Thresholds: ≥7.5=BUY, ≥5.5=HOLD, ≥3.5=WATCH, <3.5=SELL. ETFs skip fundamental signals.

### Updated services
- `macro.py` — `fear_and_greed()` (CNN Fear & Greed, free HTTP, 1h cache). Merged into `market_breadth()`.
- `market_data.py` — `day_change_cad` on `PortfolioSummary`.

### Endpoints
- `GET /ws/recommendations` — sorted SELL→BUY→WATCH→HOLD
- `GET /ws/macro` — market breadth + FRED snapshot

### New frontend
- `RecommendationsPanel.tsx` — grouped by action, color-coded cards. Score bar, analyst upside%, Minervini badge, RSI badge, top 3 reasons.
- `MacroWidget.tsx` — regime badge + signal text. VIX, SPY vs SMA200, Fear & Greed, FRED rates.

---

## 2026-05-14 — Phase 3: Market Breadth + Minervini Stage Analysis

### New MCP tool
- `get_market_breadth()` — VIX, SPY vs SMA200, composite market_regime + regime_signal. Cached 1h.

### Updated services
- `macro.py` — `market_breadth()` using yfinance `^VIX` + SPY 1y daily OHLCV.
- `technicals.py` — Minervini: `stage` (1-4), `minervini_score` (0-7), `sma_150`, `sma_200_slope_pct`, `week52_high`, `week52_low`, `pct_from_52w_high/low`.

### New REST endpoint: `GET /ws/market-breadth`

### Updated skills
- `macro-impact` — step 4 calls `get_market_breadth`; step 7 uses `market_regime` for risk stance.
- `stock-analysis` — technical section includes Minervini stage/score + 52w context.
- `sector-rotation` — step 4 calls `get_market_breadth`; rotation conviction calibrated to regime.

---

## 2026-05-14 — Phase 2: Real-time Dashboard + Multi-agent Auto-analysis

### New backend services
- `health_score.py` — rule-based health score (0-100, grade A-F). No external calls.
- `crypto_data.py` — CoinGecko v3 free, no key. Live CAD prices, 24h/7d/30d change, ATH drawdown, 20 crypto symbols. 5-min cache.

### New REST endpoints: `GET /ws/health-score`, `GET /ws/alerts`, `GET /ws/crypto`
### New MCP tools: `get_crypto_data(account_id, symbols)` — symbols=[] auto-detects from portfolio.

### New frontend
- `HealthScoreWidget.tsx` — grade badge + 5-dimension breakdown.
- `AlertsPanel.tsx` — dismissable alert cards (high/warning/info).
- Health widget in summary grid. Alerts auto-load. Auto-refresh 5 min. Skill panel click-to-copy. Parallel loads.

### Updated all 9 skills: all call `mcp__aifolimizer__get_profile` as step 1.
### New tooling: `backend/scripts/build_skills.py` — lists MCP tools + skill health, scaffolds new SKILL.md.

---

## 2026-05-14 — Phase 1: Data Foundation

### Added
- `fundamentals.py` — yfinance.info: P/E, EPS, div yield, payout, market cap, earnings date, analyst targets, beta, short interest. 6h cache.
- `technicals.py` — `ta` lib: SMA20/50/200, RSI(14), MACD, Bollinger Bands, volume SMA, trend signal. 1h cache.
- `news.py` — yfinance news, 5 articles/ticker, 30-min cache.
- MCP: 4 new tools: `get_fundamentals`, `get_technicals`, `get_earnings_calendar`, `get_news_headlines`.
- REST: 4 new endpoints: `/ws/fundamentals`, `/ws/technicals`, `/ws/earnings-calendar`, `/ws/price-history`.
- `backend/requirements.txt` — `ta>=0.11.0` (NOT pandas-ta — incompatible with Python 3.14).
- `.claude/skills/` — 8 skills moved from `~/.claude/skills/` to project-level.

### Updated skills
- `stock-analysis` — calls `get_fundamentals` + `get_technicals` + `get_news_headlines`.
- `earnings-analyzer` — calls `get_earnings_calendar` + `get_fundamentals`.
- `dividend-strategy` — calls `get_fundamentals` for div yield/payout.

### New skills: `adversarial-research` — parallel bull/bear sub-agent pipeline, probability-weighted synthesis.

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
- 8 institutional analysis skills at `~/.claude/skills/`.
- Next.js 14 dashboard — login (MFA), portfolio table, allocation chart, skill directory.
