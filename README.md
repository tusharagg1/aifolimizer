# aifolimizer

> AI-powered investment advisor for Canadian Wealthsimple users.
> Live portfolio analysis through Claude Code or Claude Desktop using existing Pro subscription. **No Anthropic API key required.**

[![CI](https://github.com/tusharagg1/aifolimizer/actions/workflows/ci.yml/badge.svg)](https://github.com/tusharagg1/aifolimizer/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Next.js 14](https://img.shields.io/badge/next.js-14-black.svg)](https://nextjs.org/)

---

## What it does

- Logs into Wealthsimple (MFA-aware, GraphQL via `ws-api`)
- Aggregates holdings across TFSA / RRSP / FHSA / Non-Reg / Crypto accounts
- Enriches with live yfinance prices, sectors, day changes
- Exposes portfolio as **MCP tools** so Claude Code / Claude Desktop can analyze natively
- Ships **8 institutional analysis skills** that auto-trigger on intent

All inference runs inside Claude Pro subscription. Nothing goes to cloud server.

## Architecture

```
Claude Code / Claude Desktop   (your Pro subscription)
         ↓ invokes
   .claude/skills/*            (institutional analysis prompts)
         ↓ calls MCP tool
   backend/mcp_server.py       (FastMCP)
         ↓ uses
   app/services/{wealthsimple, market_data, macro, portfolio_analytics, quant, pii_filter}
         ↓ HTTPS
   Wealthsimple GraphQL   |   yfinance   |   FRED CSV
```

Next.js dashboard is separate, optional viewer hitting same FastAPI backend.

## Quick start

### 1. Backend

```powershell
cd backend
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy ..\.env.example .env
# Edit .env — fill WS_EMAIL, WS_PASSWORD
.venv\Scripts\python.exe run.py
```

Backend at http://127.0.0.1:8000.

### 2. Frontend (optional dashboard)

```powershell
cd frontend
npm install
copy ..\.env.example .env.local
npm run dev
```

Open http://localhost:3000. Log in with WS email + password + MFA OTP.

### 3. Register the MCP server with Claude Code

```powershell
claude mcp add aifolimizer "C:\Users\Tusha\Documents\projects\aifolimizer\backend\.venv\Scripts\python.exe" "C:\Users\Tusha\Documents\projects\aifolimizer\backend\mcp_server.py"
```

Or Claude Desktop — add to `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "aifolimizer": {
      "command": "C:\\Users\\Tusha\\Documents\\projects\\aifolimizer\\backend\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\Tusha\\Documents\\projects\\aifolimizer\\backend\\mcp_server.py"]
    }
  }
}
```

## Usage

### Analysis (Claude Code / Claude Desktop)

```
/portfolio-health        → BlackRock-style health report + rebalance plan
/risk-assessment         → Bridgewater stress test + hedges (uses vol/Sharpe/VaR)
/stock-analysis NVDA     → Goldman + Citadel deep dive
/macro-impact            → McKinsey macro briefing (live FRED data)
/dividend-strategy       → Harvard Endowment income blueprint
/earnings-analyzer AAPL  → JPMorgan pre-earnings brief
/sector-rotation         → Renaissance rotation signals
/tax-loss-review         → Canadian tax-loss harvesting (TFSA/RRSP-aware)
```

Skills auto-trigger from intent (e.g. "how risky is my portfolio?" → `/risk-assessment`).

### MCP tools exposed to Claude

| Tool | Returns |
|------|---------|
| `get_profile` | Account types + cash balances (PII-stripped) |
| `get_portfolio` | Live enriched positions + summary, aggregate or per-account |
| `get_xray` | ETF exposure expansion + sector/asset breakdown |
| `get_concentration_warnings` | Single-position / sector over-allocation flags |
| `get_tax_loss_candidates` | Underwater positions for tax-loss harvesting |
| `get_risk_metrics` | Annualized vol, Sharpe, Sortino, VaR 95%, ES |
| `get_correlation_matrix` | Pairwise correlation between top holdings |
| `get_macro_snapshot` | FRED data: Fed funds, 10Y, CPI, CAD/USD, BoC, unemployment |
| `list_analysis_modes` | All available analysis skills |

## Project layout

```
aifolimizer/
├── backend/                         # FastAPI + MCP server
│   ├── main.py                      # FastAPI app
│   ├── mcp_server.py                # MCP server (FastMCP)
│   ├── run.py                       # Entry point
│   ├── requirements.txt
│   └── app/
│       ├── api/ws.py                # REST endpoints for the dashboard
│       ├── models/portfolio.py      # Pydantic models
│       └── services/
│           ├── wealthsimple.py      # ws-api wrapper (login, MFA, positions)
│           ├── market_data.py       # yfinance enrichment
│           ├── macro.py             # FRED snapshot
│           ├── portfolio_analytics.py # X-ray, tax-loss, concentration
│           ├── quant.py             # Sharpe, VaR, correlation, beta
│           └── pii_filter.py        # Stripper before MCP responds
├── frontend/                        # Next.js 14 dashboard
│   ├── app/dashboard/page.tsx
│   ├── components/
│   └── lib/api.ts
├── .claude/                         # Claude Code project config
│   ├── skills/                      # 8 institutional analysis skills
│   ├── context/                     # architecture, changes
│   └── commands/
├── .github/                         # CI, issue/PR templates, dependabot
├── supabase_schema.sql              # Optional: snapshot history table
├── CLAUDE.md                        # Project rules for AI agents
├── CONTRIBUTING.md
├── CHANGELOG.md
├── SECURITY.md
└── LICENSE
```

## Privacy

- WS credentials live in local `backend/.env` only — gitignored, never deployed
- WS access token held in server RAM only, 8-hour TTL
- `pii_filter.py` strips account IDs / names / emails before any data reaches Claude
- Only ticker symbols, quantities, market values, weights, sectors sent through MCP
- See [SECURITY.md](SECURITY.md) for full threat model

## Documentation

- [CLAUDE.md](CLAUDE.md) — project rules for AI agents
- [.claude/context/architecture.md](.claude/context/architecture.md) — data flow + tool contracts
- [CHANGELOG.md](CHANGELOG.md) — version history
- [CONTRIBUTING.md](CONTRIBUTING.md) — dev setup + standards
- [SECURITY.md](SECURITY.md) — threat model + reporting

## License

[MIT](LICENSE) © 2026 Tushar Aggarwal

## Acknowledgements

- [`ws-api`](https://github.com/gboudreau/ws-api-python) — reverse-engineered Wealthsimple client
- [yfinance](https://github.com/ranaroussi/yfinance) — Yahoo Finance market data
- [FRED](https://fred.stlouisfed.org/) — Federal Reserve Economic Data
- [Anthropic Claude](https://www.anthropic.com/claude) — analysis engine
