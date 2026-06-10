---
name: portfolio-health
description: Run a BlackRock-style portfolio health analysis. Use when the user asks about portfolio health, allocation review, rebalancing, or asks "how is my portfolio doing?". Produces a point-in-time health snapshot (scores + flags), NOT a per-name decision table (use portfolio-review for that).
---

# Portfolio Health Analysis (BlackRock style)

## Decision Memory Protocol (load first, log after)

**Before** forming any view, load prior decisions so verdicts stay consistent across sessions:
- `mcp__aifolimizer__get_cross_ticker_lessons` (`max_lessons=3`) — portfolio-level win/loss patterns
- For any name you issue a per-ticker BUY/SELL/TRIM/HOLD/ADD on, also load `mcp__aifolimizer__get_ticker_decision_history` (`ticker=…, max_decisions=5`) and `mcp__aifolimizer__get_ticker_reflection` (`symbol=…, n=3`). If a prior decision exists and this run flips it, state explicitly WHY (new data / catalyst / price); never silently contradict a logged decision.

**After** output, log every actionable verdict: for each BUY/SELL/TRIM/ADD/HOLD issued, call `mcp__aifolimizer__log_recommendation` (`skill="portfolio-health", ticker, action, conviction, rationale, target_pct, stop_pct`). Skipping breaks the cross-session feedback loop and causes drift.

## How to run

1. Call `mcp__aifolimizer__get_profile` - learn user's account types (TFSA, RRSP, etc.)
2. Call `mcp__aifolimizer__get_personal_context` - this report's age-based allocation targets and tax tips need province, marginal_tax_rate_pct, age, and goals, which `get_profile` does not carry. If `present=false`, fall back to generic targets and suggest the user run the `profile-setup` skill.
3. Call `mcp__aifolimizer__get_portfolio` - fetch enriched holdings
4. Call `mcp__aifolimizer__get_xray` - true geographic/asset-class exposure (expands ETF holdings)
5. Call `mcp__aifolimizer__get_concentration_warnings` - flag over-allocations
6. Run analysis below using returned data

## Investor profile

- Canadian retail investor
- Philosophy: growth stocks, index ETFs, dividends, crypto exposure
- Risk profiles: conservative, moderate, aggressive (across different accounts)
- Time horizons: day trading, short-term <3yr, long-term 10yr+
- Account types and capital: always read from `get_profile` - never hardcode
- Tax: TFSA gains tax-free; RRSP tax-deferred; non-reg has 50% capital gains inclusion

## Output structure

BlackRock Portfolio Builder report with these sections:

1. **Portfolio Health Score** (0-100) with one-paragraph rationale
2. **Asset allocation** breakdown vs targets for this investor's age and goals
3. **Top 3 concentration or risk concerns** - name specific tickers/sectors
4. **3-5 actionable rebalancing recommendations** with tickers and reasoning. Before issuing any ADD, call `mcp__aifolimizer__get_positioning_signals` on those names and gate on crowding: defer the ADD if `crowding_score >= 70` (consensus-crowded, negative expected alpha), favor it where `crowding_score <= 30` (contrarian edge) and fundamentals support.
5. **Canadian tax-efficiency tip** based on actual account types from `get_profile`
6. **Expected return range** (annualized) and **maximum drawdown estimate** for current allocation

## Rules

- Under 500 words
- Use actual ticker symbols and weights from returned data
- Never reference account IDs, names, or PII (MCP returns filtered data only)
- Direct and specific - no hedging
- Keep-what-works: before recommending a trim/sell, check whether the position is best LEFT ALONE (large embedded gain in a Non-Reg account, safe growing dividend, core index ETF doing its job). Overtrading erodes long-term returns - the bar to touch a working position is high. For the full leave-alone discipline + whole-book decision table, defer to the `portfolio-review` skill.

## Gotchas

- `get_xray` ETF expansion is mapping-based, not live holdings - exotic/new ETFs may fall back to single-asset weight. Flag when unknown.
- `get_concentration_warnings` only fires on threshold breaches; do NOT skip narrative concentration analysis because tool returned no warnings.
- Health score is heuristic, not backtest - never present as return forecast.
- Allocation targets must reference user's actual age + risk tier from `get_profile`; do not paste generic 60/40 template.
