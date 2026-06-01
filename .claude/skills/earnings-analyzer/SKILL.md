---
name: earnings-analyzer
description: Run a JPMorgan-style pre-earnings analysis on a specific ticker. Use when the user asks about an upcoming earnings report, "should I hold through earnings?", "what's the expected move?", or names a stock and earnings in the same query. Fetches portfolio via aifolimizer MCP.
---

# Earnings Analyzer (JPMorgan style)

## How to run

1. Call `mcp__aifolimizer__get_profile` - account types and capital. Frame position sizing and tax impact of any pre-earnings trade
2. Confirm ticker via `mcp__aifolimizer__get_portfolio` (or use stock with nearest earnings from get_earnings_calendar)
3. Call `mcp__aifolimizer__get_earnings_calendar` - confirmed next earnings date and days until
4. Call `mcp__aifolimizer__get_fundamentals` with `symbols=[ticker]` - EPS TTM, analyst target price, analyst recommendation
5. WebSearch for: last 4 quarters EPS beat/miss history, consensus estimates for upcoming quarter, management guidance from last call, options-implied move (require historical data not reliably in yfinance)

## Output structure

1. **Last 4 quarters EPS vs consensus** (beat/miss/meet) with price reactions
2. **Upcoming quarter consensus** EPS and revenue
3. **Key metrics Wall Street is watching** for this specific company
4. **Segment revenue breakdown** and recent trends
5. **Management guidance summary** from last call
6. **Historical price reaction** after each of last 4 earnings (% move + direction)
7. **Bull case scenario** - what beat looks like + price impact estimate
8. **Bear case scenario** - what miss looks like + downside estimate
9. **Options-implied move** for earnings day (search for recent IV)
10. **Recommended play:** Buy before / Trim before / Hold through / Wait for post-earnings dip

## After output - log decision

Call `mcp__aifolimizer__log_recommendation` with action (BUY/HOLD/SELL/ADD/TRIM), conviction (HIGH/MED/LOW), entry/target/stop %, 1-line thesis citing the play (buy before / trim before / hold through / wait), `skill_used="earnings-analyzer"`. Feeds forward win-rate / track-record loop.

## Rules

- Decision summary at very top (one paragraph)
- Full breakdown below
- Under 500 words
- Reference user's actual cost basis from portfolio data to frame recommendation

## Gotchas

- `get_earnings_calendar` from yfinance can show next-FY date instead of next quarter for newly-listed or low-coverage tickers - sanity-check via WebSearch if days_until > 100.
- Options-implied move NOT in MCP - must come from WebSearch (CBOE / OptionStrat / broker IV). Never fabricate IV.
- Historical EPS beat/miss from `get_fundamentals` is current quarter only - last 4 quarters require WebSearch.
- "Hold through earnings" is account-dependent: in non-reg account, early sell to lock gain triggers capital gains tax - call this out before recommending.
- Don't confuse forward EPS estimate with reported EPS - clearly label estimate vs actual.
