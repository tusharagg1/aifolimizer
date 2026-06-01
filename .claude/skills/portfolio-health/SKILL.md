---
name: portfolio-health
description: Run a BlackRock-style portfolio health analysis. Use when the user asks about portfolio health, allocation review, rebalancing, or asks "how is my portfolio doing?". Fetches live Wealthsimple data via the aifolimizer MCP server and produces a structured health report.
---

# Portfolio Health Analysis (BlackRock style)

## How to run

1. Call `mcp__aifolimizer__get_profile` - learn user's account types (TFSA, RRSP, etc.)
2. Call `mcp__aifolimizer__get_portfolio` - fetch enriched holdings
3. Call `mcp__aifolimizer__get_xray` - true geographic/asset-class exposure (expands ETF holdings)
4. Call `mcp__aifolimizer__get_concentration_warnings` - flag over-allocations
5. Run analysis below using returned data

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
4. **3-5 actionable rebalancing recommendations** with tickers and reasoning
5. **Canadian tax-efficiency tip** based on actual account types from `get_profile`
6. **Expected return range** (annualized) and **maximum drawdown estimate** for current allocation

## Rules

- Under 500 words
- Use actual ticker symbols and weights from returned data
- Never reference account IDs, names, or PII (MCP returns filtered data only)
- Direct and specific - no hedging

## Gotchas

- `get_xray` ETF expansion is mapping-based, not live holdings - exotic/new ETFs may fall back to single-asset weight. Flag when unknown.
- `get_concentration_warnings` only fires on threshold breaches; do NOT skip narrative concentration analysis because tool returned no warnings.
- Health score is heuristic, not backtest - never present as return forecast.
- Allocation targets must reference user's actual age + risk tier from `get_profile`; do not paste generic 60/40 template.
