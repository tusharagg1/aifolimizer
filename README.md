<div align="center">

# aifolimizer

<sub>(**AI** Port**foli**o Opti**mizer**)</sub>

***Markets analysis in Claude: any ticker, or your whole Wealthsimple portfolio.***

[![CI](https://github.com/tusharagg1/aifolimizer/actions/workflows/ci.yml/badge.svg)](https://github.com/tusharagg1/aifolimizer/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-native-purple.svg)](https://modelcontextprotocol.io)
[![Status: alpha](https://img.shields.io/badge/status-alpha-orange)](#status--roadmap)

**[Setup guide](docs/SETUP.md)** · [FAQ](docs/FAQ.md) · [Track record](TRACK_RECORD.md) · [Security](SECURITY.md)

</div>

aifolimizer turns Claude into a markets analyst you drive in plain English: fundamentals, technicals, earnings, macro, options, and quant signals on **any ticker, no brokerage account required**. Connect Wealthsimple (optional) and the same tools go portfolio-aware: allocation, concentration, tax-loss, and rebalancing on your real holdings.

It runs on your own Claude Pro plan and your own machine, and only tickers and percentages ever leave it. **103 MCP tools, 27 skills, 15 market-data adapters.** Claude writes the analysis; the integration, privacy filter, forward-test harness, and adapter layer are built around it.

### Example prompts

```text
/stock-analysis NVDA        fundamentals + technicals + news (no account needed)
/adversarial-research VFV   parallel bull / bear / consensus passes, scored into one thesis
/daily-briefing             (Wealthsimple) portfolio value, concentration flags, this week's earnings, ranked actions
should I add to NVDA?        (Wealthsimple) weighs your holding and a crowding score before answering
```

*Results pull live market data, and your real holdings once Wealthsimple is connected.*

> **Disclaimer.** Analysis is LLM-generated and can be wrong. Verify before acting. Not financial advice.

---

## Why not just ask Claude directly?

Ask a chatbot about your portfolio and it answers from a stale training cutoff, with no idea what you actually hold, making up a P/E or an RSI when it doesn't know one. aifolimizer changes what Claude is working with:

- **Real numbers, computed, not guessed.** Every price, fundamental, technical, and option Greek comes from a live feed or real math, so Claude reasons over facts instead of plausible-sounding fiction.
- **Your actual holdings.** Concentration, Canadian tax (TFSA / RRSP / FHSA), crowding, and rebalancing run on your live Wealthsimple positions, not a screenshot you paste in.
- **A track record, not guesswork.** Tracked skills log every call with entry, stop, and target, then get marked to market nightly. Deflated-Sharpe and calibration gates flag the strategies that are only luck.
- **Private by default.** Only tickers and percentages leave your machine. Balances, account numbers, and your name never do.

Same Claude, now grounded in live data, your real book, and a track record you can audit.

## Who it's for

- **Self-directed investors:** ticker-level research (fundamentals, technicals, earnings, macro, bull/bear theses). Broker-agnostic; no portfolio connection needed.
- **Wealthsimple users:** the full portfolio-aware suite on top: allocation health, concentration warnings, rebalancing, tax-loss harvesting, daily briefings on live holdings.
- **Quant developers:** a working forward-test reference: walk-forward OOS validation, deflated-Sharpe gates, signal-decay curves, regime-conditional weight tuning.
- **MCP integrators:** a real-world tool layout, fallback adapter chain, and PII-filter pattern (103 tools, 15 adapters, 27 skills).

## Features

- **Live brokerage portfolio.** Wealthsimple via the unofficial [`ws-api`](https://github.com/gboudreau/ws-api-python): MFA-aware, every account type (TFSA / RRSP / FHSA / Non-Reg / Crypto). Holdings, cost basis, and cash flow from the real account; cross-account aggregation and tax-aware logic run server-side.
- **103 MCP tools:** live prices, fundamentals, technicals (SMA / RSI / MACD / Bollinger / Minervini stage), FRED macro, crowding, crypto, insider activity, options chains with Greeks, Reddit + StockTwits sentiment, GDELT geopolitics. Verified on Claude Desktop and Claude Code.
- **27 analysis skills:** allocation health, risk, sector rotation, dividends, tax-loss harvesting, pre/post-earnings, macro, and quant anomalies (PEAD, momentum). Auto-trigger on intent, or invoke as slash commands.
- **15 data adapters** behind one base class ([`data_sources/base.py`](backend/app/services/data_sources/base.py)): yfinance, Finnhub, Twelve Data, Tiingo, EODHD, Stooq, Massive, Binance, Kraken, Coinbase, CoinGecko, Frankfurter, open.er-api, Alpha Vantage, plus the Wealthsimple broker. `data_router` chains them with circuit-breaker fallback; adding Polygon or any paid feed is one file.
- **Forward-tested where tracked.** Two skills (`pre-trade-check`, `position-review`) log every call with entry/stop/target; a nightly job marks them to market for 7/30/90-day win rates and alpha vs XEQT/SPY/TSX/QQQ. The other 25 are read-only. Wins and losses both: [TRACK_RECORD.md](TRACK_RECORD.md).
- **Statistical safeguards.** Walk-forward OOS validation, deflated-Sharpe overfitting gate (Bailey & López de Prado 2014), Brier + ECE calibration, signal-decay curves (1 to 63 days), regime-conditional gating, nightly weight tuner.
- **Runs locally.** State in JSONL under `~/.aifolimizer/` and `backend/.claude/context/`; Postgres + Redis optional via `docker compose up -d`.

### Inference & fallback

Inference runs inside an existing Claude Pro session - symbols, weights (% of NLV), returns %, and scores reach Anthropic; no dollar balances, no account IDs. If Claude Pro is unavailable (logged out, subscription lapsed, no API key), scheduled skills fall back to free-tier LLMs (GitHub Models, Gemini, OpenRouter, Qwen) routed through `llm_router.py`. Fallback is opt-in - off unless a provider key is set in `.env`. Output is tagged `[fallback: free-LLM]` and quality is meaningfully lower than Claude on adversarial reasoning. Same redaction rules apply (see [Privacy](#privacy)). Full fallback runbook in [scripts/AUTOMATION.md](scripts/AUTOMATION.md) and [docs/FAQ.md](docs/FAQ.md).

## Architecture

```
Claude Code / Claude Desktop   (Pro subscription)
         ↓ invokes
   .claude/skills/*            (27 analysis skills)
         ↓ calls MCP tools
   backend/mcp_server.py       (FastMCP - 103 tools)
         ↓ uses
   app/services/*              (50+ service modules)
         ↓
   Wealthsimple GraphQL  |  yfinance  |  FRED  |  CoinGecko  |  …
         ↓
   Postgres (TimescaleDB)  +  Redis      (optional, via Docker)
   JSONL state files                     (always - ~/.aifolimizer/, backend/.claude/context/)
```

## Living docs

These three files update as the project runs:

- [TRACK_RECORD.md](TRACK_RECORD.md) - every recommendation, marked to market nightly
- [.claude/context/lessons.md](.claude/context/lessons.md) - corrections from sessions, do-not-repeat rules
- [.claude/context/changes.md](.claude/context/changes.md) - change log

## Status & Roadmap

A single-user local tool today: Wealthsimple as the broker, free delayed data (yfinance / FRED / CoinGecko), quant logic and core services under test. Multi-broker, multi-user (OAuth/SSO), and audit logging are natural next steps; open an issue if you'd use them.

## Good to know

- Advisory only: no auto-trading, you place every trade.
- Wealthsimple access uses the unofficial reverse-engineered `ws-api`; it can break on a WS release and may conflict with their ToS.
- Free, delayed data: FRED macro lags a day or two, and yfinance occasionally throttles TSX symbols (the adapter chain falls back automatically).
- Local by design: run on localhost. Tokens default to a 14-day TTL; MFA re-auth is `python mcp_login.py`.

## Quick start

> The condensed version is below. If you want every step explained (what each setting does, where files go, Windows + macOS commands side by side), follow the **[full setup guide](docs/SETUP.md)** instead.

> Commands below use bash (works on macOS/Linux/WSL/Git-Bash). For native Windows PowerShell equivalents, see [scripts/AUTOMATION.md](scripts/AUTOMATION.md). Replace `<REPO>` with the absolute repo path.

**Prerequisites:** Python 3.12+, Docker Desktop (optional, for Postgres + Redis), Claude Code CLI or Claude Desktop (Pro), Wealthsimple account (optional - required only for portfolio-aware skills).

**Easiest path: install as a Claude Code plugin.** Requires [`uv`](https://docs.astral.sh/uv/getting-started/installation/) on PATH (one binary). No clone, no venv, no manual wiring:

```bash
claude plugin marketplace add tusharagg1/aifolimizer
claude plugin install aifolimizer@aifolimizer
```

The plugin ships all 27 skills and launches the MCP server via `uv run`, which builds the dependency env on first use. The first launch takes ~1-2 min while uv downloads wheels; if the `mcp__aifolimizer__*` tools don't show up immediately, restart Claude once (the env is cached afterward). The market-data tools work out of the box; the Wealthsimple portfolio tools stay dormant until you run `mcp_login.py` (see below). Note: plugin state (paper-trade history etc.) lives in the per-version plugin cache and resets on plugin updates, so for persistent history and the always-on scheduler, use the local clone path below.

**Full local install — one command.** Creates the venv, installs deps, seeds `backend/.env`, writes `.mcp.json` with absolute paths for your machine, registers the MCP server, and runs a health check. Idempotent (won't clobber existing config):

```bash
./setup.sh                                            # macOS / Linux / WSL / Git-Bash
powershell -ExecutionPolicy Bypass -File setup.ps1    # native Windows
```

Then edit `backend/.env` (WS creds), run `mcp_login.py`, and start `run.py` (steps 5-6 below). The manual walkthrough below is the same thing, spelled out; use it if you'd rather do each step yourself or `setup` fails.

```bash
# 1. Postgres password file (required before docker compose up)
mkdir -p .secrets && openssl rand -hex 24 > .secrets/pg_password.txt && chmod 600 .secrets/pg_password.txt

# 2. Start infrastructure (optional - Postgres + Redis)
docker compose up -d

# 3. Backend install
cd backend
python -m venv .venv && source .venv/bin/activate
pip install --upgrade pip && pip install -r requirements.txt
cp ../.env.example .env && $EDITOR .env    # fill WS_EMAIL, WS_PASSWORD

# 4. (Optional) Claude Code permissions / hooks - skip to use defaults (more permission prompts)
cd .. && cp .claude/settings.example.json .claude/settings.json
$EDITOR .claude/settings.json   # replace <REPO_ROOT> and <USER_HOME>; delete the hooks block if you don't run sync_coordinator/obsidian_export
cd backend

# 5. First-time Wealthsimple login (MFA - re-run only when WS forces re-auth)
python mcp_login.py

# 6. Run the backend (http://127.0.0.1:8000)
python run.py
```

Tokens persist to `~/.aifolimizer/ws_session.json` (mode 0600 on POSIX; NTFS-protected on Windows) so backend restarts resume without re-entering OTP. For an always-on service with scheduled-skill execution, see [scripts/AUTOMATION.md](scripts/AUTOMATION.md) (Windows/Task Scheduler today; `launchd` / `systemd` / `cron` snippets in the POSIX appendix).

**Register the MCP server with Claude:**

```bash
claude mcp add aifolimizer "<REPO>/backend/.venv/bin/python" "<REPO>/backend/mcp_server.py"
```

Or copy `.mcp.example.json` → `.mcp.json` and replace `<REPO_ROOT>`. For Claude Desktop, edit `claude_desktop_config.json` (see [docs/FAQ.md](docs/FAQ.md) for OS-specific paths).

Restart Claude, then ask "get my profile" or run `/daily-briefing` to verify live data is flowing.

**Optional:** Telegram alerts (set `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` in `.env`) and scheduled skills - full runbook in [scripts/AUTOMATION.md](scripts/AUTOMATION.md).

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
/momentum-scanner         → 12-month momentum + breakout scan (scheduler-driven)
/pead-tracker             → Post-earnings drift tracker (scheduler-driven)
/position-review          → Open-position health gate (scheduler-driven)
/top-trades-today         → Highest-conviction tradeable ideas (scheduler-driven)
/perf-optimizer           → Rolling weights tuner reflection (scheduler-driven)
```

The five `(scheduler-driven)` skills run on a nightly cadence in `app/jobs/scheduler.py`; they are also invocable on demand. Sample outputs (synthetic data) live under [docs/examples/](docs/examples/).

## MCP tools (103 total - table highlights core 32; full list in `backend/mcp_server.py`)

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
| `list_analysis_modes` | Filesystem-driven list of all 27 skills + their MCP tools | static |

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
│   ├── skills/          # 27 analysis skills
│   ├── context/         # architecture.md, changes.md, lessons.md, STATE.md
│   └── agents/
├── docs/                # FAQ + sample skill outputs
├── scripts/             # AUTOMATION runbook + PowerShell launchers + run-claude-skill.sh
│   └── posix/           # launchd / systemd unit files (macOS / Linux)
├── setup.sh, setup.ps1  # one-command bootstrap (venv, deps, .env, .mcp.json, doctor)
├── .gitattributes       # LF for *.sh / unit files (POSIX shebang safety)
├── docker-compose.yml   # Postgres (TimescaleDB) + Redis
├── .github/             # CI workflows + dependabot
├── CLAUDE.md, AGENTS.md # Project rules / agent context
├── TRACK_RECORD.md      # Live recommendation track record
└── LICENSE
```

## Privacy

`pii_filter.py` strips account IDs, account numbers, internal Wealthsimple IDs, user IDs, email, and full name from the portfolio-bearing tools (`get_profile`, `get_portfolio`, `get_portfolio_analysis`, `get_xray`). Dollar amounts (book cost, market value, cash balance) stay on the machine within the local Claude Pro session. Most other MCP tools (technicals, fundamentals, macro, news, crypto) carry no PII and bypass the filter.

**What leaves the machine:** prompts to Anthropic via the local Claude Pro session - symbols, weights (% of NLV), returns %, scores, and public market data. Optional free-LLM fallbacks (GitHub Models / Gemini / OpenRouter / Qwen) follow the same %-of-NAV redaction rules - never absolute dollar balances, account IDs, email, name, or WS token - and only fire if their key is set in `.env`. Outbound market-data fetches (yfinance / FRED / CoinGecko) see the ticker list, nothing else.

**What never leaves:** Wealthsimple email, password, OTP, access/refresh tokens, account IDs, account numbers, full name. Credentials live in local `backend/.env` (gitignored, never persisted to disk past process memory). Tokens persist to `~/.aifolimizer/ws_session.json` outside the repo with a 14-day default TTL (override via `WS_TOKEN_TTL_HOURS`, range 1-720h) and auto-clear when stale or rejected.

`get_trade_ticket` is a notable exception - it returns raw `dollar_amount_cad` and `max_loss_cad` for the local Claude consumer, so its output stays inside the local session.

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

- [scripts/AUTOMATION.md](scripts/AUTOMATION.md) - scheduled-skill runbook, MFA re-auth, NSSM service, Telegram, troubleshooting (Windows + POSIX appendix)
- [scripts/posix/README.md](scripts/posix/README.md) - ready-to-edit launchd / systemd / cron units for macOS + Linux
- [docs/FAQ.md](docs/FAQ.md) - common setup, privacy, and usage questions
- [docs/examples/](docs/examples/) - synthetic skill outputs + redacted prompt sample
- [SECURITY.md](SECURITY.md) - threat model, disclosure policy, hardening checklist
- [.env.example](.env.example) - every supported env var, with comments
- [CLAUDE.md](CLAUDE.md) / [AGENTS.md](AGENTS.md) - project rules / agent context
- [TRACK_RECORD.md](TRACK_RECORD.md) - live recommendation performance

## Contributing

Issues and PRs welcome. Counts of MCP tools (103) and skills (27) cited in CLAUDE.md / README.md / AGENTS.md / architecture.md are guarded by `python backend/scripts/check_doc_counts.py` - runs in CI after lint, fails the build if a doc claim drifts. Run locally before editing those numbers. `TRACK_RECORD.md` is auto-generated by the `generate_trust_report` MCP tool - refresh by calling the tool rather than editing by hand.

## Troubleshooting

Start with the doctor: `python backend/scripts/health_check.py` reports PASS/WARN/FAIL on Python, MCP import, tool count, services, WS session, and registration. The usual fixes:

- `get my profile` empty or MFA looping? Re-auth with `python mcp_login.py` from `backend/`.
- Port conflict (5432 / 6379 / 8000)? Remap in `docker-compose.yml` or pass `--port`.
- First `/daily-briefing` slow (~30s)? Cold cache; the next call returns in seconds.

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

- [`ws-api`](https://github.com/gboudreau/ws-api-python) - reverse-engineered Wealthsimple client
- [yfinance](https://github.com/ranaroussi/yfinance) - Yahoo Finance market data
- [FRED](https://fred.stlouisfed.org/) - Federal Reserve Economic Data
- [CoinGecko](https://www.coingecko.com/) - Crypto market data
- [Anthropic Claude](https://www.anthropic.com/claude) - analysis engine via MCP
