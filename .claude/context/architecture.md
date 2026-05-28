# aifolimizer — Architecture Reference

## Data Flow

```
User (Claude Code / Claude Desktop — Pro subscription)
    │
    ├─ invokes skill  →  .claude/skills/<skill>/SKILL.md
    │                    └─ instructions tell Claude which MCP tools to call
    │
    ▼
MCP Server  (backend/mcp_server.py — FastMCP, stdio transport)
    │
    ├─ get_profile()              → wealthsimple.py → pii_filter.py
    ├─ get_portfolio()            → wealthsimple.py → market_data.py → pii_filter.py
    ├─ get_xray()                 → wealthsimple.py → portfolio_analytics.py → pii_filter.py
    ├─ get_concentration_warnings()→ wealthsimple.py → portfolio_analytics.py → pii_filter.py
    ├─ get_tax_loss_candidates()  → wealthsimple.py → portfolio_analytics.py → pii_filter.py
    ├─ get_risk_metrics()         → wealthsimple.py → market_data.py → quant.py → pii_filter.py
    ├─ get_correlation_matrix()   → wealthsimple.py → market_data.py → quant.py → pii_filter.py
    ├─ get_macro_snapshot()       → macro.py (FRED CSV, 12h cache) → pii_filter.py
    ├─ get_market_breadth()       → macro.py (yfinance VIX+SPY, 1h cache) → pii_filter.py
    ├─ get_fundamentals()         → fundamentals.py (yfinance.info, 6h cache) → pii_filter.py
    ├─ get_technicals()           → technicals.py (ta lib + Minervini, 1h cache) → pii_filter.py
    ├─ get_earnings_calendar()    → fundamentals.py earnings dates → pii_filter.py
    ├─ get_news_headlines()       → news.py (yfinance news, 30m cache) → pii_filter.py
    ├─ get_crypto_data()          → crypto_data.py (CoinGecko, 5m cache) → pii_filter.py
    ├─ get_triggered_alerts()     → alerts.py read_recent_history (JSONL log)
    ├─ run_alerts_now()           → alerts.py evaluate + dispatch (live WS + yfinance)
    ├─ backtest_portfolio()       → backtest.py run buy_hold / rsi_swing / sma_cross
    └─ list_analysis_modes()      → static list (18 tools)

FastAPI REST API  (backend/main.py + app/api/ws.py — port 8000)
    │   ← used by Next.js dashboard only (not by Claude)
    ├─ POST /ws/login             → wealthsimple.py
    ├─ POST /ws/verify-otp        → wealthsimple.py
    ├─ GET  /ws/portfolio         → same pipeline as MCP get_portfolio
    ├─ GET  /ws/profile           → same pipeline as MCP get_profile
    ├─ GET  /ws/fundamentals      → fundamentals.py
    ├─ GET  /ws/technicals        → technicals.py
    ├─ GET  /ws/earnings-calendar → fundamentals.py earnings dates
    ├─ GET  /ws/price-history     → market_data.py yfinance OHLCV
    ├─ GET  /ws/health-score      → health_score.py (rule-based, no external calls)
    ├─ GET  /ws/alerts            → portfolio_analytics + fundamentals
    ├─ GET  /ws/market-breadth    → macro.py market_breadth() (VIX+SPY, 1h cache)
    └─ GET  /ws/crypto            → crypto_data.py (CoinGecko)

Next.js 16 Frontend  (frontend/ — port 3000)
    ├─ /login      → WS email + password + MFA → POST /ws/login, /ws/verify-otp
    └─ /dashboard  → GET /ws/portfolio + /ws/health-score + /ws/alerts in parallel
                     → portfolio table + allocation chart + price chart + health widget + alerts
```

## External Data Sources

| Source | Auth | Cache TTL | Used by |
|--------|------|-----------|---------|
| Wealthsimple | email/password + MFA → token in RAM | 8h session | wealthsimple.py |
| yfinance (Yahoo Finance) | None | varies | market_data.py, fundamentals.py, technicals.py, news.py |
| FRED (Federal Reserve) | None | 12h | macro.py |
| `ta>=0.11.0` | N/A (local) | 1h | technicals.py |
| CoinGecko v3 | None (free tier) | 5m | crypto_data.py |

## Session / Auth Model

```
Login flow:
  1. User sends email+password via /ws/login
  2. WS returns 401 with OTP required → backend raises OTPRequiredException
  3. User sends OTP via /ws/verify-otp
  4. Backend stores token: sessions[session_id] = {token, expires_at}
  5. All subsequent calls pass session_id → backend looks up token → calls WS

Token lifecycle:
  - Stored in Python dict (server RAM only, never disk/DB)
  - TTL: 8 hours from login
  - Evicted on 401 from WS or manual logout
  - MCP server shares same session store as FastAPI
```

## PII Filter Contract

Every MCP tool response passes through `pii_filter.filter_portfolio()` before returning to Claude.

**Stripped:** account_id, account_number, email, full name, WS internal IDs, phone
**Kept:** symbol, name (company), quantity, book_cost, market_value, weight, day_change_pct, total_return_pct, asset_class, sector, cash_balance (aggregate only), account_type label (TFSA/RRSP — NOT the ID)

## Key Service Contracts

### market_data.enrich(raw_positions, cash_balance) → PortfolioResponse
- Input: raw WS positions + cash float
- Calls yfinance per symbol
- Returns enriched positions + summary

### fundamentals.get_fundamentals(symbols: list[str]) → dict[str, dict]
- Input: ticker list (e.g. ["AAPL", "XEQT.TO"])
- Returns: {symbol: {pe_ratio, eps_ttm, dividend_yield, ...}}
- Missing fields → None (yfinance.info inconsistent)
- Cache: 6h/symbol

### technicals.get_technicals(symbols: list[str]) → dict[str, dict]
- Input: symbols list
- Downloads 1y daily OHLCV
- Computes via `ta` lib: SMA20/50/150/200, RSI, MACD, Bollinger Bands
- Minervini stage analysis: stage (1-4), minervini_score (0-7), pct_from_52w_high/low
- Cache: 1h/symbol

### macro.market_breadth() → dict
- Returns: vix, vix_signal, vix_regime, spy_price, spy_sma200, spy_vs_sma200_pct, spy_regime, market_regime, regime_signal
- market_regime values: bull_low_fear | bull_high_fear | bear_high_fear | bear_low_fear
- Cache: 1h

### quant.portfolio_risk_metrics(returns, weights) → RiskMetrics
- Input: pd.DataFrame of daily returns + weight vector
- Returns: vol, sharpe, sortino, var_95, expected_shortfall, max_drawdown

### health_score.compute_health_score(portfolio) → dict
- Input: PortfolioResponse (no external calls)
- Returns: score (0-100), grade (A-F), verdict, breakdown (5 dimensions)
- Dimensions: diversification, concentration, performance, cash_efficiency, asset_class_diversity

## MCP Tool Name Mapping

Tools called as `mcp__aifolimizer__<tool_name>` matching function name in `mcp_server.py`.

Example: `mcp__aifolimizer__get_portfolio` → `backend/mcp_server.py::get_portfolio()`

## Environment Variables

```
# backend/.env (LOCAL ONLY — never committed, never deployed to cloud)
WS_EMAIL=...
WS_PASSWORD=...
SUPABASE_URL=...        # optional
SUPABASE_SERVICE_KEY=...  # optional
NTFY_TOPIC=...          # optional — ntfy.sh topic for alerts push (random string, treat as private)

# frontend/.env.local (LOCAL ONLY)
NEXT_PUBLIC_API_URL=http://localhost:8000
```

## File Index

| File | Purpose |
|------|---------|
| `backend/mcp_server.py` | All MCP tools (32 total) |
| `backend/main.py` | FastAPI app entry point + CORS |
| `backend/run.py` | uvicorn launcher |
| `backend/scripts/build_skills.py` | Auto skills builder / scaffold tool |
| `backend/app/api/ws.py` | REST endpoints |
| `backend/app/services/wealthsimple.py` | WS GraphQL client + auth |
| `backend/app/services/market_data.py` | yfinance price enrichment |
| `backend/app/services/fundamentals.py` | yfinance.info fundamentals |
| `backend/app/services/technicals.py` | `ta` lib indicators |
| `backend/app/services/news.py` | yfinance news |
| `backend/app/services/health_score.py` | Rule-based portfolio health score |
| `backend/app/services/crypto_data.py` | CoinGecko crypto data |
| `backend/app/services/portfolio_analytics.py` | ETF x-ray, tax-loss, concentration |
| `backend/app/services/quant.py` | Risk metrics (pure Python) |
| `backend/app/services/macro.py` | FRED macro data |
| `backend/app/services/pii_filter.py` | PII stripping |
| `backend/app/services/alerts.py` | Rule eval + ntfy.sh dispatch + JSONL history |
| `backend/app/services/positioning.py` | Crowding signals (inst%, short%, analyst, news) |
| `backend/app/services/backtest.py` | Per-position backtest + walk-forward |
| `backend/app/services/data_router.py` | Multi-source fallback chain + batch quotes |
| `backend/app/services/data_cache.py` | SQLite disk cache (quotes/history/fundamentals) |
| `backend/app/services/paper_trade.py` | Forward rec logging + mark-to-market scoring |
| `backend/app/services/alpha_attribution.py` | Alpha/beta vs SPY/XEQT/TSX/QQQ |
| `backend/app/services/skill_backtest.py` | Backtest all 13 skills as codified rules |
| `backend/app/services/trust_report.py` | Generate TRACK_RECORD.md + JSONL |
| `backend/app/services/recommendations.py` | Rule-based BUY/SELL/HOLD/WATCH scoring |
| `backend/app/services/llm_router.py` | 4-provider LLM fallback (GitHub→Gemini→OpenRouter→Qwen) |
| `backend/scripts/run_alerts.py` | CLI: evaluate alerts, push to ntfy (cron-friendly) |
| `backend/scripts/schedule_alerts.ps1` | Register Windows Task Scheduler job |
| `backend/app/models/portfolio.py` | Pydantic data models |
| `backend/app/core/config.py` | Env var loading |
| `frontend/app/dashboard/page.tsx` | Main dashboard |
| `frontend/app/login/page.tsx` | MFA login form |
| `frontend/components/PortfolioTable.tsx` | Holdings table + crowding badges |
| `frontend/components/AllocationChart.tsx` | Pie chart |
| `frontend/components/PriceChart.tsx` | OHLCV + SMA50 chart |
| `frontend/components/HealthScoreWidget.tsx` | Health grade badge + breakdown |
| `frontend/components/AlertsPanel.tsx` | Dismissable alert cards |
| `frontend/components/RecommendationsPanel.tsx` | BUY/SELL/HOLD cards + AI narratives |
| `frontend/components/MacroWidget.tsx` | Regime badge + FRED rates |
| `frontend/lib/api.ts` | Typed fetch helpers |
| `.claude/skills/*/SKILL.md` | 13 skills |
| `.claude/context/changes.md` | Change log |
| `.claude/context/architecture.md` | This file |
| `supabase_schema.sql` | Optional snapshot history schema |
