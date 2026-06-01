First public tag of aifolimizer. Alpha software, single-user, no warranty - read the disclaimers below before pointing it at a real account.

## What is in the box

- **84 MCP tools** exposed via FastMCP, organized by service (Wealthsimple session, market data, fundamentals, technicals, news, macro, quant, portfolio analytics, health score, crypto data, alerts, recommendations).
- **21 analysis skills** under `.claude/skills/` covering portfolio health, risk, adversarial research, earnings (pre + post), macro impact, dividend strategy, sector rotation, tax-loss review, cash deployment, daily briefing, position review, pre-trade check, momentum scanner, PEAD tracker, top trades today, weekly mirror and more.
- **12 free data adapters** with no required API keys: yfinance, FRED, CoinGecko, Finnhub, Tiingo, Twelve Data, EODHD, Stooq, Binance, Frankfurter, Alpha Vantage, plus the Wealthsimple unofficial API (via `gboudreau/ws-api`).
- **Validation infrastructure**: walk-forward OOS backtesting per skill, deflated-Sharpe gate, Brier score + ECE calibration tracking, per-source data reliability metrics, and a `generate_trust_report` tool that writes a public TRACK_RECORD.md.
- **MCP-native** - runs locally in Claude Code or Claude Desktop Pro. No Anthropic API key needed; no cloud LLM call is required for analysis.

## Status

**Alpha. Single-user.** Built for one Canadian retail account. APIs, schemas, skill prompts, and tool names will change without deprecation windows. Pin to a commit if you build on it.

## Important caveats

- **Wealthsimple ToS risk** - the WS integration uses the unofficial `ws-api` library, which talks to the same private GraphQL endpoint as the WS web app. WS does not publish a public API and does not endorse third-party clients. Use at your own risk; your account could be flagged or rate-limited. Email + password live in a local `.env`; access/refresh tokens persist to `~/.aifolimizer/ws_session.json`.
- **Privacy posture** - all WS account data (account IDs, balances, names, email) stays on your machine. The PII filter strips identifying fields from MCP responses before they reach Claude. Optional external-LLM fallbacks (GitHub Models / Gemini / OpenRouter / Qwen) are off unless you set their env vars; even when on, prompts carry only symbols, weights, and scores - never absolute dollars or account IDs.
- **Not investment advice.** Backtests are historical replays of codified rules and do not include slippage, halts, dividend timing, or tax effects beyond a flat `tx_cost_bps`. Skill outputs are research aides; you make the trades.
- **Limited universe** - tested primarily on TSX + US large-cap equities, ETFs, and the top ~20 crypto assets. Options-screen tools (covered call / protective put) use yfinance chains and inherit its quote staleness.

## Validation methodology

- `walk_forward_backtest_skill` runs each codified-rule skill across the unbiased 40+ symbol basket with 252-day windows, 63-day step, and reports per-window stability, regime split (bull/bear/sideways), and a deflated Sharpe per Bailey & Lopez de Prado.
- `get_calibration_report` tracks Brier score and ECE on the integrated buy/sell signal so you can tell if predicted win-probabilities match realized outcomes.
- `get_skill_track_record` compares each codified skill against SPY and XEQT buy-and-hold for both a holdings-only and a broad-basket label.
- LLM-driven skills (adversarial-research, earnings-postmortem, stock-compare, weekly-mirror, momentum-scanner) are explicitly **not** rule-replayable and are excluded from the backtest claims.

## Install

See the [README](https://github.com/tusharagg1/aifolimizer#readme) for full setup. Short version on Windows:

```
git clone https://github.com/tusharagg1/aifolimizer
cd aifolimizer\backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env   # fill in WS_EMAIL / WS_PASSWORD
claude mcp add aifolimizer "<repo-root>\backend\.venv\Scripts\python.exe" "backend\mcp_server.py"
```

## Known limitations

- Windows-first; macOS/Linux paths work but are less exercised.
- WS session file relies on filesystem ACLs - on Windows, BitLocker / strict NTFS perms recommended.
- Some MCP tools (options chains, Reddit/StockTwits sentiment) hit free public endpoints with aggressive caching but no SLA.
- Postgres + Redis (via docker-compose) bind to `0.0.0.0` by default - rebind to `127.0.0.1` if your dev box is on an untrusted LAN.

## Acknowledgements

- [`gboudreau/ws-api`](https://github.com/gboudreau/ws-api) - the unofficial Wealthsimple API client this project depends on. Without it the WS integration would not exist.
- [FastMCP](https://github.com/jlowin/fastmcp) - MCP server scaffolding.
- yfinance, FRED, CoinGecko, Finnhub, Tiingo, Twelve Data, EODHD, Stooq, Binance, Frankfurter, Alpha Vantage - free public market data.
- Anthropic Claude Code / Claude Desktop - the analysis runtime.

MIT licensed. No warranty. Not affiliated with Wealthsimple, Anthropic, or any data provider.
