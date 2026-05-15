# aifolimizer — Change Log

Append-only. Most recent at top.

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
