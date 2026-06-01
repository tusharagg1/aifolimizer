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
    ├─ get_positioning_signals()   → positioning.py (crowding, inst%, short%, 6h)
    ├─ snapshot_positioning_history()→ positioning.py → JSONL append (idempotent/day)
    ├─ get_crowding_shifts()       → positioning.py reads history JSONL
    ├─ get_quote_with_source()     → data_router.py fallback chain (yfinance→finnhub→tiingo→stooq)
    ├─ get_quotes_batch()          → data_router.py batch (13x faster than serial)
    ├─ get_data_source_reliability()→ data_router.py success/latency stats
    ├─ log_recommendation()        → paper_trade.py JSONL append
    ├─ score_recommendations()     → paper_trade.py mark-to-market
    ├─ get_live_track_record()     → paper_trade.py rolling win-rate + P&L
    ├─ snapshot_portfolio_equity() → paper_trade.py NAV history (idempotent/day)
    ├─ get_alpha_attribution()     → alpha_attribution.py vs SPY/XEQT/TSX/QQQ
    ├─ get_skill_track_record()    → skill_backtest.py 3-5yr rule replay
    ├─ generate_trust_report()     → trust_report.py → TRACK_RECORD.md + JSONL
    └─ list_analysis_modes()       → filesystem-driven list of all 21 skills

FastAPI REST API  (backend/main.py — port 8000)
    ├─ app/api/ws.py              → portfolio, profile, fundamentals, technicals, alerts, crypto
    ├─ app/api/agents.py          → agent execution endpoints
    └─ app/api/ops.py             → ops / health / metrics endpoints

Postgres (TimescaleDB)  +  Redis  (docker-compose.yml — local Docker)
    ├─ app/db/pool.py             → asyncpg connection pool
    ├─ app/db/repositories/       → alerts, changes, crowding, equity, recommendations, signals, snapshots, weights
    ├─ app/cache/redis_client.py  → L2 cross-process cache (shared by MCP + FastAPI)
    └─ app/jobs/                  → scheduler.py + tasks.py + queues.py (RQ worker)
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
  - Stored in Python dict (server RAM) AND persisted to ~/.aifolimizer/ws_session.json
    (mode 0600, outside repo) so a backend restart resumes without re-OTP. Password
    never persisted — only access+refresh token + email + timestamp.
  - TTL: 8 hours from login; persisted file auto-cleared when stale or rejected by WS
  - Evicted on 401 from WS or manual logout
  - MCP server shares same session store as FastAPI
```

## PII Filter Contract

Every MCP tool response passes `pii_filter.filter_portfolio()` before return to Claude.

**Stripped:** account_id, account_number, email, full name, WS internal IDs, phone
**Kept:** symbol, name (company), quantity, book_cost, market_value, weight, day_change_pct, total_return_pct, asset_class, sector, cash_balance (aggregate only), account_type label (TFSA/RRSP — NOT ID)

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
- Input: pd.DataFrame daily returns + weight vector
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
TELEGRAM_BOT_TOKEN=...  # optional — Telegram bot token for alert push (BotFather)
TELEGRAM_CHAT_ID=...    # optional — Telegram chat/channel ID receiving alerts
```

No frontend — analysis runs in Claude Code / Claude Desktop.

## File Index

| File | Purpose |
|------|---------|
| `backend/mcp_server.py` | All MCP tools (80 total) |
| `backend/main.py` | FastAPI app entry + CORS |
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
| `backend/app/services/alerts.py` | Rule eval + Telegram dispatch + JSONL history |
| `backend/app/services/positioning.py` | Crowding signals (inst%, short%, analyst, news) |
| `backend/app/services/backtest.py` | Per-position backtest + walk-forward |
| `backend/app/services/data_router.py` | Multi-source fallback chain + batch quotes |
| `backend/app/services/data_cache.py` | SQLite disk cache (quotes/history/fundamentals) |
| `backend/app/services/paper_trade.py` | Forward rec logging + mark-to-market scoring |
| `backend/app/services/alpha_attribution.py` | Alpha/beta vs SPY/XEQT/TSX/QQQ |
| `backend/app/services/skill_backtest.py` | Backtest 13 codified-rule skills (LLM skills not replayed) |
| `backend/app/services/trust_report.py` | Generate TRACK_RECORD.md + JSONL |
| `backend/app/services/recommendations.py` | Rule-based BUY/SELL/HOLD/WATCH scoring |
| `backend/app/services/llm_router.py` | 4-provider LLM fallback (GitHub→Gemini→OpenRouter→Qwen) |
| `backend/scripts/run_alerts.py` | CLI: eval alerts, push to Telegram (cron-friendly) |
| `backend/scripts/schedule_alerts.ps1` | Register Windows Task Scheduler job |
| `backend/app/models/portfolio.py` | Pydantic data models |
| `backend/app/core/config.py` | Env var loading |
| `backend/app/api/agents.py` | Agent execution endpoints |
| `backend/app/api/ops.py` | Ops / health / metrics endpoints |
| `backend/app/db/pool.py` | asyncpg connection pool |
| `backend/app/db/repositories/` | 8 repos: alerts, changes, crowding, equity, recommendations, signals, snapshots, weights |
| `backend/app/cache/redis_client.py` | Redis L2 cache client |
| `backend/app/jobs/scheduler.py` | APScheduler background jobs |
| `backend/app/jobs/tasks.py` | RQ task definitions |
| `backend/app/jobs/queues.py` | RQ queue setup |
| `backend/app/services/agent_registry.py` | Agent registration + runner wiring |
| `backend/app/services/event_dispatcher.py` | Event bus for async signal dispatch |
| `backend/app/services/market_regime.py` | Bull/bear/sideways regime classifier |
| `backend/app/services/risk_gate.py` | Pre-trade risk gate checks |
| `backend/app/services/discovery.py` | Stock discovery / screener |
| `backend/app/services/signal_change_detector.py` | Detects regime/signal transitions |
| `backend/app/services/llm_router.py` | 4-provider LLM fallback (GitHub→Gemini→OpenRouter→Qwen) |
| `backend/app/services/skill_llm_runner.py` | Runs skills via LLM router |
| `docker-compose.yml` | Postgres (TimescaleDB pg16) + Redis 7 |
| `.claude/skills/*/SKILL.md` | 21 skills |
| `.claude/context/changes.md` | Change log |
| `.claude/context/architecture.md` | This file |
| `supabase_schema.sql` | Optional snapshot history schema |