# aifolimizer — Change Log

Append-only. Most recent at top.

---

## 2026-05-16 — Positioning / crowding signals (AI-consensus risk guard)

### Why
Goldman / BlackRock 2025 research: AI-driven retail + quant flows pile into the same names, making late entries into consensus trades have negative expected alpha. Defensive guard for stock-analysis / cash-deployment / adversarial-research skills.

### Added
- `backend/app/services/positioning.py` — per-symbol crowding signal:
  - `institutional_ownership_pct`, `short_pct_float`, `insider_ownership_pct`, `analyst_count`, `analyst_recommendation` (yfinance.info)
  - `headlines_7d`, `headlines_30d`, `headline_velocity_ratio` (per-day 7d vs 30d ratio from yfinance.news timestamps)
  - `crowding_score` 0-100 — weighted (inst 35%, short 20%, analyst 20%, news 25%)
  - `crowding_label` `consensus` (≥70) / `neutral` / `contrarian` (≤30)
  - `consensus_flag`, `contrarian_flag` booleans
  - Cache 6h, parallel fetch ThreadPoolExecutor(8)
- `backend/mcp_server.py` — `get_positioning_signals(account_id, symbols)` MCP tool. Defaults to top 15 holdings if `symbols=[]`. Total tool count 17 → 18.
- Skill wiring:
  - `stock-analysis` SKILL.md — Stage 7 tool call, output section CROWDING (items 19-22), 3 new gotchas
  - `cash-deployment` SKILL.md — Stage 7 tool call, Setup Score expanded /5 → /6, new disqualified bucket "Consensus-crowded", 2 new gotchas
  - `adversarial-research` SKILL.md — Stage 1 6th tool call, **third Consensus sub-agent** in Stage 2 (marginal-buyer thesis lens), Stage 4 output adds "Consensus / crowding read" line, 3 new gotchas
- `CLAUDE.md` — investor profile gains "Crowding awareness" rule; MCP tool table + skill table + `list_analysis_modes` updated

### Smoke test
- AAPL: inst 65.7%, short 0.9%, 43 analysts, velocity 4.29 → crowding 85.0 → `consensus` ✓
- NVDA: inst 70.6%, short 1.2%, 57 analysts, velocity 4.29 → crowding 88.3 → `consensus` ✓
- XEQT.TO: all fields null (ETF coverage gap) → crowding 39.0 → `neutral` (graceful fallback) ✓

### Known limits
- yfinance.news returns max ~10 articles regardless of timespan → headline velocity ratio caps artificially high (consistent bias, not noise). Acceptable; flagged in stock-analysis gotchas.
- TSX / .TO tickers sparse on institutional + analyst fields — crowding label unreliable when 3+ inputs null. Flagged in gotchas.
- Crowding ≠ overvaluation. The flag adjusts conviction, doesn't invert the call.
- Reddit / X chatter not measured — retail surge under-counted.

### Next
- Restart MCP server for Claude Code to discover `get_positioning_signals`
- (Optional) Add positioning-aware backtest strategy `crowd_fade` to `backtest.py` to validate the thesis on user's universe
- (Optional) Persist crowding history to detect score changes (regime shifts) over time

---

## 2026-05-16 — Backtesting service + `backtest_portfolio` MCP tool

### Added
- `backend/app/services/backtest.py` — per-position rule-replay over historical OHLCV. Strategies:
  - `buy_hold` — passive baseline
  - `rsi_swing` — buy RSI<30, sell RSI>70
  - `sma_cross` — long when close > SMA50
- Metrics per (symbol, strategy): `total_return_pct`, `cagr_pct`, `sharpe` (rf=0, ann.√252), `max_drawdown_pct`, `num_trades`, `days`.
- Portfolio aggregation: weighted total / CAGR per strategy, worst single-position drawdown.
- `delta_vs_buy_hold_pct` — negative means active rules underperformed passive (the honest answer most of the time).
- Cache: 1h per `(symbol, strategy, lookback_days)`.
- `backend/mcp_server.py` — `backtest_portfolio(account_id, symbols, lookback_days, strategies, top_n)`. Defaults to top 15 holdings, 365d, all 3 strategies. `lookback_days` clamped 30..730.

### Smoke test
- AAPL 365d: buy_hold +42.7% (sharpe 1.7, DD -13.8%); rsi_swing +11.9% (-30.8 vs buy_hold); sma_cross +32.9% (-9.8). Both active strategies lose to passive in this regime — expected for momentum names in uptrend.

### Skipped
- Transaction costs / slippage (overstates strategy returns ~5-15bps/trade)
- Position sizing / stop-loss layers
- Walk-forward / out-of-sample split

---

## 2026-05-16 — Alerts service + ntfy.sh push + 2 new MCP tools

### Added
- `backend/app/services/alerts.py` — rule evaluator (6 rules) + ntfy.sh dispatcher + JSONL history reader. Dedup: same `(rule, symbol, day)` only fires once. State file `.claude/context/alerts_state.json` (auto-trimmed to 7d). History `.claude/context/alerts.jsonl` (append-only).
- `backend/scripts/run_alerts.py` — CLI runner. `--dry-run` skips push but still logs history. `--account TFSA` filters. Reads cached WS session from `.ws_session.json`.
- `backend/mcp_server.py` — `get_triggered_alerts(since_hours, limit)` reads history; `run_alerts_now(account_id, price_drop_pct, dry_run)` evaluates live and dispatches.

### Rules shipped
- `price_drop_intraday` (default −5%)
- `rsi_oversold` (≤30) / `rsi_overbought` (≥75) on top 15 holdings
- `earnings_imminent` (next 3 days)
- `concentration_single` (>10%) / `concentration_sector` (>35%)

### Config
- New env var: `NTFY_TOPIC` in `backend/.env`. If unset, alerts only logged (no push). Treat as private — anyone with the topic URL can read your alerts.
- ntfy.sh free tier — no signup, install ntfy mobile app, subscribe to topic.

### Schedule
- Manual: `cd backend && .venv/Scripts/python scripts/run_alerts.py`
- Cron (Linux/Mac) or Task Scheduler (Windows) every 1h during market hours.

### Smoke test
- Synthetic portfolio with day_change_pct=-7.5 + weight=15 fires both `price_drop_intraday` and `concentration_single`. Dispatch wrote 2 history entries, deduped 0.

---

## 2026-05-16 — New skill: `cash-deployment` (add-to-winners with discipline)

### Added
- `.claude/skills/cash-deployment/SKILL.md` — routes uninvested cash to existing holdings ranked by setup quality. Excludes concentration-flagged, stage 3/4, overbought, deteriorating names. Outputs Setup Score /5 table + dollar/share allocation per ticker
- `backend/mcp_server.py` — `list_analysis_modes` updated to 12 skills; `cash_deployment` entry added
- Pure reuse: no new MCP tool. Calls `get_profile`, `get_portfolio`, `get_concentration_warnings`, `get_fundamentals`, `get_technicals`

### Triggers (skill description)
"where do I put my cash?", "I have $X to invest", "deploy my cash", "add to my best names", "what should I buy with my settled funds?"

### Gotchas captured
- Cash is account-specific — no cross-account deploy without contribution-room impact
- USD-in-CAD-account FX spread (~1.5%) for .TO buys
- Settled vs unsettled cash (T+1 on equity sales)
- Superficial-loss-rule check if cash came from a tax-loss sale
- Cap any single add at 5% even for "aggressive growth" lens
- Don't double-count recurring auto-deposits already going into the same ticker

---

## 2026-05-16 — New skill: `earnings-postmortem` + new MCP tool `get_earnings_results`

### Added
- `.claude/skills/earnings-postmortem/SKILL.md` — post-report breakdown skill. Covers headline beat/miss, 4-quarter trend table, management guidance shift, analyst reaction, valuation re-rate, Canadian tax-aware action recommendation
- `backend/mcp_server.py` — new `get_earnings_results(account_id, symbols, quarters=4)` MCP tool returning last N quarters of EPS estimate/actual/surprise/outcome per ticker. Cached 12h (reported data is immutable)
- `backend/app/services/fundamentals.py` — `get_earnings_history(symbols, quarters)` using yfinance `Ticker.earnings_history`. Parallel via ThreadPoolExecutor(max_workers=8). Normalized output: `{quarter, eps_actual, eps_estimate, eps_difference, surprise_pct, outcome}`
- MCP `list_analysis_modes` updated to 11 skills; `earnings_postmortem` entry added

### Triggers (skill description)
"did X beat?", "what did Y report?", "how did earnings go?", pasted earnings reports, words like "reported", "earnings call", "Q1 results"

### Smoke test
`get_earnings_history(['AAPL', 'MSFT'], 4)` returned 4 quarters each, all "beat" outcomes, surprise_pct in 3-13% range. Source: yfinance Ticker.earnings_history (no lxml dependency — different code path than `earnings_dates`)

### Gotchas captured in skill
- EPS only — no revenue figure in earnings_history. Revenue beats/misses need WebSearch
- TSX (.TO) coverage sparse — fallback to WebSearch + IR press release
- "Beat" outcome strict to EPS — company can beat EPS via buybacks while missing revenue
- Pre-earnings consensus revisions matter: beat vs lowered estimate weaker than vs raised
- Forward guidance NOT in yfinance — WebSearch required

---

## 2026-05-16 — New skill: `stock-compare` (head-to-head A vs B)

### Added
- `.claude/skills/stock-compare/SKILL.md` — Goldman/Citadel-style side-by-side matchup. Strategy lens (growth/income/value) + horizon. Reuses `get_fundamentals`, `get_technicals`, `get_news_headlines` with two tickers in one call. No new MCP tool — pure reuse.
- Output: verdict-first → side-by-side matrix (15 rows) → moat → catalysts/risks → valuation → technical setup → Canadian tax-aware placement recommendation
- Gotchas: cache-staleness symmetry, mismatched fiscal years, asymmetric analyst-target % upside, US/.TO cross-border coverage gap, US-div withholding in Non-Reg, beta benchmark mismatch (S&P vs TSX)

### Backend
- `backend/mcp_server.py` — `list_analysis_modes` now reports 10 skills; added `stock_compare` entry

### Triggers (skill description)
"X vs Y", "which is better A or B", "should I pick X or Y", side-by-side matchup requests

---

## 2026-05-14 — Phase 6: Performance pass (no behavior change)

### Backend
- `app/api/ws.py`
  - `_PORTFOLIO_CACHE` key now `(session_id, account_id)` — per-tab caching, account-switch hits cache
  - `asyncio.Lock` per cache key with double-checked locking — concurrent dashboard fetches dedupe to one WS+yfinance round-trip
  - `/portfolio` endpoint now routed through `_get_portfolio` (was bypassing cache)
- `app/services/market_data.py`
  - New `_TICKER_CACHE` (5-min TTL) for `yf.Ticker.info` + `fast_info`. Old code: 1 HTTP per holding per `enrich()` call. New: 1 per holding per 5 min. Measured: 2.0s → 0.0s on cached path.
- `app/services/technicals.py`
  - `get_technicals` now batches all uncached symbols into one `yf.download(group_by="ticker")` call. Old: serial loop, 1 HTTP per symbol. Measured: 5 syms in 0.5s vs ~1.4s sequential.
- `app/services/fundamentals.py`
  - `get_fundamentals` now uses `ThreadPoolExecutor(max_workers=8)` for uncached symbols. Each `_fetch_one` makes 3 HTTP calls (info, calendar, dividends) which now overlap. Measured: 5 syms in 1.2s.

### Frontend
- New `components/CountdownLabel.tsx` — isolates 5-second re-render tick from dashboard tree (was re-rendering all charts/tables every 5s)
- `React.memo` added to: `AllocationChart`, `HealthScoreWidget`, `MacroWidget`, `BenchmarkWidget`, `OptimizerWidget`, `AlertsPanel`, `RecommendationsPanel` (PortfolioTable already memoized)
- `lib/api.ts` — all `wsGet*` helpers now accept optional `signal?: AbortSignal`
- `app/dashboard/page.tsx`
  - Per-loader `AbortController` ref — new fetch cancels prior in-flight one (fixes account-tab race + stale-state stomp)
  - Stale-while-revalidate: skeleton only shown on initial load (no data yet); background refresh keeps stale data visible
  - Cleanup effect aborts all in-flight fetches on unmount

---

## 2026-05-14 — Phase 5: Multi-Provider LLM Narrative Layer

### Goal
AI-generated narrative sentences on each recommendation card — no Anthropic key.
Router auto-selects best available free provider at runtime with fallback.

### New service
- `backend/app/services/llm_router.py`
  - 4 providers tried in priority order: GitHub Models → Gemini → OpenRouter → Qwen
  - All use free tiers (GitHub Pro qualifies for GitHub Models)
  - Per-provider error tracking: 2 consecutive failures → 5-min cooldown → retry
  - 30-min narrative cache keyed by (symbol, score, market_regime)
  - `generate_narratives_batch()`: concurrent generation with semaphore (4 max)
  - Skips HOLDs first, fills with HOLDs if under 15-position limit
  - Graceful: returns `None` per symbol when all providers fail

### Updated `backend/app/core/config.py`
- Added: `github_token`, `google_api_key`, `openrouter_api_key`, `dashscope_api_key`
- All optional — system works rule-based-only if none set

### New endpoints in `ws.py`
- `GET /ws/ai-narratives` — returns `{narratives: {symbol: text}, providers: [...]}`
- `GET /ws/llm-status` — lists currently available providers

### Updated frontend
- `api.ts`: `NarrativesResponse` type, `wsGetNarratives()`, `wsGetLlmStatus()`
- `RecommendationsPanel.tsx`: shows AI narrative per card (italic, indigo left-border)
  - Pulse skeleton while loading, gracefully absent if no providers
  - Provider badge shows which LLM generated it (e.g. "AI via github")
- `dashboard/page.tsx`:
  - Narratives load 3s after page render (rule-based recs appear first)
  - Refresh also re-fetches narratives with same stagger

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
Always-on recommendations (BUY/SELL/HOLD/WATCH) without manual Claude commands.
Rule-based engine using all existing data — no Anthropic API key required.

### New backend service
- `backend/app/services/recommendations.py` — scoring engine (0-10 score per position)
  - Technical: Minervini stage, RSI, MACD histogram, SMA200 trend, 52w range
  - Fundamental: analyst rec/target, EPS growth, short interest, revenue growth
  - Macro: market regime (bull/bear × fear), VIX level, Fear & Greed index
  - Position: weight concentration, total return
  - Thresholds: ≥7.5=BUY, ≥5.5=HOLD, ≥3.5=WATCH, <3.5=SELL
  - ETFs skip fundamental signals (no analyst targets for index ETFs)

### Updated services
- `macro.py` — added `fear_and_greed()` (CNN Fear & Greed Index, free HTTP, 1h cache)
  - Merged into `market_breadth()` so all consumers get it automatically
- `market_data.py` — added `day_change_cad` to `PortfolioSummary` (weighted sum of daily moves)
- `portfolio.py` (models) — `day_change_cad: float = 0.0` field on `PortfolioSummary`

### New REST endpoints (`backend/app/api/ws.py`)
- `GET /ws/recommendations` — scored list sorted SELL→BUY→WATCH→HOLD
- `GET /ws/macro` — combined market breadth + FRED snapshot in one call
- Also rewrote ws.py to fix all pre-existing lint (E501 lines, F841 unused var)

### New frontend components
- `frontend/components/RecommendationsPanel.tsx`
  - Groups by action with color-coded cards (green/red/amber/gray)
  - Score bar, analyst upside %, Minervini stage badge, RSI badge, top 3 reasons
- `frontend/components/MacroWidget.tsx`
  - Market regime badge + signal text
  - VIX, SPY vs SMA200, Fear & Greed, FRED rates (Fed Funds, 10Y, BoC, CAD/USD)

### Redesigned dashboard (`frontend/app/dashboard/page.tsx`)
Layout:
  1. Summary cards: Portfolio Value, Day Change (CAD), Total Return, Book Cost, Cash
  2. Health score + Macro widget + Allocation chart (3-col)
  3. Recommendations panel (full width, auto-loads on page open)
  4. Alerts panel
  5. Holdings table
  6. Price chart
  7. Skills panel (collapsible, default collapsed)

### Updated `frontend/lib/api.ts`
- `Recommendation` interface
- `MacroSnapshot` interface  
- `PortfolioSummary.day_change_cad` field
- `wsGetRecommendations()` + `wsGetMacro()` fetch functions

---

## 2026-05-14 — Phase 3: Market Breadth + Minervini Stage Analysis

### New MCP tool
- `get_market_breadth()` — VIX (fear gauge), SPY vs SMA200 (bull/bear regime), composite market_regime label + regime_signal. Cached 1h. No API key.

### Updated services
- `macro.py` — added `market_breadth()` function. Uses yfinance `^VIX` + SPY 1y daily OHLCV.
- `technicals.py` — added Minervini trend template: `stage` (1=basing/2=uptrend/3=distribution/4=decline), `minervini_score` (0-7 criteria met), `sma_150`, `sma_200_slope_pct`, `week52_high`, `week52_low`, `pct_from_52w_high`, `pct_from_52w_low`.

### New REST endpoint
- `GET /ws/market-breadth` — delegates to `macro.market_breadth()`

### Updated skills
- `macro-impact` — step 4 now calls `get_market_breadth`; step 7 uses `market_regime` for risk stance
- `stock-analysis` — technical section now includes Minervini stage/score + 52w context interpretation
- `sector-rotation` — step 4 calls `get_market_breadth`; rotation conviction calibrated to regime

### Source: evaluated claudemarketplaces.com skills
- Marketplace skills (gracefullight/stock-checker, sundial-org, tradermonty) were reviewed
- All were redundant with existing yfinance/ta-lib stack
- Only unique value: market breadth + Minervini (implemented above with free data)

---

## 2026-05-14 — Phase 2: Real-time Dashboard + Multi-agent Auto-analysis

### New backend services
- `backend/app/services/health_score.py` — rule-based portfolio health score (0-100, grade A-F). No external calls — computed from portfolio data (diversification, concentration, return, cash drag, asset class diversity).
- `backend/app/services/crypto_data.py` — CoinGecko free API v3, no key. Live CAD prices, 24h/7d/30d change, ATH drawdown, market cap rank, 20 crypto symbols. 5-min cache.

### New REST endpoints (`backend/app/api/ws.py`)
- `GET /ws/health-score` — health score + grade + breakdown
- `GET /ws/alerts` — concentration warnings + upcoming earnings alerts (priority sorted)
- `GET /ws/crypto` — CoinGecko data for crypto holdings

### New MCP tools (`backend/mcp_server.py`)
- `get_crypto_data(account_id, symbols)` — CoinGecko crypto data. symbols=[] auto-detects from portfolio

### New frontend components
- `frontend/components/HealthScoreWidget.tsx` — grade badge (A-F) + 5-dimension breakdown
- `frontend/components/AlertsPanel.tsx` — dismissable alert cards (high/warning/info)

### Updated dashboard (`frontend/app/dashboard/page.tsx`)
- Health score widget in summary grid
- Alerts panel: auto-loads concentration + earnings alerts
- Auto-refresh every 5 min, countdown in header
- Skill panel: click to copy command
- `wsGetHealthScore()` + `wsGetAlerts()` load in parallel with portfolio

### Updated `frontend/lib/api.ts`
- `wsGetHealthScore()`, `wsGetAlerts()` fetch functions
- `HealthScore`, `Alert` TypeScript interfaces

### Updated all 9 skills
- All 9 skills call `mcp__aifolimizer__get_profile` as step 1
- Removed "(fixed context)" from investor profile sections
- Added rule: account types + capital always from `get_profile`, never hardcoded

### Updated `CLAUDE.md`
- 14 MCP tools (was 9), 9 skills (was 7), `ta>=0.11.0` (not pandas-ta)
- Added how-to-start, tech stack table, file index
- Session startup instructions for new sessions

### New tooling
- `backend/scripts/build_skills.py` — lists MCP tools + skill health, scaffolds new SKILL.md
  - Run: `python backend/scripts/build_skills.py`
  - Scaffold: `python backend/scripts/build_skills.py --scaffold <tool_name>`

---

## 2026-05-14 — Phase 1 Enhancement: Data Foundation

### Added
- `backend/app/services/fundamentals.py` — yfinance.info: P/E, EPS, div yield, payout, market cap, earnings date, analyst targets, ownership, beta, short interest. 6h cache.
- `backend/app/services/technicals.py` — `ta` lib: SMA20/50/200, RSI(14), MACD, Bollinger Bands, volume SMA, trend signal. 1h cache.
- `backend/app/services/news.py` — yfinance news fetcher, 5 articles/ticker, 30-min cache.
- `backend/mcp_server.py` — 4 new MCP tools: `get_fundamentals`, `get_technicals`, `get_earnings_calendar`, `get_news_headlines`
- `backend/app/api/ws.py` — 4 new REST endpoints: `/ws/fundamentals`, `/ws/technicals`, `/ws/earnings-calendar`, `/ws/price-history`
- `backend/requirements.txt` — added `ta>=0.11.0` (NOT pandas-ta — incompatible with Python 3.14)
- `.claude/skills/` — 8 skills moved from `~/.claude/skills/` to project-level
- `.claude/agents/` — analyst.md + researcher.md moved to project-level
- `.claude/context/` — created (changes.md + architecture.md)

### Updated skills (Phase 2)
- `stock-analysis` — calls `get_fundamentals` + `get_technicals` + `get_news_headlines` before WebSearch
- `earnings-analyzer` — calls `get_earnings_calendar` + `get_fundamentals` first
- `dividend-strategy` — calls `get_fundamentals` for dividend yield/payout data

### New skills (Phase 3)
- `adversarial-research` — parallel bull/bear sub-agent pipeline, probability-weighted synthesis

---

## 2026-05-XX — MVP Build (Initial)

### Built
- Wealthsimple GraphQL client (`wealthsimple.py`) — MFA-aware login, 8h token TTL in RAM
- PII filter (`pii_filter.py`) — strips account IDs, names, emails before MCP response
- MCP server (`mcp_server.py`) — 9 tools: get_profile, get_portfolio, get_xray, get_concentration_warnings, get_tax_loss_candidates, get_risk_metrics, get_correlation_matrix, get_macro_snapshot, list_analysis_modes
- FastAPI REST API (`main.py` + `app/api/ws.py`) — login, OTP, portfolio, profile endpoints
- yfinance enrichment (`market_data.py`) — live prices, sectors, day change
- FRED macro data (`macro.py`) — Fed funds, 10Y, CPI, CAD/USD, BoC rate, 12h cache
- Quant analytics (`quant.py`) — Sharpe, Sortino, VaR 95%, correlation matrix, pure Python
- Portfolio analytics (`portfolio_analytics.py`) — ETF X-ray, concentration warnings, tax-loss candidates
- 8 institutional analysis skills at `~/.claude/skills/` — BlackRock, Bridgewater, Goldman+Citadel, McKinsey, Harvard, JPMorgan, Renaissance, Canadian tax-loss
- Next.js 14 dashboard — login (MFA), portfolio table, allocation chart, skill directory
