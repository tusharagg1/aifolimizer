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
    ├─ get_fundamentals()         → fundamentals.py (yfinance.info, 6h cache) → pii_filter.py  [NEW]
    ├─ get_technicals()           → technicals.py (pandas-ta, 1h cache) → pii_filter.py       [NEW]
    ├─ get_earnings_calendar()    → fundamentals.py earnings dates → pii_filter.py             [NEW]
    ├─ get_news_headlines()       → news.py (yfinance news, 30m cache) → pii_filter.py         [NEW]
    └─ list_analysis_modes()      → static list

FastAPI REST API  (backend/main.py + app/api/ws.py — port 8000)
    │   ← used by Next.js dashboard only (not by Claude)
    ├─ POST /ws/login             → wealthsimple.py (returns session_id + needs_otp flag)
    ├─ POST /ws/verify-otp        → wealthsimple.py (completes MFA, stores token in RAM)
    ├─ GET  /ws/portfolio         → same pipeline as MCP get_portfolio
    ├─ GET  /ws/profile           → same pipeline as MCP get_profile
    ├─ GET  /ws/fundamentals      → fundamentals.py                                            [NEW]
    ├─ GET  /ws/technicals        → technicals.py                                              [NEW]
    ├─ GET  /ws/earnings-calendar → fundamentals.py earnings dates                            [NEW]
    └─ GET  /ws/price-history     → market_data.py yfinance OHLCV                             [NEW]

Next.js 14 Frontend  (frontend/ — port 3000)
    ├─ /login        → WS email + password + MFA form → POST /ws/login, /ws/verify-otp
    └─ /dashboard    → GET /ws/portfolio → portfolio table + allocation chart + [price chart NEW]
```

## External Data Sources

| Source | Auth | Endpoints used | Cache TTL | Used by |
|--------|------|---------------|-----------|---------|
| Wealthsimple | email/password + MFA → token in RAM | GraphQL FetchIdentityPositions | 8h session | wealthsimple.py |
| yfinance (Yahoo Finance) | None | `.history()`, `.info`, `.news`, `.calendar` | varies | market_data.py, fundamentals.py, technicals.py, news.py |
| FRED (Federal Reserve) | None | Public CSV series | 12h | macro.py |
| pandas-ta | N/A | Local computation on yfinance OHLCV | 1h | technicals.py |

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
- Input: raw WS positions list + cash float
- Calls yfinance per symbol (batched)
- Returns enriched positions + summary stats

### fundamentals.get_fundamentals(symbols: list[str]) → dict[str, dict]
- Input: list of ticker symbols (e.g. ["AAPL", "XEQT.TO"])
- Returns: {symbol: {pe_ratio, eps_ttm, dividend_yield, ...}}
- Missing fields → None (yfinance.info is inconsistent)
- Cache: 6 hours per symbol

### technicals.get_technicals(symbols: list[str]) → dict[str, dict]
- Input: list of symbols
- Downloads 1y daily OHLCV via yfinance
- Computes via pandas-ta: SMA, RSI, MACD, Bollinger Bands
- Cache: 1 hour per symbol

### quant.portfolio_risk_metrics(returns, weights) → RiskMetrics
- Input: pd.DataFrame of daily returns + weight vector
- Returns: vol, sharpe, sortino, var_95, expected_shortfall, max_drawdown

## MCP Tool Name Mapping

Claude Code calls tools as `mcp__aifolimizer__<tool_name>` where `<tool_name>` matches the function name in `mcp_server.py`.

Example: `mcp__aifolimizer__get_portfolio` → `backend/mcp_server.py::get_portfolio()`

## Environment Variables

```
# backend/.env (LOCAL ONLY — never committed, never deployed to cloud)
WS_EMAIL=...
WS_PASSWORD=...
SUPABASE_URL=...        # optional
SUPABASE_SERVICE_KEY=...  # optional

# frontend/.env.local (LOCAL ONLY)
NEXT_PUBLIC_API_URL=http://localhost:8000
```

## File Index

| File | Purpose |
|------|---------|
| `backend/mcp_server.py` | All MCP tools (13 total after Phase 1) |
| `backend/main.py` | FastAPI app entry point + CORS |
| `backend/run.py` | uvicorn launcher |
| `backend/app/api/ws.py` | REST endpoints |
| `backend/app/services/wealthsimple.py` | WS GraphQL client + auth |
| `backend/app/services/market_data.py` | yfinance price enrichment |
| `backend/app/services/fundamentals.py` | yfinance.info fundamentals [NEW] |
| `backend/app/services/technicals.py` | pandas-ta indicators [NEW] |
| `backend/app/services/news.py` | yfinance news [NEW] |
| `backend/app/services/portfolio_analytics.py` | ETF x-ray, tax-loss, concentration |
| `backend/app/services/quant.py` | Risk metrics (pure Python) |
| `backend/app/services/macro.py` | FRED macro data |
| `backend/app/services/pii_filter.py` | PII stripping |
| `backend/app/models/portfolio.py` | Pydantic data models |
| `backend/app/core/config.py` | Env var loading |
| `frontend/app/dashboard/page.tsx` | Main dashboard |
| `frontend/app/login/page.tsx` | MFA login form |
| `frontend/components/PortfolioTable.tsx` | Holdings table |
| `frontend/components/AllocationChart.tsx` | Pie chart |
| `frontend/components/PriceChart.tsx` | OHLCV + SMA chart [NEW] |
| `frontend/lib/api.ts` | Typed fetch helpers |
| `.claude/skills/*/SKILL.md` | 9 institutional analysis skills |
| `.claude/agents/analyst.md` | Deep analysis subagent |
| `.claude/agents/researcher.md` | Market data fetch subagent |
| `.claude/context/changes.md` | This change log |
| `.claude/context/architecture.md` | This file |
| `supabase_schema.sql` | Optional snapshot history schema |
