---
name: earnings-postmortem
description: Run a post-earnings report breakdown on a specific ticker - beat/miss vs consensus, last 4 quarters trend, management guidance shift, and "does the result change my thesis?". Use when the user pastes an earnings report, asks "did X beat?", "what did Y report?", "how did earnings go?", or names a stock with words like "reported", "earnings call", "Q1 results". Fetches portfolio via aifolimizer MCP.
---

# Earnings Postmortem (Post-Report Breakdown)

## How to run

1. Call `mcp__aifolimizer__get_profile` - account types and capital. Frame any post-report trim/add decision and tax impact
2. Confirm ticker via `mcp__aifolimizer__get_portfolio` - current weight + cost basis (or note "not held" if researching watchlist name)
3. Call `mcp__aifolimizer__get_earnings_results` with `symbols=[ticker]`, `quarters=4` - last 4 quarters EPS actual vs estimate, surprise %, beat/meet/miss outcome
4. Call `mcp__aifolimizer__get_fundamentals` with `symbols=[ticker]` - current P/E, forward P/E, EPS TTM, analyst target, recommendation, profit margin, revenue growth
5. Call `mcp__aifolimizer__get_news_headlines` with `ticker=ticker` - post-report news + analyst reactions
6. WebSearch only for: full earnings call transcript quotes, segment revenue breakdown, forward guidance text, options-implied move that already played out, sell-side rating changes post-report

## Investor profile

- Age: 32, Canadian investor
- Time horizons: short-term trading + long-term (10yr+) holding
- Account types and capital: always read from `get_profile` - never hardcode

## Output structure

### 1. Verdict (top, one paragraph)
- Beat / Meet / Miss on EPS (from `get_earnings_results` most recent quarter)
- Stock reaction summary (from news + price move if available)
- Thesis change: confirmed / weakened / broken
- Action: Hold / Add / Trim / Exit - with tax-account framing

### 2. Headline numbers
- This quarter EPS actual vs estimate, surprise %
- Revenue actual vs estimate (from WebSearch if not in MCP - clearly label source)
- YoY growth on revenue and EPS

### 3. Last 4 quarters trend
Render markdown table. Columns: Quarter | EPS Estimate | EPS Actual | Surprise % | Outcome.
Below table: one sentence on pattern (improving beats, deteriorating, choppy).

### 4. Management commentary signal
- Guidance: raised / maintained / lowered (require WebSearch - yfinance doesn't carry guidance)
- Key segment commentary (e.g., cloud growth, ad revenue, subscriber net adds)
- New risks called out by management
- Capital allocation changes (buybacks, dividend, capex)

### 5. Analyst reaction
- Upgrades/downgrades since report (from headlines + WebSearch)
- Price target revisions: pre vs post
- Consensus shift

### 6. Valuation re-rate
- Forward P/E now vs pre-report
- Did multiple expand or compress on print?
- Is current price implying guidance is credible?

### 7. Recommendation (Canadian tax aware)
- If user holds: hold / add / trim / exit with reasoning tied to cost basis
- If user doesn't hold: initiate / wait / pass
- Account placement (TFSA / RRSP / Non-Reg) - same framework as stock-analysis
- For Non-Reg trims, flag capital gains realization explicitly

## Rules

- Decision summary at very top
- Always render 4-quarter trend table - verbal-only not acceptable
- Under 600 words
- Cite cost basis if user holds ticker
- Never invent EPS or revenue figures - quote from `get_earnings_results` or state "WebSearch: <source, date>"
- Forward guidance MUST come from WebSearch (yfinance has no guidance field) - do not fabricate

## Gotchas

- `get_earnings_results` returns yfinance `earnings_history` - EPS only, no revenue. Revenue beats/misses require WebSearch (earnings press release or transcript)
- yfinance `surprisePercent` is decimal (0.05 = 5%) - service already multiplies by 100, but verify sign on negative surprises
- For .TO tickers, yfinance `earnings_history` sparse - many TSX names return empty. Note "TSX coverage gap", rely on WebSearch with company's IR release
- "outcome" field is strict EPS-only beat/miss - company can beat on EPS via buybacks while missing on revenue. Always look at revenue separately
- "Stock reaction" requires post-report price move - `get_technicals` cached 1h, may not reflect fresh print. Note timestamp
- Pre-earnings consensus revisions matter: "beat" against lowered estimate weaker than beat against raised estimate. Flag if WebSearch shows estimates cut in 2 weeks before report
- Guidance shift is dominant signal, not headline beat. Beat with guide-down is sell catalyst; small miss with guide-up is buy
- Crypto holdings: skill inapplicable - redirect to crypto-specific analysis
