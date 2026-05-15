---
name: earnings-analyzer
description: Run a JPMorgan-style pre-earnings analysis on a specific ticker. Use when the user asks about an upcoming earnings report, "should I hold through earnings?", "what's the expected move?", or names a stock and earnings in the same query. Fetches portfolio via aifolimizer MCP.
---

# Earnings Analyzer (JPMorgan style)

## How to run

1. Call `mcp__aifolimizer__get_profile` — account types and capital. Used to frame position sizing and tax impact of any pre-earnings trade
2. Confirm the ticker via `mcp__aifolimizer__get_portfolio` (or use the stock with nearest earnings from get_earnings_calendar)
3. Call `mcp__aifolimizer__get_earnings_calendar` to get the confirmed next earnings date and days until
4. Call `mcp__aifolimizer__get_fundamentals` with `symbols=[ticker]` — EPS TTM, analyst target price, analyst recommendation
5. Use WebSearch for: last 4 quarters EPS beat/miss history, consensus estimates for upcoming quarter, management guidance from last call, and options-implied move (these require historical data not reliably in yfinance)

## Output structure

1. **Last 4 quarters EPS vs consensus** (beat/miss/meet) with prices reactions
2. **Upcoming quarter consensus** EPS and revenue
3. **Key metrics Wall Street is watching** for this specific company
4. **Segment revenue breakdown** and recent trends
5. **Management guidance summary** from the last call
6. **Historical price reaction** after each of last 4 earnings (% move + direction)
7. **Bull case scenario** — what a beat looks like + price impact estimate
8. **Bear case scenario** — what a miss looks like + downside estimate
9. **Options-implied move** for earnings day (search for recent IV)
10. **Recommended play:** Buy before / Trim before / Hold through / Wait for post-earnings dip

## Rules

- Decision summary at the very top (one paragraph)
- Then full breakdown below
- Under 500 words
- Reference the user's actual cost basis from portfolio data to frame the recommendation
