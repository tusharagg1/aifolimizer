---
name: earnings-analyzer
description: Run a JPMorgan-style pre-earnings analysis on a specific ticker. Use when the user asks about an upcoming earnings report, "should I hold through earnings?", "what's the expected move?", or names a stock and earnings in the same query. Fetches portfolio via aifolimizer MCP.
---

# Earnings Analyzer (JPMorgan style)

## Stage 0 — Decision Memory (load BEFORE forming any verdict)

Before fetching market data, load prior decisions on this ticker so the verdict stays consistent across sessions:
- `mcp__aifolimizer__get_ticker_decision_history` with `ticker=TICKER, max_decisions=5` — prior actions, outcomes, reflections
- `mcp__aifolimizer__get_ticker_reflection` with `symbol=TICKER, n=3` — prior recs + realized alpha
- `mcp__aifolimizer__get_cross_ticker_lessons` with `max_lessons=3` — portfolio-level win/loss patterns

Reconciliation rule: if a prior decision exists and your new read flips it, state explicitly WHY it changed (new data / catalyst / price move). Never silently contradict a logged decision — that drift is exactly what this prevents.

## How to run

1. Call `mcp__aifolimizer__get_profile` - account types and capital. Frame position sizing and tax impact of any pre-earnings trade
2. Confirm ticker via `mcp__aifolimizer__get_portfolio` (or use stock with nearest earnings from get_earnings_calendar)
3. Call `mcp__aifolimizer__get_earnings_calendar` - confirmed next earnings date and days until
4. Call `mcp__aifolimizer__get_fundamentals` with `symbols=[ticker]` - EPS TTM, analyst target price, analyst recommendation
5. Call `mcp__aifolimizer__get_recent_filings` with `ticker=ticker, forms=["8-K"]` - pre-earnings 8-K events (guidance preannounce, M&A, exec change) that reshape the setup. US-listed only
6. Call `mcp__aifolimizer__get_finnhub_news` with `ticker=ticker` - news sentiment tally heading into the print (positioning into the event)
7. Call `mcp__aifolimizer__get_earnings_results` with `symbols=[ticker], quarters=4` - authoritative last-4-quarters EPS estimate/actual/surprise/beat-miss outcome. Do NOT WebSearch this; the MCP serves it.
8. WebSearch ONLY for what no MCP tool serves: options-implied move for earnings day (CBOE / broker IV), upcoming-quarter consensus EPS/revenue if absent from `get_fundamentals`, and qualitative management guidance from the last call.

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

Call `mcp__aifolimizer__log_recommendation` with `action` (BUY/HOLD/SELL/ADD/TRIM), `conviction` (HIGH/MED/LOW), `target_pct` + `stop_pct` (% from entry — the schema takes percentages, not absolute prices), `rationale` (1-line thesis citing the play: buy before / trim before / hold through / wait), `skill="earnings-analyzer"`. Feeds forward win-rate / track-record loop.

If the play is BUY/ADD, first call `mcp__aifolimizer__get_positioning_signals` with `symbols=[ticker]`: if `crowding_score >= 70` the name is consensus-crowded into the print (late entry = negative expected alpha) — downgrade conviction or defer the add to post-earnings. This is a lighter check than the swing skills since earnings is an event catalyst, but a crowded pre-print add is exactly the chase to avoid.

## Rules

- Decision summary at very top (one paragraph)
- Full breakdown below
- Under 500 words
- Reference user's actual cost basis from portfolio data to frame recommendation

## Gotchas

- `get_earnings_calendar` from yfinance can show next-FY date instead of next quarter for newly-listed or low-coverage tickers - sanity-check via WebSearch if days_until > 100.
- Options-implied move NOT in MCP - must come from WebSearch (CBOE / OptionStrat / broker IV). Never fabricate IV.
- Last-4-quarters EPS beat/miss comes from `get_earnings_results` (per-quarter estimate/actual/surprise/outcome) - do NOT WebSearch it. `get_fundamentals` carries only the current/next quarter. Only options-implied move and qualitative guidance need WebSearch.
- "Hold through earnings" is account-dependent: in non-reg account, early sell to lock gain triggers capital gains tax - call this out before recommending.
- Don't confuse forward EPS estimate with reported EPS - clearly label estimate vs actual.
