# aifolimizer - Project Context

## Session Startup (read every new session)
1. Read `.claude/context/changes.md` - what built, when
2. Read `.claude/context/architecture.md` - data flow, API contracts, file index
3. Read `.claude/context/lessons.md` - past corrections, do-not-repeat rules
4. Call `mcp__aifolimizer__get_profile` before analysis - never hardcode account types or capital

## What This Is
AI investment advisor for Canadian Wealthsimple retail investors (growth+income+crypto profile). Live portfolio via local backend. AI analysis in Claude Code/Desktop Pro - no Anthropic API key.

## Architecture

```
Claude Code / Claude Desktop  (Pro subscription - no API key)
        ↓ invokes
   .claude/skills/*  (27 institutional analysis skills)
        ↓ calls MCP tool
   backend/mcp_server.py  (FastMCP, 103 tools)
        ↓ uses
   app/services/{wealthsimple, market_data, fundamentals, technicals, news, macro, quant, portfolio_analytics, health_score, crypto_data}
        ↓ HTTP
   Wealthsimple + yfinance + FRED + CoinGecko (all free, no keys required)
```

No frontend - analysis runs in Claude Code / Claude Desktop. FastAPI is exposed for ad-hoc REST testing only.

## How to Start

Fresh clone: `./setup.sh` (POSIX/Git-Bash) or `powershell -File setup.ps1` (Windows) — venv, deps, `backend/.env`, `.mcp.json`, MCP registration, doctor. Idempotent. Then `python backend/scripts/health_check.py` to diagnose.

```bash
# Backend (FastAPI + shared session store)
cd backend && .venv/Scripts/activate && uvicorn main:app --reload --port 8000
```
MCP server (`mcp_server.py`) runs as separate process managed by Claude Code - register once with:
```
claude mcp add aifolimizer "<venv_python_path>" "backend/mcp_server.py"
```

## MCP Tools (103 total - table below is a curated subset; see `mcp_server.py` for full list)

| Tool | Returns | Cache |
|---|---|---|
| `get_profile` | Account types, cash balances, total invested - PII stripped | session |
| `get_portfolio` | Live enriched positions + summary - PII stripped | live |
| `get_xray` | ETF exposure + sector/asset-class breakdown | live |
| `get_concentration_warnings` | Single-position / sector over-allocation flags | live |
| `get_tax_loss_candidates` | Underwater positions for tax-loss harvesting | live |
| `get_risk_metrics` | Annualized vol, Sharpe, Sortino, VaR 95%, ES, max drawdown | 1h |
| `get_correlation_matrix` | Pairwise correlation top N holdings | 1h |
| `get_macro_snapshot` | FRED: Fed funds, 10Y yield, CPI, CAD/USD, BoC rate, unemployment | 12h |
| `get_fundamentals` | P/E, EPS, div yield, payout, market cap, earnings date, analyst target, beta | 6h |
| `get_technicals` | SMA20/50/200, RSI(14), MACD, Bollinger, trend, RSI signal | 1h |
| `get_earnings_calendar` | Next earnings dates per holding, flags next-14-days | 6h |
| `get_earnings_results` | Last N quarters EPS estimate/actual/surprise/outcome | 12h |
| `get_news_headlines` | Recent headlines per ticker | 30m |
| `get_positioning_signals` | Crowding score, inst. ownership, short interest, headline velocity | 6h |
| `snapshot_positioning_history` | Append crowding scores to JSONL (idempotent/day) | live |
| `get_crowding_shifts` | Symbols w/ crowding score shift ≥threshold over lookback | live |
| `get_crypto_data` | CoinGecko: price CAD, market cap, 24h/7d/30d, ATH distance | 5m |
| `get_triggered_alerts` | Alert events from jsonl log | live |
| `run_alerts_now` | Evaluate alert rules vs live portfolio | live |
| `backtest_portfolio` | Rule-replay per symbol. Supports `tx_cost_bps` + `walk_forward` | 1h |
| `get_skill_track_record` | Backtest 13 codified-rule skills over 3-5yr bars. CAGR, Sharpe, Sortino, max DD, hit-rate, alpha | disk |
| `log_recommendation` | Log skill rec to recommendations.jsonl | live |
| `score_recommendations` | Mark-to-market open recs, flag stops/targets hit | live |
| `get_live_track_record` | Rolling 7/30/90d win-rate + P&L from scored recs | live |
| `snapshot_portfolio_equity` | Append NAV to portfolio_history.jsonl (idempotent/day) | live |
| `get_alpha_attribution` | Alpha, beta, Sharpe, info ratio, tracking error vs benchmarks | live |
| `get_quote_with_source` | Live quote w/ source attribution (fallback chain) | 5m |
| `get_quotes_batch` | Batch quotes for N symbols - 13x faster than serial | 5m |
| `get_data_source_reliability` | Per-source success rate + avg latency | live |
| `generate_trust_report` | Write TRACK_RECORD.md + jsonl, git-commit | live |
| `list_analysis_modes` | Filesystem-driven list of all 27 skills + their MCP tools | static |

L1+L2: in-process dict + cross-process diskcache. MCP+FastAPI share L2.

## Analysis Skills (27 in `.claude/skills/` - table below highlights core 13)

| Skill | Framework | Key MCP tools |
|---|---|---|
| `daily-briefing` | Morning digest | get_profile, get_portfolio, get_macro_snapshot, get_concentration_warnings, get_triggered_alerts, get_earnings_calendar, get_positioning_signals |
| `portfolio-health` | BlackRock | get_profile, get_portfolio, get_xray, get_concentration_warnings |
| `risk-assessment` | Bridgewater | get_profile, get_portfolio, get_risk_metrics, get_correlation_matrix |
| `stock-analysis` | Goldman + Citadel TA | get_profile, get_portfolio, get_fundamentals, get_technicals, get_news_headlines, get_positioning_signals |
| `stock-compare` | Head-to-head | get_profile, get_portfolio, get_fundamentals, get_technicals, get_news_headlines |
| `macro-impact` | McKinsey | get_profile, get_portfolio, get_macro_snapshot |
| `dividend-strategy` | Harvard Endowment | get_profile, get_portfolio, get_fundamentals |
| `earnings-analyzer` | JPMorgan | get_profile, get_portfolio, get_earnings_calendar, get_fundamentals |
| `earnings-postmortem` | Post-report EPS | get_profile, get_portfolio, get_earnings_results, get_fundamentals, get_news_headlines |
| `sector-rotation` | Renaissance | get_profile, get_portfolio, get_xray |
| `tax-loss-review` | Canadian harvesting | get_profile, get_tax_loss_candidates |
| `adversarial-research` | Bull/bear/consensus | get_profile, get_portfolio, get_fundamentals, get_technicals, get_news_headlines, get_macro_snapshot, get_positioning_signals |
| `cash-deployment` | Add-to-winners w/ crowding guard | get_profile, get_portfolio, get_concentration_warnings, get_fundamentals, get_technicals, get_positioning_signals |

Each skill: auto-triggers from frontmatter, calls get_profile FIRST.

## Investor Profile (verify with get_profile)

- Canadian retail investor
- Philosophy: growth stocks, index ETFs (XEQT/VFV), dividends, crypto
- Risk: mixed - conservative (bonds/GIC), moderate (ETFs), aggressive (stocks, crypto)
- Horizons: day trading + short-term (<3yr) + long-term (10yr+)
- Tax: TFSA (tax-free), RRSP (tax-deferred), Non-Reg (50% cap gains inclusion)
- **Capital + balances: ALWAYS pull from `get_profile` - never hardcode**
- **Crowding**: before adding to name, call `get_positioning_signals`. Score ≥70 = consensus-crowded → negative expected alpha. Defer adds; favor contrarian (score ≤30) when fundamentals support.

## Tech Stack

| Layer | Tech |
|---|---|
| Backend API | FastAPI + uvicorn (Python 3.12) |
| MCP server | FastMCP (shares services with FastAPI) |
| Technical indicators | `ta>=0.11.0` (NOT pandas-ta - incompatible w/ Python 3.14) |
| Prices + fundamentals | yfinance (free, no key) |
| Macro data | FRED public CSV API (free, no key) |
| Crypto data | CoinGecko v3 free API (no key, 30 req/min) |
| AI inference | Claude Code / Claude Desktop Pro (no Anthropic API key) |

Supports Python 3.12+; pinned <3.14 due to pandas-ta lineage.

## Privacy Rules (NON-NEGOTIABLE)

<important if="touching backend/, .env, mcp_server.py, pii_filter.py, or any MCP tool response">
- WS_EMAIL/WS_PASSWORD: local `.env` only, never committed, logged, or sent to AI. Password NEVER persisted to disk.
- WS access + refresh token: server RAM + persisted to `~/.aifolimizer/ws_session.json` (mode 0600 on POSIX; on Windows the file lives in the user profile and relies on NTFS ACL - set BitLocker / strict ACL if hardening). 14-day default TTL (override via `WS_TOKEN_TTL_HOURS`, range 1-720h); auto-cleared when stale/rejected. Delete to force re-auth. Lives outside repo - never committed.
- `pii_filter.py` is applied to portfolio/profile/x-ray responses (the tools that surface account-bearing payloads from `wealthsimple.py`). Other tools (technicals, fundamentals, macro, news, etc.) operate on public market data + symbol lists and do not handle PII. When adding a new MCP tool that touches WS account data, route the response through `pii_filter` before returning.
- Account IDs, account numbers, email, full name: NEVER leave the local machine.
- External LLM fallbacks (GitHub Models / Gemini / OpenRouter / Qwen) fire ONLY if their API key env var is set. Prompts carry symbols, weights (% of NLV), returns %, and scores - NEVER absolute dollar balances, account IDs, email, name, or WS token. Leave keys unset to keep all fallback inference on-machine.
- Primary inference path is your Claude Code / Claude Desktop Pro session, which sends prompts (symbols, weights, scores, public market data) to Anthropic per their normal ToS. No separate API key, no third-party LLM, no creds, no PII.
</important>

## Environment Variables

```bash
# backend/.env (local only - never commit)
WS_EMAIL=...
WS_PASSWORD=...
# Optional: Telegram alerts, free-LLM fallback keys, Sentry - see .env.example
```

## Code Rules

- No hardcoded capital amounts or account types - always read from `get_profile`
- No PII in logs, DB, or MCP output
- **Activate PII pre-commit hook once per clone:** `git config core.hooksPath .githooks`. Hook blocks owner-identifying strings (gmail, full-name, legacy IDs); allowlist for LICENSE/README/pyproject.toml. CI runs `gitleaks` with same rules from `.gitleaks.toml`.
- Functions short, single-purpose
- No comments explaining WHAT - only WHY when non-obvious
- Append to `.claude/context/changes.md` after significant changes
- Skills builder: `python backend/scripts/build_skills.py` lists tools + skills
- Scaffold skill: `python backend/scripts/build_skills.py --scaffold <tool_name>`

## Workflow Rules

- **Verify before "done."** Compile-clean ≠ working. Run import-check (backend) AND exercise w/ real input.
- **Lessons loop.** After correction or surprise bug, append rule to `.claude/context/lessons.md`.
- **Pause for elegance on non-trivial changes** (3+ files or new abstraction). Ask "cleaner path?" before commit.
- **Surgical changes only.** Touch only what request requires. Match existing style. Remove only imports/vars your changes made unused.
- **Targeted reads only.** Use `semantic_search_nodes` or `get_review_context` before Grep/Read. Read only relevant lines. Ask before loading large files, logs, generated files, or full dependency trees.
- **Summarize tool output.** Never paste full logs. Extract relevant; drop rest.
- **Final answer format:** changes made + verification + blockers only.

## Commit Rules

- NEVER add `Co-Authored-By: Claude ...` trailer to commits
- NEVER add "Generated with Claude Code" footer or AI-attribution to commits, PRs, or PR bodies
- Commit messages authored solely by human user
