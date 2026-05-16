---
name: portfolio-health
description: Run a BlackRock-style portfolio health analysis. Use when the user asks about portfolio health, allocation review, rebalancing, or asks "how is my portfolio doing?". Fetches live Wealthsimple data via the aifolimizer MCP server and produces a structured health report.
---

# Portfolio Health Analysis (BlackRock style)

## How to run

1. Call MCP tool `mcp__aifolimizer__get_profile` to learn the user's account types (TFSA, RRSP, etc.)
2. Call MCP tool `mcp__aifolimizer__get_portfolio` to fetch enriched holdings
3. Call MCP tool `mcp__aifolimizer__get_xray` to see true geographic / asset-class exposure (expands ETF holdings)
4. Call MCP tool `mcp__aifolimizer__get_concentration_warnings` to flag over-allocations
5. Run the analysis below using the returned data

## Investor profile

- Age: 32, Canadian resident
- Philosophy: growth stocks, index ETFs, dividends, crypto exposure
- Risk profiles: conservative, moderate, aggressive (across different accounts)
- Time horizons: day trading, short-term <3yr, long-term 10yr+
- Account types and capital: always read from `get_profile` — never hardcode
- Tax: TFSA gains tax-free; RRSP tax-deferred; non-reg has 50% capital gains inclusion

## Output structure

Deliver a BlackRock Portfolio Builder report with these sections:

1. **Portfolio Health Score** (0-100) with one-paragraph rationale
2. **Asset allocation** breakdown vs targets recommended for this investor's age and goals
3. **Top 3 concentration or risk concerns** — name specific tickers/sectors
4. **3-5 actionable rebalancing recommendations** with tickers and reasoning
5. **Canadian tax-efficiency tip** based on actual account types held (use TFSA/RRSP info from `get_profile`)
6. **Expected return range** (annualized) and **maximum drawdown estimate** for current allocation

## Rules

- Keep under 500 words
- Use the actual ticker symbols and weights from the returned data
- Never reference account IDs, names, or PII (MCP returns filtered data only)
- Be direct and specific — no hedging

## Gotchas

- `get_xray` ETF expansion is mapping-based, not live holdings — exotic / new ETFs may fall back to single-asset weight. Flag when unknown.
- `get_concentration_warnings` only fires on threshold breaches; do NOT skip narrative concentration analysis just because the tool returned no warnings.
- Health score is a heuristic, not a backtest — never present it as a return forecast.
- Allocation targets must reference the user's actual age + risk tier from `get_profile`; do not paste a generic 60/40 template.
