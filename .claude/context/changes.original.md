# aifolimizer — Change Log

Append-only. Most recent at top.

---

## 2026-05-14 — Phase 2: Real-time Dashboard + Multi-agent Auto-analysis

### New backend services
- `backend/app/services/health_score.py` — rule-based portfolio health score (0-100, grade A-F). Fast: no external calls, computed purely from portfolio data (diversification, concentration, return, cash drag, asset class diversity).
- `backend/app/services/crypto_data.py` — CoinGecko free API v3 wrapper (no key required). Provides live CAD prices, 24h/7d/30d change, ATH drawdown, market cap rank for 20 crypto symbols. 5-minute cache.

### New REST endpoints (`backend/app/api/ws.py`)
- `GET /ws/health-score` — returns health score + grade + breakdown for the dashboard
- `GET /ws/alerts` — returns concentration warnings + upcoming earnings alerts (priority sorted)
- `GET /ws/crypto` — returns CoinGecko data for crypto holdings

### New MCP tools (`backend/mcp_server.py`)
- `get_crypto_data(account_id, symbols)` — CoinGecko crypto data. If symbols=[], auto-detects crypto from portfolio

### New frontend components
- `frontend/components/HealthScoreWidget.tsx` — grade badge (A-F) + 5-dimension breakdown bar
- `frontend/components/AlertsPanel.tsx` — dismissable alert cards (high/warning/info severity)

### Updated dashboard (`frontend/app/dashboard/page.tsx`)
- Health score widget in summary grid
- Alerts panel auto-loads and shows concentration + earnings alerts
- Auto-refresh every 5 minutes with countdown timer in header
- Skill command panel: clicking a skill copies the command to clipboard
- Both `wsGetHealthScore()` and `wsGetAlerts()` load in parallel with portfolio on page load

### Updated `frontend/lib/api.ts`
- `wsGetHealthScore()`, `wsGetAlerts()` fetch functions
- `HealthScore`, `Alert` TypeScript interfaces

### Updated all 9 skills
- All 9 skills now call `mcp__aifolimizer__get_profile` as first step
- Removed "(fixed context)" from investor profile sections
- Added explicit rule: "Account types and capital: always read from `get_profile` — never hardcode"

### Updated `CLAUDE.md`
- 14 MCP tools (was 9), 9 skills (was 7), `ta>=0.11.0` (not pandas-ta)
- Added how-to-start instructions, full tech stack table, updated file index
- "How to Start" run order for new sessions

### New tooling
- `backend/scripts/build_skills.py` — auto skills builder: lists all MCP tools + skill health check, scaffolds new SKILL.md from any tool name
  - Run: `python backend/scripts/build_skills.py`
  - Scaffold: `python backend/scripts/build_skills.py --scaffold <tool_name>`

---

## 2026-05-14 — Phase 1 Enhancement: Data Foundation

### Added
- `backend/app/services/fundamentals.py` — yfinance.info wrapper: P/E, EPS, dividend yield, payout ratio, market cap, earnings date, analyst targets, insider/institutional ownership, beta, short interest. 6-hour cache.
- `backend/app/services/technicals.py` — pandas-ta: SMA20/50/200, RSI(14), MACD, Bollinger Bands, volume SMA, trend signal. 1-hour cache.
- `backend/app/services/news.py` — yfinance news fetcher, 5 articles per ticker, 30-min cache.
- `backend/mcp_server.py` — 4 new MCP tools: `get_fundamentals`, `get_technicals`, `get_earnings_calendar`, `get_news_headlines`
- `backend/app/api/ws.py` — 4 new REST endpoints: `/ws/fundamentals`, `/ws/technicals`, `/ws/earnings-calendar`, `/ws/price-history`
- `backend/requirements.txt` — added `pandas-ta>=0.3.14b0`
- `.claude/skills/` — all 8 skills moved from user-level `~/.claude/skills/` to project-level for version control
- `.claude/agents/` — analyst.md + researcher.md moved to project-level
- `.claude/context/` — this directory created (changes.md + architecture.md)

### Updated skills (Phase 2)
- `stock-analysis` — now calls `get_fundamentals` + `get_technicals` + `get_news_headlines` before WebSearch
- `earnings-analyzer` — now calls `get_earnings_calendar` + `get_fundamentals` first
- `dividend-strategy` — now calls `get_fundamentals` for dividend yield/payout data

### New skills (Phase 3)
- `adversarial-research` — parallel bull/bear sub-agent pipeline, probability-weighted synthesis

---

## 2026-05-XX — MVP Build (Initial)

### Built
- Wealthsimple GraphQL client (`wealthsimple.py`) — MFA-aware login, 8h token TTL in RAM
- PII filter (`pii_filter.py`) — strips account IDs, names, emails before every MCP response
- MCP server (`mcp_server.py`) — 9 tools: get_profile, get_portfolio, get_xray, get_concentration_warnings, get_tax_loss_candidates, get_risk_metrics, get_correlation_matrix, get_macro_snapshot, list_analysis_modes
- FastAPI REST API (`main.py` + `app/api/ws.py`) — login, OTP, portfolio, profile endpoints
- yfinance enrichment (`market_data.py`) — live prices, sectors, day change
- FRED macro data (`macro.py`) — Fed funds, 10Y, CPI, CAD/USD, BoC rate, 12h cache
- Quant analytics (`quant.py`) — Sharpe, Sortino, VaR 95%, correlation matrix, pure Python
- Portfolio analytics (`portfolio_analytics.py`) — ETF X-ray, concentration warnings, tax-loss candidates
- 8 institutional analysis skills at `~/.claude/skills/` — BlackRock, Bridgewater, Goldman+Citadel, McKinsey, Harvard, JPMorgan, Renaissance, Canadian tax-loss
- Next.js 14 dashboard — login (MFA), portfolio table, allocation pie chart, skill directory
