# aifolimizer

> AI-powered investment advisor for Canadian Wealthsimple users.
> Live portfolio analysis through Claude Code or Claude Desktop using your Pro subscription. **No Anthropic API key required.**

[![CI](https://github.com/tusharagg1/aifolimizer/actions/workflows/ci.yml/badge.svg)](https://github.com/tusharagg1/aifolimizer/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)

---

## What it does

- Connects to Wealthsimple (MFA-aware, GraphQL via `ws-api`) — reads live holdings across TFSA / RRSP / FHSA / Non-Reg / Crypto
- Enriches with live prices, fundamentals, technicals, macro data, crowding signals
- Exposes everything as **80 MCP tools** so Claude Code / Claude Desktop can analyze natively
- Ships **21 institutional analysis skills** that auto-trigger on intent
- Logs every recommendation, marks to market, builds a live track record
- Postgres (TimescaleDB) + Redis on Docker for persistent history and caching

Primary inference runs inside your Claude Pro subscription — no API key, nothing leaves your machine. Optional free-tier LLM fallbacks are off unless you add a provider key (see [Privacy](#privacy)).

## Architecture

```
Claude Code / Claude Desktop   (your Pro subscription)
         ↓ invokes
   .claude/skills/*            (21 institutional analysis skills)
         ↓ calls MCP tools
   backend/mcp_server.py       (FastMCP — 80 tools)
         ↓ uses
   app/services/*              (50+ service modules)
         ↓
   Wealthsimple GraphQL  |  yfinance  |  FRED  |  CoinGecko
         ↓
   Postgres (TimescaleDB)  +  Redis   (Docker — local)
```

## Quick start

### 1. Start infrastructure

```powershell
# Requires Docker Desktop running
docker compose up -d
```

### 2. Backend

```powershell
cd backend
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy ..\.env.example .env
# Edit .env — fill WS_EMAIL, WS_PASSWORD
.venv\Scripts\python.exe run.py
```

Backend at http://127.0.0.1:8000.

### 3. Register MCP server with Claude Code

```powershell
claude mcp add aifolimizer "C:\path\to\aifolimizer\backend\.venv\Scripts\python.exe" "C:\path\to\aifolimizer\backend\mcp_server.py"
```

Or Claude Desktop — add to `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "aifolimizer": {
      "command": "C:\\path\\to\\aifolimizer\\backend\\.venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\aifolimizer\\backend\\mcp_server.py"]
    }
  }
}
```

## Usage

Type naturally in Claude — skills auto-trigger on intent. Or invoke directly:

```
/daily-briefing           → Morning digest: portfolio, macro, alerts, earnings
/portfolio-health         → BlackRock-style health report + rebalance plan
/risk-assessment          → Bridgewater stress test + hedges
/stock-analysis NVDA      → Goldman + Citadel fundamental + technical deep dive
/stock-compare NVDA AAPL  → Head-to-head matchup
/adversarial-research VFV → Bull / bear / consensus pipeline
/macro-impact             → McKinsey macro briefing (live FRED data)
/dividend-strategy        → Harvard Endowment income blueprint
/earnings-analyzer AAPL   → JPMorgan pre-earnings brief
/earnings-postmortem AAPL → EPS beat/miss breakdown post-report
/sector-rotation          → Renaissance rotation signals
/tax-loss-review          → Canadian tax-loss harvesting (TFSA/RRSP-aware)
/cash-deployment          → Add-to-winners with concentration + crowding guard
/pre-trade-check TSLA     → Risk gate before entering a position
/auto-rebalance           → Drift-based rebalance recommendations
/weekly-mirror            → Weekly portfolio review against goals
```

## MCP tools (80 total — table below highlights core 32; see `backend/mcp_server.py` for full list)

| Tool | Returns | Cache |
|------|---------|-------|
| `get_profile` | Account types, cash balances (PII-stripped) | session |
| `get_portfolio` | Live enriched positions + summary | live |
| `get_xray` | ETF exposure expansion + sector breakdown | live |
| `get_concentration_warnings` | Over-allocation flags | live |
| `get_tax_loss_candidates` | Underwater positions for harvesting | live |
| `get_risk_metrics` | Vol, Sharpe, Sortino, VaR 95%, ES, max DD | 1h |
| `get_correlation_matrix` | Pairwise correlation between top holdings | 1h |
| `get_macro_snapshot` | FRED: Fed funds, 10Y, CPI, CAD/USD, BoC | 12h |
| `get_fundamentals` | P/E, EPS, div yield, payout, beta, analyst target | 6h |
| `get_technicals` | SMA, RSI, MACD, Bollinger, Minervini stage | 1h |
| `get_earnings_calendar` | Next earnings dates, flags next-14-day names | 6h |
| `get_earnings_results` | Last N quarters EPS estimate/actual/surprise | 12h |
| `get_news_headlines` | Recent headlines per ticker | 30m |
| `get_positioning_signals` | Crowding score, inst%, short%, headline velocity | 6h |
| `get_crypto_data` | CoinGecko: price CAD, market cap, 24h/7d/30d | 5m |
| `get_triggered_alerts` | Recent alert events from local log | live |
| `run_alerts_now` | Evaluate alert rules vs live portfolio | live |
| `backtest_portfolio` | Rule-replay: buy_hold/rsi_swing/sma_cross/crowd_fade | 1h |
| `get_skill_track_record` | Backtest 13 codified-rule skills (3-5yr) | disk |
| `log_recommendation` | Log rec with entry price, target, stop | live |
| `score_recommendations` | Mark open recs to market, flag stops/targets hit | live |
| `get_live_track_record` | Rolling 7/30/90d win-rate + P&L | live |
| `snapshot_portfolio_equity` | Append today's NAV to history (idempotent) | live |
| `get_alpha_attribution` | Alpha/beta vs SPY/XEQT/TSX/QQQ | live |
| `get_quote_with_source` | Live quote with data-source attribution | 5m |
| `get_quotes_batch` | Batch quote fetch (13x faster than serial) | 5m |
| `get_data_source_reliability` | Per-source success rate + latency | live |
| `snapshot_positioning_history` | Append crowding scores to JSONL (daily) | live |
| `get_crowding_shifts` | Detect symbols with crowding score shifts | live |
| `generate_trust_report` | Write TRACK_RECORD.md + full JSONL | live |
| `get_positioning_signals` | Goldman/BlackRock consensus-crowding flags | 6h |
| `list_analysis_modes` | Filesystem-driven list of all 21 skills + their MCP tools | static |

## Project layout

```
aifolimizer/
├── backend/
│   ├── main.py                      # FastAPI app
│   ├── mcp_server.py                # 80 MCP tools (FastMCP)
│   ├── run.py                       # uvicorn entry point
│   ├── requirements.txt
│   └── app/
│       ├── api/                     # REST endpoints (ws.py, agents.py, ops.py)
│       ├── db/                      # Postgres pool + 8 repositories
│       ├── cache/                   # Redis client
│       ├── jobs/                    # Scheduler + task queue (RQ)
│       ├── models/                  # Pydantic models
│       └── services/                # 50+ service modules
├── .claude/
│   ├── skills/                      # 21 institutional analysis skills
│   ├── context/                     # architecture.md, changes.md, lessons.md
│   └── agents/                      # Agent definitions
├── docker-compose.yml               # Postgres (TimescaleDB) + Redis
├── .github/                         # CI, issue/PR templates, dependabot
├── CLAUDE.md                        # Project rules for AI agents
├── AGENTS.md                        # Agent-optimized project context
├── TRACK_RECORD.md                  # Live recommendation track record
└── LICENSE
```

## Privacy

- WS credentials in local `backend/.env` only — gitignored, never deployed. **Password is never written to disk.**
- WS access + refresh token live in server RAM and are also persisted to `~/.aifolimizer/ws_session.json` (mode 0600, owner-only, **outside the repo**) so a backend restart resumes without re-entering OTP. 8-hour TTL; the file is auto-cleared when stale or rejected. Delete it to force a fresh login.
- `pii_filter.py` strips account IDs / numbers / names / emails before any data reaches Claude. Only ticker symbols, quantities, market values, weights, and sectors are sent through MCP.
- **Inference:** primary analysis runs inside your Claude Code / Claude Desktop Pro session (no API key, nothing leaves the machine). Optional free-tier LLM fallbacks (GitHub Models, Gemini, OpenRouter, Qwen) are used **only if you set the matching API key** in `backend/.env`. When enabled, their prompts carry symbols, **relative weights (% of NLV)**, returns %, and scores — **never absolute dollar balances, account IDs, email, name, or your WS token.** Leave the keys unset to keep all inference on-machine.

## Infrastructure

```powershell
# Start Postgres + Redis
docker compose up -d

# Stop
docker compose down

# View logs
docker compose logs -f
```

Data persisted in `.data/` (gitignored). Secrets in `.secrets/pg_password.txt`.

## Documentation

- [CLAUDE.md](CLAUDE.md) — project rules for AI agents
- [AGENTS.md](AGENTS.md) — agent-optimized condensed project context
- [.claude/context/architecture.md](.claude/context/architecture.md) — data flow + service contracts
- [.claude/context/changes.md](.claude/context/changes.md) — change log
- [TRACK_RECORD.md](TRACK_RECORD.md) — live recommendation performance

## Contributing

- Counts of MCP tools and skills cited in CLAUDE.md / README.md / AGENTS.md / architecture.md are guarded by `python backend/scripts/check_doc_counts.py` — runs in CI after lint, fails the build if a doc claim drifts from the real `@mcp.tool()` decorator count or `.claude/skills/` folder count. Run it locally before editing those numbers.
- TRACK_RECORD.md is auto-generated by the `generate_trust_report` MCP tool — do not hand-edit; refresh by calling the tool.

## License

[MIT](LICENSE) © 2026 Tushar Aggarwal

## Acknowledgements

- [`ws-api`](https://github.com/gboudreau/ws-api-python) — reverse-engineered Wealthsimple client
- [yfinance](https://github.com/ranaroussi/yfinance) — Yahoo Finance market data
- [FRED](https://fred.stlouisfed.org/) — Federal Reserve Economic Data
- [CoinGecko](https://www.coingecko.com/) — Crypto market data
- [Anthropic Claude](https://www.anthropic.com/claude) — analysis engine
