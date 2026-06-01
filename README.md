# aifolimizer

Local MCP portfolio analysis driven by Claude Desktop or Claude Code, with optional Wealthsimple integration for full portfolio awareness. Exposes 84 MCP tools and 22 analysis skills covering risk, earnings, macro, dividends, tax, technicals, and quant anomalies. Backed by 12 swappable market-data adapters behind a shared interface.

Runs locally on an existing Claude Pro subscription.

[![CI](https://github.com/tusharagg1/aifolimizer/actions/workflows/ci.yml/badge.svg)](https://github.com/tusharagg1/aifolimizer/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-native-purple.svg)](https://modelcontextprotocol.io)
[![Status: alpha](https://img.shields.io/badge/status-alpha-orange)](#status--roadmap)

> **Disclaimer.** Outcomes are LLM-generated. Verify before acting. Not financial advice.

---

## Who it's for

- **Self-directed investors** running ticker-level research - fundamentals, technicals, earnings, macro, adversarial bull/bear theses. Broker-agnostic; no portfolio connection required.
- **Wealthsimple users** wanting the full portfolio-aware suite on top: allocation health, concentration warnings, rebalancing, tax-loss harvesting, sector rotation, daily briefings wired to live holdings. Other brokers slot into the same `Brokerage` abstraction.
- **Quant developers** needing a working forward-test reference: walk-forward OOS validation, deflated-Sharpe overfitting gates, signal-decay curves, regime-conditional weight tuning.
- **MCP integrators**: if you're building MCP servers, the tool layout, fallback adapter chain, and PII filter approach may be worth a look (84 tools, 12 adapters, 22 skills).

## Features

- **Live brokerage portfolio.** Wealthsimple integration via the unofficial [`ws-api`](https://github.com/gboudreau/ws-api-python) (MFA-aware, all account types - TFSA / RRSP / FHSA / Non-Reg / Crypto). Holdings, cost basis, account types, and cash balances flow from the actual account; cross-account aggregation and tax-aware logic run server-side.
- **12 data adapters** (yfinance, Finnhub, Twelve Data, Tiingo, EODHD, Stooq, Binance, CoinGecko, Frankfurter, Alpha Vantage, plus a cross-check adapter and the Wealthsimple broker adapter) share a base class at [`data_sources/base.py`](backend/app/services/data_sources/base.py). The `data_router` chains them with circuit-breaker fallback. Adding Polygon, Refinitiv, or another paid feed is a one-file adapter.
- **84 MCP tools** covering live prices, fundamentals, technicals (SMA / RSI / MACD / Bollinger / Minervini stage), macro from FRED, crowding and positioning, crypto, insider activity, options chains with Greeks, sentiment from Reddit and StockTwits, and geopolitical signals from GDELT. Verified with Claude Desktop and Claude Code; untested with Cursor or other MCP clients but should work.
- **22 analysis skills** covering allocation health, risk, fundamentals, technicals, sector rotation, dividends, tax-loss harvesting, pre/post-earnings, macro, and quant anomalies (PEAD, momentum). Auto-trigger on natural-language intent or invoke directly as slash commands.
- **Forward-tested where it's tracked.** Two of 22 skills (`pre-trade-check`, `position-review`) write every recommendation to `recommendations.jsonl` with entry, stop, and target. A nightly scheduler marks open recommendations to market; rolling 7 / 30 / 90-day win rates surface via `get_live_track_record`. Alpha vs XEQT / SPY / TSX / QQQ comes from `get_alpha_attribution`. The other 20 skills are read-only analysis surfaces and aren't tracked. Live numbers - wins and losses - in [TRACK_RECORD.md](TRACK_RECORD.md).
- **Statistical safeguards.** Walk-forward OOS validation ([`skill_backtest.py`](backend/app/services/skill_backtest.py)), deflated-Sharpe overfitting gate (Bailey & López de Prado 2014), Brier + ECE calibration ([`calibration.py`](backend/app/services/calibration.py)), empirical signal-decay curves at 1/3/5/10/21/42/63 days ([`signal_history.py`](backend/app/services/signal_history.py)), regime-conditional gating, and a nightly weight tuner ([`market_regime.py`](backend/app/services/market_regime.py), [`weights_tuner.py`](backend/app/services/weights_tuner.py)).
- **Runs locally.** Most state lives in JSONL files under `~/.aifolimizer/` and `backend/.claude/context/`. Postgres (TimescaleDB) and Redis are available via `docker compose up -d` for richer history and cross-process caching.

### Inference & fallback

Inference runs inside an existing Claude Pro session - symbols, weights (% of NLV), returns %, and scores reach Anthropic; no dollar balances, no account IDs. If Claude Pro is unavailable (logged out, subscription lapsed, no API key), scheduled skills fall back to free-tier LLMs (GitHub Models, Gemini, OpenRouter, Qwen) routed through `llm_router.py`. Fallback is opt-in - off unless a provider key is set in `.env`. Output is tagged `[fallback: free-LLM]` and quality is meaningfully lower than Claude on adversarial reasoning. Same redaction rules apply (see [Privacy](#privacy)). Full fallback runbook in [scripts/AUTOMATION.md](scripts/AUTOMATION.md) and [docs/FAQ.md](docs/FAQ.md).

## Architecture

```
Claude Code / Claude Desktop   (Pro subscription)
         ↓ invokes
   .claude/skills/*            (22 analysis skills)
         ↓ calls MCP tools
   backend/mcp_server.py       (FastMCP - 84 tools)
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

aifolimizer is a single-user local tool today. Brokerage support is Wealthsimple. Market data flows from yfinance, FRED, and CoinGecko on free, delayed feeds. Tests cover the quant logic and core services; auth and MCP route handlers are exercised manually. Multi-broker support behind a `Brokerage` interface, OAuth/SSO multi-user identity, KMS-backed token storage, and append-only audit logging are all out of scope for the single-user posture today - open as issues / PRs if any of these matter for your use case.

## Limitations

- yfinance throttles intermittently on TSX symbols (~2x a week); the adapter chain falls back but logs noise.
- Wealthsimple tokens default to 14-day TTL (override via `WS_TOKEN_TTL_HOURS`, range 1-720h). MFA re-auth is `python mcp_login.py`.
- The Wealthsimple API is reverse-engineered. Wealthsimple does not officially support automated access; the integration may break on any release and may violate their ToS.
- Macro skill cites FRED data that's 1-3 days stale.
- No auto-trading. Output is suggestions; trades are placed manually.
- Don't expose this server to anything outside localhost until route-handler tests land.

## Quick start

> Commands below use bash (works on macOS/Linux/WSL/Git-Bash). For native Windows PowerShell equivalents, see [scripts/AUTOMATION.md](scripts/AUTOMATION.md). Replace `<REPO>` with the absolute repo path.

**Prerequisites:** Python 3.12+, Docker Desktop (optional, for Postgres + Redis), Claude Code CLI or Claude Desktop (Pro), Wealthsimple account (optional - required only for portfolio-aware skills).

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

## MCP tools (84 total - table highlights core 32; full list in `backend/mcp_server.py`)

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
| `list_analysis_modes` | Filesystem-driven list of all 22 skills + their MCP tools | static |

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
│   ├── skills/          # 22 analysis skills
│   ├── context/         # architecture.md, changes.md, lessons.md, STATE.md
│   └── agents/
├── docs/                # FAQ + sample skill outputs
├── scripts/             # AUTOMATION runbook + PowerShell launchers
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
- [docs/FAQ.md](docs/FAQ.md) - common setup, privacy, and usage questions
- [docs/examples/](docs/examples/) - synthetic skill outputs + redacted prompt sample
- [SECURITY.md](SECURITY.md) - threat model, disclosure policy, hardening checklist
- [.env.example](.env.example) - every supported env var, with comments
- [CLAUDE.md](CLAUDE.md) / [AGENTS.md](AGENTS.md) - project rules / agent context
- [TRACK_RECORD.md](TRACK_RECORD.md) - live recommendation performance

## Contributing

Issues and PRs welcome. Counts of MCP tools (80) and skills (21) cited in CLAUDE.md / README.md / AGENTS.md / architecture.md are guarded by `python backend/scripts/check_doc_counts.py` - runs in CI after lint, fails the build if a doc claim drifts. Run locally before editing those numbers. `TRACK_RECORD.md` is auto-generated by the `generate_trust_report` MCP tool - refresh by calling the tool rather than editing by hand.

## Troubleshooting

- **Port conflicts (5432, 6379, 8000).** Remap in `docker-compose.yml` (e.g. `"5433:5432"`) or change the backend port via `uvicorn main:app --port 8001`.
- **First `/daily-briefing` is slow (~30s).** Cold cache - fundamentals, technicals, and macro all fetch from upstream on the first call. Subsequent calls hit L1 + diskcache and return in <2s.
- **Wealthsimple MFA timeout.** Refresh token expired or WS forced re-auth. Re-run `python mcp_login.py` from `backend/`.
- **Telegram `getUpdates` returns empty.** Telegram exposes `chat.id` only after the bot has received at least one message. Open the bot, send any text, then re-fetch.
- **`claude mcp list` is slow (~5s).** Eager imports in `mcp_server.py` (yfinance, pandas, ta). Harmless - only paid on first invocation per session.

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
