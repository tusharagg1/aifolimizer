# aifolimizer

> Open-source MCP-native portfolio brain. Plugs a live brokerage account into Claude Desktop or Claude Code through a local backend, exposes 80 analysis tools, and ships 21 ready-to-run skills covering risk, earnings, macro, dividends, tax, technicals, and quant anomalies. Reference brokerage today is Wealthsimple; the market-data layer (yfinance, FRED, CoinGecko, plus 10 more adapters) sits behind a shared interface so paid feeds drop in one file at a time.
>
> Runs locally, uses an existing Claude Pro subscription, **no Anthropic API key required**.

[![CI](https://github.com/tusharagg1/aifolimizer/actions/workflows/ci.yml/badge.svg)](https://github.com/tusharagg1/aifolimizer/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-native-purple.svg)](https://modelcontextprotocol.io)
![Status: alpha](https://img.shields.io/badge/status-alpha-orange)

> **Disclaimer.** Educational and research tool. Output comes from an LLM and may be wrong. Verify independently before acting on any signal. Use at your own risk.

---

## Who it's for

- **Self-directed investors** who want institutional-grade analysis they can own, audit, and extend on a self-hosted stack.
- **Quant researchers and developers** who want a working reference for backtest rigor (deflated Sharpe, walk-forward, signal decay) and live track-record forward-testing.
- **Educators** teaching portfolio theory, factor investing, or LLM tooling — every skill is a documented playbook styled after a public investing tradition.

## What it does

- **Live brokerage portfolio.** Reference Wealthsimple integration ships in-box (MFA-aware, GraphQL via `ws-api`, TFSA / RRSP / FHSA / Non-Reg / Crypto). Holdings, cost basis, account types, and cash balances flow from the actual account; cross-account aggregation and tax-aware logic run server-side. Plaid / Schwab / IBKR are on the roadmap behind a `Brokerage` interface.
- **13 market-data adapters** (yfinance, Finnhub, Twelve Data, Tiingo, EODHD, Stooq, Binance, CoinGecko, Frankfurter, Alpha Vantage, plus a Wealthsimple cross-check) share a clean abstract base at [`data_sources/base.py`](backend/app/services/data_sources/base.py). The `data_router` chains them with circuit-breaker fallback. Adding Polygon, Refinitiv, or another paid feed is a one-file adapter.
- **80 MCP tools** covering live prices, fundamentals, technicals (SMA / RSI / MACD / Bollinger / Minervini stage), macro from FRED, crowding and positioning, crypto, insider activity, options chains with Greeks, sentiment from Reddit and StockTwits, and geopolitical signals from GDELT. Any MCP client — Claude Desktop, Claude Code, Cursor — picks them up the moment the server is registered.
- **21 analysis skills** styled after public investing traditions: Graham / Buffett / Lynch fundamental lenses, Dalio All-Weather risk concepts, allocation health, sector rotation, endowment-style income, pre-earnings, technical analysis, top-down macro, plus quant anomaly skills (PEAD, momentum). Auto-trigger on natural-language intent. (Firm names indicate stylistic inspiration drawn from public writing.)
- **Forward-tested where it matters.** Trade-oriented skills (`pre-trade-check`, `position-review`) write every recommendation to `recommendations.jsonl` with entry, stop, and target. A nightly scheduler marks open recommendations to market; rolling 7 / 30 / 90-day win rates surface via `get_live_track_record`. Alpha vs XEQT / SPY / TSX / QQQ comes from `get_alpha_attribution` once the daily NAV pipeline (`snapshot_portfolio_equity`) has ~30 days of history. Other skills are read-only analysis surfaces — they inform decisions without carrying forward-tracked predictions.
- **Quant rigor**, not vibes — walk-forward OOS validation ([`skill_backtest.py:585-740`](backend/app/services/skill_backtest.py)), deflated Sharpe with Bailey-López de Prado 2014 ([`skill_backtest.py:68-128`](backend/app/services/skill_backtest.py)), Brier + ECE reliability bins ([`calibration.py:72-148`](backend/app/services/calibration.py)), signal-decay curves ([`signal_history.py:506-569`](backend/app/services/signal_history.py)), regime-conditional gating + nightly weight tuner ([`market_regime.py`](backend/app/services/market_regime.py), [`weights_tuner.py`](backend/app/services/weights_tuner.py)).
- **Runs locally.** Most state lives in JSONL files under `~/.aifolimizer/` and `backend/.claude/context/`. Postgres (TimescaleDB) and Redis are available via `docker compose up -d` for richer history and cross-process caching.

Primary inference runs inside an existing Claude Pro session — no separate API key, no third-party LLM beyond Claude. Optional free-tier LLM fallbacks (GitHub Models, Gemini, OpenRouter, Qwen) are off unless a provider key is set; the same redaction rules apply (see [Privacy](#privacy)).

## Status & Roadmap

aifolimizer is a single-user local tool today. Brokerage support is Wealthsimple. Market data flows from yfinance, FRED, and CoinGecko on free, delayed feeds. Tests cover the quant logic and core services; auth and route handlers are exercised manually. Coming: a `Brokerage` interface for Plaid / Schwab / IBKR, multi-user identity with OAuth/SSO, append-only audit logging, KMS-backed token storage, and integration tests on the auth and MCP-route surface.

## Architecture

```
Claude Code / Claude Desktop   (Pro subscription)
         ↓ invokes
   .claude/skills/*            (21 analysis skills)
         ↓ calls MCP tools
   backend/mcp_server.py       (FastMCP — 80 tools)
         ↓ uses
   app/services/*              (50+ service modules)
         ↓
   Wealthsimple GraphQL  |  yfinance  |  FRED  |  CoinGecko  |  …
         ↓
   Postgres (TimescaleDB)  +  Redis      (optional, via Docker)
   JSONL state files                     (always — ~/.aifolimizer/, backend/.claude/context/)
```

## Quick start

> Commands below use bash (works on macOS/Linux/WSL/Git-Bash). For native Windows PowerShell equivalents, see [scripts/AUTOMATION.md](scripts/AUTOMATION.md). Replace `<REPO>` with the absolute repo path.

**Prerequisites:** Python 3.12+, Docker Desktop (optional, for Postgres + Redis), Claude Code CLI or Claude Desktop (Pro), a Wealthsimple account.

```bash
# 1. Postgres password file (required before docker compose up)
mkdir -p .secrets && openssl rand -hex 24 > .secrets/pg_password.txt && chmod 600 .secrets/pg_password.txt

# 2. Start infrastructure (optional — Postgres + Redis)
docker compose up -d

# 3. Backend install
cd backend
python -m venv .venv && source .venv/bin/activate
pip install --upgrade pip && pip install -r requirements.txt
cp ../.env.example .env && $EDITOR .env    # fill WS_EMAIL, WS_PASSWORD

# 4. First-time Wealthsimple login (MFA — re-run only when WS forces re-auth)
python mcp_login.py

# 5. Run the backend (http://127.0.0.1:8000)
python run.py
```

Tokens persist to `~/.aifolimizer/ws_session.json` (mode 0600 on POSIX; NTFS-protected on Windows) so backend restarts resume without re-entering OTP. For an always-on service with scheduled-skill execution, see [scripts/AUTOMATION.md](scripts/AUTOMATION.md) (Windows/Task Scheduler today; `launchd` / `systemd` / `cron` snippets in the POSIX appendix).

**Register the MCP server with Claude:**

```bash
claude mcp add aifolimizer "<REPO>/backend/.venv/bin/python" "<REPO>/backend/mcp_server.py"
```

Or copy `.mcp.example.json` → `.mcp.json` and replace `<REPO_ROOT>`. For Claude Desktop, edit `claude_desktop_config.json` (see [docs/FAQ.md](docs/FAQ.md) for OS-specific paths).

Restart Claude, then ask "get my profile" or run `/daily-briefing` to verify live data is flowing.

**Optional:** Telegram alerts (set `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` in `.env`) and scheduled skills — full runbook in [scripts/AUTOMATION.md](scripts/AUTOMATION.md).

## Tests

```bash
cd backend && PYTHONPATH=. python -m pytest tests/ -q
```

## Usage

Skills auto-trigger on intent in Claude. Or invoke directly:

```
/daily-briefing           → Morning digest: portfolio, macro, alerts, earnings
/portfolio-health         → Health report + rebalance plan
/risk-assessment          → Stress test + hedge candidates
/stock-analysis NVDA      → Fundamental + technical deep dive
/stock-compare NVDA AAPL  → Head-to-head matchup
/adversarial-research VFV → Bull / bear / consensus pipeline
/macro-impact             → Macro briefing on live FRED data
/dividend-strategy        → Income blueprint
/earnings-analyzer AAPL   → Pre-earnings brief
/earnings-postmortem AAPL → EPS beat/miss breakdown post-report
/sector-rotation          → Rotation signals
/tax-loss-review          → Canadian tax-loss harvesting (TFSA/RRSP-aware)
/cash-deployment          → Add-to-winners with concentration + crowding guard
/pre-trade-check TSLA     → Risk gate before entering a position
/auto-rebalance           → Drift-based rebalance recommendations
/weekly-mirror            → Weekly portfolio review against goals
```

Sample outputs (synthetic data) live under [docs/examples/](docs/examples/).

## MCP tools (80 total — table below highlights core 32; see `backend/mcp_server.py` for the full list)

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
| `get_options_chain` | Options chain with Black-Scholes Greeks | 15m |
| `list_analysis_modes` | Filesystem-driven list of all 21 skills + their MCP tools | static |

## Project layout

```
aifolimizer/
├── backend/
│   ├── main.py, mcp_server.py, run.py, requirements.txt
│   └── app/
│       ├── api/         # REST endpoints (ws.py, agents.py, ops.py)
│       ├── db/          # Postgres pool + 8 repositories
│       ├── cache/       # Redis client
│       ├── jobs/        # Scheduler + task queue (RQ)
│       ├── models/      # Pydantic models
│       └── services/    # 50+ service modules
├── .claude/
│   ├── skills/          # 21 analysis skills
│   ├── context/         # architecture.md, changes.md, lessons.md, STATE.md
│   └── agents/
├── docs/                # FAQ + sample skill outputs
├── scripts/             # AUTOMATION runbook + PowerShell launchers
├── docker-compose.yml   # Postgres (TimescaleDB) + Redis
├── .github/             # CI, issue/PR templates, dependabot
├── CLAUDE.md, AGENTS.md # Project rules / agent context
├── TRACK_RECORD.md      # Live recommendation track record
└── LICENSE
```

## Privacy

`pii_filter.py` strips account IDs, account numbers, internal Wealthsimple IDs, user IDs, email, and full name from the three portfolio-bearing tools (`get_profile`, `get_portfolio`, `get_portfolio_analysis`). Dollar amounts (book cost, market value, cash balance) stay on the machine within the local Claude Pro session. Most other MCP tools (technicals, fundamentals, macro, news, crypto) carry no PII and bypass the filter.

**What leaves the machine:** prompts to Anthropic via the local Claude Pro session — symbols, weights (% of NLV), returns %, scores, and public market data. Optional free-LLM fallbacks (GitHub Models / Gemini / OpenRouter / Qwen) follow the same %-of-NAV redaction rules and only fire if their key is set in `.env`. Outbound market-data fetches (yfinance / FRED / CoinGecko) see the ticker list, nothing else.

**What never leaves:** Wealthsimple email, password, OTP, access/refresh tokens, account IDs, account numbers, full name. Credentials live in local `backend/.env` (gitignored, never persisted to disk past process memory). Tokens persist to `~/.aifolimizer/ws_session.json` outside the repo.

`get_trade_ticket` is a notable exception — it returns raw `dollar_amount_cad` and `max_loss_cad` for the local Claude consumer, so its output stays inside the local session.

Wealthsimple access uses a community-maintained reverse-engineered API (`ws-api`). Wealthsimple does not officially support automated access; use is at your own risk and may violate Wealthsimple ToS.

Full threat model and hardening checklist: [SECURITY.md](SECURITY.md). Credential FAQs: [docs/FAQ.md](docs/FAQ.md).

## Infrastructure

```bash
docker compose up -d        # Postgres + Redis
docker compose down         # stop
docker compose logs -f      # tail
```

Data persisted in `.data/` (gitignored). Secrets in `.secrets/pg_password.txt`.

## Documentation

- [scripts/AUTOMATION.md](scripts/AUTOMATION.md) — scheduled-skill runbook, MFA re-auth, NSSM service, Telegram, troubleshooting (Windows + POSIX appendix)
- [docs/FAQ.md](docs/FAQ.md) — common setup, privacy, and usage questions
- [docs/examples/](docs/examples/) — synthetic skill outputs + redacted prompt sample
- [SECURITY.md](SECURITY.md) — threat model, disclosure policy, hardening checklist
- [.env.example](.env.example) — every supported env var, with comments
- [CLAUDE.md](CLAUDE.md) / [AGENTS.md](AGENTS.md) — project rules / agent context
- [TRACK_RECORD.md](TRACK_RECORD.md) — live recommendation performance

## Contributing

Issues and PRs welcome. Counts of MCP tools (80) and skills (21) cited in CLAUDE.md / README.md / AGENTS.md / architecture.md are guarded by `python backend/scripts/check_doc_counts.py` — runs in CI after lint, fails the build if a doc claim drifts. Run locally before editing those numbers. `TRACK_RECORD.md` is auto-generated by the `generate_trust_report` MCP tool — refresh by calling the tool rather than editing by hand.

## Troubleshooting

- **Port conflicts (5432, 6379, 8000).** Remap in `docker-compose.yml` (e.g. `"5433:5432"`) or change the backend port via `uvicorn main:app --port 8001`.
- **First `/daily-briefing` is slow (~30s).** Cold cache — fundamentals, technicals, and macro all fetch from upstream on the first call. Subsequent calls hit L1 + diskcache and return in <2s.
- **Wealthsimple MFA timeout.** Refresh token expired or WS forced re-auth. Re-run `python mcp_login.py` from `backend/`.
- **Telegram `getUpdates` returns empty.** Telegram exposes `chat.id` only after the bot has received at least one message. Open the bot, send any text, then re-fetch.
- **`claude mcp list` is slow (~5s).** Eager imports in `mcp_server.py` (yfinance, pandas, ta). Harmless — only paid on first invocation per session.

More in [docs/FAQ.md](docs/FAQ.md).

## Uninstall

```bash
claude mcp remove aifolimizer                # 1. unregister MCP
docker compose down -v                       # 2. tear down Docker (-v drops volumes)
rm -rf ~/.aifolimizer                        # 3. remove session tokens (Windows: Remove-Item -Recurse -Force ~\.aifolimizer)
schtasks /delete /tn "aifolimizer-*" /f      # 4. Windows scheduled tasks (skip on POSIX)
nssm remove aifolimizer-backend confirm      # 5. NSSM service (only if installed)
```

## License

[MIT](LICENSE) © 2026 Tushar Aggarwal

## Acknowledgements

- [`ws-api`](https://github.com/gboudreau/ws-api-python) — reverse-engineered Wealthsimple client
- [yfinance](https://github.com/ranaroussi/yfinance) — Yahoo Finance market data
- [FRED](https://fred.stlouisfed.org/) — Federal Reserve Economic Data
- [CoinGecko](https://www.coingecko.com/) — Crypto market data
- [Anthropic Claude](https://www.anthropic.com/claude) — analysis engine
