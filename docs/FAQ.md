# FAQ

## Why does this exist when OpenBB, FinGPT, and ChatGPT already do this?

The combination is the gap.

OpenBB is a strong financial-data terminal and now ships its own agentic workspace and MCP support. It excels at data aggregation; live brokerage portfolio sync and forward-tested skill recommendations are not its primary focus.

FinGPT is a research-grade platform of finance-domain LLMs (sentiment, NER, forecasting, RAG demos). It produces excellent model-layer output and slots into any agent stack — including this one — at the LLM tier.

ChatGPT (and any general-purpose chat model) does not see actual holdings, cost basis, account types, or cash. It improvises positions from whatever the user pastes.

aifolimizer plugs a live Wealthsimple portfolio into Claude through MCP, runs 21 analysis skills against it, and forward-tests the trade-oriented skills (`pre-trade-check`, `position-review`) by logging every recommendation with entry / stop / target and marking them to market on a nightly schedule. The track record is auditable.

## Do you store my Wealthsimple password?

No.

`WS_PASSWORD` lives in `backend/.env` (local file, gitignored) and loads into process memory only. It never persists to disk, never appears in logs, and never reaches any LLM.

Only the post-auth access and refresh tokens persist, and only to `~/.aifolimizer/ws_session.json` so a backend restart resumes without re-prompting for OTP. Delete that file to force a fresh OTP login.

For portfolio values (book cost, market value, cash balances): those stay on the machine within the local Claude Pro session, where the analysis needs them. The optional free-LLM fallback path constructs %-of-NAV prompts that omit absolute dollars.

## Why does `docker compose up` fail on first run?

Missing `.secrets/pg_password.txt`. Compose mounts it as a Docker secret for Postgres and refuses to start without it. See **README Step 1** for the one-line generator command.

## Where do auth tokens go on disk?

`~/.aifolimizer/ws_session.json`.

On POSIX (Mac/Linux): mode `0600`, owner-only. On Windows: NTFS ACL restricts to the local user account; the chmod call from Python is a no-op, so use NTFS permissions or BitLocker for stronger protection.

The file holds access + refresh tokens with an 8-hour TTL. It auto-clears when stale or rejected. It lives outside the repo and never gets committed. Delete the file to force a fresh OTP login.

## What data leaves my machine when I use an LLM?

Depends on which LLM.

| Provider | What it sees |
|---|---|
| Claude Code / Claude Desktop (Pro) | Tool responses for the three portfolio-bearing tools (`get_profile`, `get_portfolio`, `get_portfolio_analysis`) with PII fields stripped — symbols, weights as % of NLV, returns %, scores, and dollar values. Public-data tools (technicals, fundamentals, macro, news, crypto) carry no PII and pass through directly. |
| Anthropic API (if configured separately) | Same as above |
| Gemini / GitHub Models / OpenRouter / Qwen (fallback) | Hand-built %-of-NAV prompts that omit absolute dollar values. Fallback only fires if the corresponding env var is set. |

What stays on the machine in every case:

- Wealthsimple email, password, and OTP
- Account IDs and account numbers
- Full name and email of the account holder
- Wealthsimple access and refresh tokens

Leave the fallback API keys unset to keep all inference local to Claude Code or Claude Desktop.

## Can I run this on Mac or Linux?

Yes — the backend, MCP server, and skills are cross-platform Python 3.12.

The Windows-only bits are the convenience launchers in `AUTOMATION.md` and `scripts/aifolimizer-launch.ps1`. On Mac or Linux, start the backend and MCP server manually:

```bash
cd backend && source .venv/bin/activate && uvicorn main:app --reload --port 8000
```

Frontend, Docker, Postgres, Redis, yfinance, FRED, and CoinGecko all work identically.

## Why are there no real-time quotes?

Because yfinance is delayed 15+ minutes, and that is intentional.

This is a research and decision tool, not an execution venue. Trades are placed in Wealthsimple itself. The decision lifecycle here (signal generation → adversarial review → log_recommendation → forward score) operates on horizons of hours to weeks, where 15-minute staleness is irrelevant.

For live tick data, this is the wrong project.

## Is this financial advice?

No. This is software that helps reason about a portfolio. It does not constitute investment, tax, or legal advice. Every trade decision belongs to the user. Skills can and do produce wrong recommendations — that is why the trade-oriented skills (`pre-trade-check`, `position-review`) log every recommendation and forward-test it, so the track record is auditable before any output gets trusted.

See the disclaimer in `README.md`.

## How do I add a different brokerage (Questrade, IBKR, Robinhood, etc.)?

Today, brokerage support is Wealthsimple. The integration sits in `backend/app/services/wealthsimple.py` and is referenced directly across the API, MCP, jobs, and scripts (~89 sites).

A `Brokerage` interface that lets Plaid, Schwab, IBKR, and others plug in alongside Wealthsimple is on the roadmap. Forking the Wealthsimple service today and matching the function shapes (positions, cash balances, account metadata) is possible, but expect to update the call sites that import `wealthsimple` directly. PRs that introduce the abstraction cleanly are welcome.

## Why does my first `/daily-briefing` take 30+ seconds?

Cold caches.

The first run hits yfinance, FRED, CoinGecko, and the Wealthsimple API for every holding. Subsequent runs within the cache TTL hit the local diskcache (and Redis if available) and complete in 2–5 seconds.

Cache TTLs are documented in the MCP tool table in `CLAUDE.md` (5m for crypto, 1h for technicals, 6h for fundamentals, 12h for macro).

## Can I use this without a Claude Pro subscription?

Yes, with quality tradeoffs.

Set one of these in `backend/.env`:

```bash
GITHUB_TOKEN=...        # GitHub Models — free tier
GOOGLE_API_KEY=...      # Gemini — free tier
OPENROUTER_API_KEY=...  # mixed free models
```

The fallback chain in `llm_router.py` routes inference to whichever is set. Skill output quality is meaningfully lower than Claude Opus / Sonnet — adversarial reasoning and multi-step synthesis suffer most. Use the fallback for daily briefings and basic screens; do a human review before trusting adversarial-research output.

## How do I uninstall?

```bash
claude mcp remove aifolimizer
docker compose down -v
rm -rf ~/.aifolimizer
```

Then delete the repo. That removes the MCP registration, the Postgres / Redis volumes, the persisted session tokens, and the source. `backend/.env` goes with the repo. Nothing lives in the system Python or shell config.
