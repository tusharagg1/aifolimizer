# aifolimizer — Project Context

## Session Startup (read this every new session)
1. Read `.claude/context/changes.md` — what's built and when
2. Read `.claude/context/architecture.md` — data flow, API contracts, file index
3. Read `.claude/context/lessons.md` — past corrections, do-not-repeat rules
4. Call `mcp__aifolimizer__get_profile` before any analysis — never hardcode account types or capital

## What This Is
AI-powered investment advisor for Canadian Wealthsimple user (age 32, growth + income + crypto).
Live portfolio data via local backend. AI analysis runs in Claude Code / Claude Desktop (Pro plan) — no Anthropic API key.

## Architecture

```
Claude Code / Claude Desktop  (Pro subscription — no API key)
        ↓ invokes
   .claude/skills/*  (9 institutional analysis skills)
        ↓ calls MCP tool
   backend/mcp_server.py  (FastMCP, 14 tools)
        ↓ uses
   app/services/{wealthsimple, market_data, fundamentals, technicals, news, macro, quant, portfolio_analytics, health_score, crypto_data}
        ↓ HTTP
   Wealthsimple + yfinance + FRED + CoinGecko (all free, no keys required)

Next.js Dashboard (separate, optional — port 3000)
        ↓ REST calls
   backend/app/api/ws.py  (FastAPI, port 8000)
        └─ same services as MCP
```

## How to Start

```bash
# Terminal 1 — backend (FastAPI + shared session store)
cd backend && .venv/Scripts/activate && uvicorn main:app --reload --port 8000

# Terminal 2 — frontend
cd frontend && npm run dev
```
MCP server (`mcp_server.py`) runs as separate process managed by Claude Code — register once with:
```
claude mcp add aifolimizer "<venv_python_path>" "backend/mcp_server.py"
```

## MCP Tools (32 total — exposed to Claude; not all listed below — see `mcp_server.py` for full)

| Tool | Returns | Cache |
|---|---|---|
| `get_profile` | Account types (TFSA/RRSP/etc), cash balances, total invested — PII stripped | session |
| `get_portfolio` | Live enriched positions + summary (aggregate or per-account) — PII stripped | live |
| `get_xray` | ETF exposure expansion + sector/asset-class breakdown | live |
| `get_concentration_warnings` | Single-position / sector over-allocation flags | live |
| `get_tax_loss_candidates` | Underwater positions for Canadian tax-loss harvesting | live |
| `get_risk_metrics` | Annualized vol, Sharpe, Sortino, VaR 95%, ES, max drawdown | 1h |
| `get_correlation_matrix` | Pairwise correlation between top N holdings | 1h |
| `get_macro_snapshot` | FRED: Fed funds, 10Y yield, CPI, CAD/USD, BoC rate, unemployment | 12h |
| `get_fundamentals` | P/E, EPS, div yield, payout, market cap, earnings date, analyst target, beta | 6h (L1+L2) |
| `get_technicals` | SMA20/50/200, RSI(14), MACD, Bollinger Bands, trend, RSI signal | 1h |
| `get_earnings_calendar` | Next earnings dates per holding, flags next-14-days | 6h |
| `get_earnings_results` | Last N quarters EPS estimate/actual/surprise/outcome per ticker | 12h |
| `get_news_headlines` | Recent headlines per ticker from yfinance news | 30m |
| `get_positioning_signals` | Crowding score, institutional ownership, short interest, headline velocity — flag consensus-crowded names | 6h (L1+L2) |
| `snapshot_positioning_history` | Append today's crowding scores to JSONL log (idempotent per-day). Run daily to build regime-shift dataset. | live |
| `get_crowding_shifts` | Detect symbols whose crowding score shifted ≥threshold over lookback. Reads from history JSONL. | live |
| `get_crypto_data` | CoinGecko: price CAD, market cap, 24h/7d/30d change, ATH distance | 5m |
| `get_triggered_alerts` | Recent alert events from local jsonl log (price drop, RSI, earnings, concentration) | live |
| `run_alerts_now` | Evaluate alert rules vs live portfolio, append triggers to history | live |
| `backtest_portfolio` | Per-symbol rule-replay (buy_hold / rsi_swing / sma_cross / crowd_fade / crowd_buy). Supports `tx_cost_bps` + `walk_forward`. | 1h |
| `get_skill_track_record` | Backtest all 13 skills as codified rules over 3-5yr historical bars. Returns CAGR, Sharpe, Sortino, max DD, hit-rate, alpha vs SPY/XEQT. | disk |
| `log_recommendation` | Log a skill rec (action, conviction, entry price, target, stop) to recommendations.jsonl for forward tracking. | live |
| `score_recommendations` | Mark-to-market all open recs, flag stops/targets hit. Returns win-rate + avg return. | live |
| `get_live_track_record` | Rolling 7/30/90d win-rate and P&L from scored recs. By-conviction breakdown. | live |
| `snapshot_portfolio_equity` | Append today's total NAV to portfolio_history.jsonl (idempotent per day). | live |
| `get_alpha_attribution` | Annualized alpha, beta, Sharpe, info ratio, tracking error vs SPY/XEQT/TSX/QQQ. Includes WS Managed AUM benchmark. | live |
| `get_quote_with_source` | Live quote with data-source attribution (yfinance→finnhub→tiingo→stooq fallback). | 5m |
| `get_quotes_batch` | Batch quote fetch for N symbols in one HTTP call — 13x faster than serial. | 5m |
| `get_data_source_reliability` | Per-source success rate + avg latency over trailing window. Trust-signal evidence. | live |
| `generate_trust_report` | Write TRACK_RECORD.md (public) + track_record_full.jsonl (private). Git-commit to timestamp. | live |
| `list_analysis_modes` | All 13 available skills with tool lists | static |

L1+L2: in-process dict + cross-process diskcache. MCP and FastAPI share L2 so cold MCP restarts don't re-fetch yfinance if FastAPI warmed within TTL.

## Analysis Skills (13 — in `.claude/skills/`)

| Skill | Framework | Key MCP tools |
|---|---|---|
| `daily-briefing` | One-shot morning digest composing 7 MCP tools | get_profile, get_portfolio, get_macro_snapshot, get_concentration_warnings, get_triggered_alerts, get_earnings_calendar, get_positioning_signals |
| `portfolio-health` | BlackRock Portfolio Builder | get_profile, get_portfolio, get_xray, get_concentration_warnings |
| `risk-assessment` | Bridgewater Risk Assessment | get_profile, get_portfolio, get_risk_metrics, get_correlation_matrix |
| `stock-analysis` | Goldman Sachs + Citadel TA | get_profile, get_portfolio, get_fundamentals, get_technicals, get_news_headlines, get_positioning_signals |
| `stock-compare` | Head-to-head A vs B matchup | get_profile, get_portfolio, get_fundamentals, get_technicals, get_news_headlines |
| `macro-impact` | McKinsey Macro | get_profile, get_portfolio, get_macro_snapshot |
| `dividend-strategy` | Harvard Endowment Dividend | get_profile, get_portfolio, get_fundamentals |
| `earnings-analyzer` | JPMorgan Earnings | get_profile, get_portfolio, get_earnings_calendar, get_fundamentals |
| `earnings-postmortem` | Post-report EPS beat/miss breakdown | get_profile, get_portfolio, get_earnings_results, get_fundamentals, get_news_headlines |
| `sector-rotation` | Renaissance / Sector Rotation | get_profile, get_portfolio, get_xray |
| `tax-loss-review` | Canadian tax-loss harvesting | get_profile, get_tax_loss_candidates |
| `adversarial-research` | Multi-agent bull/bear/consensus pipeline | get_profile, get_portfolio, get_fundamentals, get_technicals, get_news_headlines, get_macro_snapshot, get_positioning_signals |
| `cash-deployment` | Add-to-winners cash deployment with concentration + crowding guard | get_profile, get_portfolio, get_concentration_warnings, get_fundamentals, get_technicals, get_positioning_signals |

Each skill: auto-triggers from description frontmatter, calls get_profile FIRST, then MCP tools, runs analysis in Claude's context.

## Investor Profile (use as context, always verify with get_profile)

- Age: 32, Canadian resident
- Philosophy: growth stocks, index ETFs (XEQT/VFV), dividends, crypto
- Risk: mixed — conservative (bonds/GIC), moderate (index ETFs), aggressive (stocks, crypto)
- Time horizons: day trading + short-term (<3yr) + long-term (10yr+)
- Tax: TFSA (gains tax-free), RRSP (tax-deferred), Non-Reg (50% capital gains inclusion)
- **Capital and account balances: ALWAYS pull from `get_profile` — never hardcode**
- **Crowding awareness**: when AI recommends adding to a name, call `get_positioning_signals` first. Consensus-crowded names (score ≥ 70) have negative expected alpha for late entries per Goldman / BlackRock 2025 research on AI-driven retail + quant crowding. Defer adds on consensus names; favor contrarian setups (score ≤ 30) when fundamentals support

## Tech Stack

| Layer | Tech |
|---|---|
| Frontend | Next.js 16 (App Router) + Tailwind 4 + Recharts 3 |
| Backend API | FastAPI + uvicorn (Python 3.12) |
| MCP server | FastMCP (shares services with FastAPI) |
| Technical indicators | `ta>=0.11.0` (NOT pandas-ta — incompatible with Python 3.14) |
| Prices + fundamentals | yfinance (free, no key) |
| Macro data | FRED public CSV API (free, no key) |
| Crypto data | CoinGecko v3 free API (no key, 30 req/min) |
| AI inference | Claude Code / Claude Desktop Pro (no Anthropic API key) |
| Optional DB | Supabase (snapshot history only) |

## Privacy Rules (NON-NEGOTIABLE)

<important if="touching backend/, .env, mcp_server.py, pii_filter.py, or any MCP tool response">
- WS_EMAIL/WS_PASSWORD: local `.env` only, never committed, logged, or sent to AI
- WS access token: server RAM only (Python dict), 8h TTL, never persisted
- `pii_filter.py` MUST run before every MCP tool response
- Account IDs, numbers, email, full name: NEVER leave server
</important>

## Environment Variables

```bash
# backend/.env (local only — never commit)
WS_EMAIL=...
WS_PASSWORD=...
SUPABASE_URL=...        # optional
SUPABASE_SERVICE_KEY=...  # optional

# frontend/.env.local (local only)
NEXT_PUBLIC_API_URL=http://localhost:8000
```

## Code Rules

- No hardcoded capital amounts or account types — always read from `get_profile`
- No PII in logs, DB, or MCP output
- Functions short and single-purpose
- No comments explaining WHAT — only WHY when non-obvious
- Append to `.claude/context/changes.md` after significant changes
- Auto skills builder: `python backend/scripts/build_skills.py` lists all tools + skills
- Scaffold new skill: `python backend/scripts/build_skills.py --scaffold <tool_name>`

## Workflow Rules

- **Verify before "done."** Compile-clean ≠ working. Run import-check (backend) or `tsc --noEmit` + lint (frontend) AND exercise the changed code with realistic input. Empty-import tests miss `UnboundLocalError` and shape mismatches.
- **Lessons loop.** After any user correction or surprise bug, append a short rule to `.claude/context/lessons.md`. Goal: same mistake never recurs.
- **Pause for elegance on non-trivial changes** (3+ files or a new abstraction). Ask "is there a cleaner path?" before commit. Skip for one-line fixes — don't over-engineer trivial work.
- **Surgical changes only.** Touch only what the request requires. Don't clean up adjacent code. Match existing style. Mention unrelated dead code rather than deleting it. Remove only imports/variables your changes made unused.

## Commit Rules

- NEVER add `Co-Authored-By: Claude ...` trailer to commit messages
- NEVER add "Generated with Claude Code" footer or any AI-attribution to commits, PRs, or PR bodies
- Commit messages are authored solely by the human user — no AI co-author lines, no tool advertisements
