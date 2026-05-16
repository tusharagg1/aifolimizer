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

## MCP Tools (18 total — exposed to Claude)

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
| `get_fundamentals` | P/E, EPS, div yield, payout, market cap, earnings date, analyst target, beta | 6h |
| `get_technicals` | SMA20/50/200, RSI(14), MACD, Bollinger Bands, trend, RSI signal | 1h |
| `get_earnings_calendar` | Next earnings dates per holding, flags next-14-days | 6h |
| `get_earnings_results` | Last N quarters EPS estimate/actual/surprise/outcome per ticker | 12h |
| `get_news_headlines` | Recent headlines per ticker from yfinance news | 30m |
| `get_positioning_signals` | Crowding score, institutional ownership, short interest, headline velocity — flag consensus-crowded names | 6h |
| `get_crypto_data` | CoinGecko: price CAD, market cap, 24h/7d/30d change, ATH distance | 5m |
| `get_triggered_alerts` | Recent alert events from local jsonl log (price drop, RSI, earnings, concentration) | live |
| `run_alerts_now` | Evaluate alert rules vs live portfolio, append triggers to history | live |
| `list_analysis_modes` | All 12 available skills with tool lists | static |

## Analysis Skills (12 — in `.claude/skills/`)

| Skill | Framework | Key MCP tools |
|---|---|---|
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

## Commit Rules

- NEVER add `Co-Authored-By: Claude ...` trailer to commit messages
- NEVER add "Generated with Claude Code" footer or any AI-attribution to commits, PRs, or PR bodies
- Commit messages are authored solely by the human user — no AI co-author lines, no tool advertisements
