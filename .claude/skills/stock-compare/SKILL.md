---
name: stock-compare
description: Run a head-to-head comparison between two tickers (A vs B) for a growth, income, or value investor over a stated horizon. Use when the user asks "X vs Y", "which is better A or B?", "should I pick X or Y?", or wants a side-by-side fundamentals + technicals + valuation matchup. Fetches portfolio context via aifolimizer MCP.
---

# Stock Compare (Head-to-Head)

## Stage 0 — Decision Memory (load BEFORE forming any verdict)

Before fetching market data, load prior decisions on each ticker (A and B) so the verdict stays consistent across sessions:
- `mcp__aifolimizer__get_ticker_decision_history` with `ticker=A` then `ticker=B` (`max_decisions=5`) — prior actions, outcomes, reflections
- `mcp__aifolimizer__get_ticker_reflection` with `symbol=A` / `symbol=B` (`n=3`) — prior recs + realized alpha
- `mcp__aifolimizer__get_cross_ticker_lessons` with `max_lessons=3` — portfolio-level win/loss patterns

Reconciliation rule: if a prior decision exists and your new read flips it, state explicitly WHY it changed (new data / catalyst / price move). Never silently contradict a logged decision — that drift is exactly what this prevents.

## How to run

1. Call `mcp__aifolimizer__get_profile` - account types, capital, tax context. Frame placement recommendation at end (TFSA vs RRSP vs Non-Reg)
2. Identify two tickers from user query (Ticker A, Ticker B). If user names only one and asks "vs sector leader", pick obvious peer and state choice explicitly
3. Call `mcp__aifolimizer__get_portfolio` - flag which tickers user already holds, with current weight and cost basis
4. Call `mcp__aifolimizer__get_fundamentals` with `symbols=[A, B]` in one call - P/E, EPS, dividend yield, payout, market cap, analyst target, beta
5. Call `mcp__aifolimizer__get_technicals` with `symbols=[A, B]` in one call - SMA20/50/200, RSI, MACD, Bollinger Bands, trend, Minervini stage + score, 52-week context
6. Call `mcp__aifolimizer__get_news_headlines` for each ticker - recent catalyst differential
7. WebSearch only for: sector peer multiples, recent analyst upgrade/downgrade asymmetry, or gaps in MCP response

## Investor profile

- Canadian retail investor
- Time horizons: short-term trading + long-term (10yr+) holding
- Strategy lens: confirm with user (growth / income / value); default to growth if unstated
- Account types and capital: always read from `get_profile` - never hardcode

## Output structure

### 1. Verdict (top, one paragraph)
- Winner: A or B (or "tie / context-dependent" with conditions)
- Strategy + horizon assumed (echo back what user asked for)
- One-sentence why

### 2. Side-by-side matrix
Render markdown table. Columns: Metric | Ticker A | Ticker B | Edge.
Rows (minimum):
- Market cap
- Revenue growth (TTM YoY if available, else flag gap)
- Operating margin
- P/E (trailing) and forward P/E
- P/S
- EV/EBITDA (if available)
- Dividend yield and payout ratio
- Beta
- Analyst target (% upside vs current)
- Institutional ownership %
- 52-week position (% from high / % from low)
- Trend (Minervini stage)
- Minervini score (/7)
- RSI (overbought/neutral/oversold)
- MACD signal

### 3. Moat and business model
- One paragraph each - A then B
- Key differentiators
- Where they compete head-to-head, where they don't

### 4. Catalysts and risks (next 12 months)
- A: catalysts, then risks
- B: catalysts, then risks

### 5. Valuation conclusion
- Which is cheaper on absolute multiples
- Which is cheaper relative to own growth (PEG-style, even if rough)
- Sector context

### 6. Technical setup
- Which has better entry RIGHT NOW (or "neither - wait")
- Entry zone, stop-loss, profit target for winner
- If loser still has setup worth noting, mention as fallback

### 7. Recommendation (Canadian tax + holdings aware)
- If user already holds one: hold/add/trim decision
- If user holds neither: which to initiate, sizing as % of portfolio
- Account placement: TFSA / RRSP / Non-Reg with reasoning
  - US dividend payers → RRSP (no 15% withholding)
  - High-growth no-div → TFSA (tax-free gains)
  - Canadian dividend payers → Non-Reg (dividend tax credit) or TFSA

## After output - log decision

For winner ticker (and loser if user holds it), call `mcp__aifolimizer__log_recommendation` with action (BUY/HOLD/SELL/ADD/TRIM/PASS), conviction (HIGH/MED/LOW), entry/target/stop %, 1-line thesis citing the head-to-head edge, `skill_used="stock-compare"`. Feeds forward win-rate / track-record loop.

## Rules

- Under 700 words
- Always render side-by-side matrix - verbal-only comparison not acceptable
- Cite cost basis if user holds either ticker
- For .TO suffix tickers, flag TSX coverage gaps explicitly rather than fabricating
- Never invent metrics - quote from MCP or state "MCP gap; WebSearch confirmed: <source>"

## Gotchas

- `get_fundamentals` cached 6h - both tickers share same staleness window, comparison internally consistent even if absolute numbers lag. Note cache timestamp.
- `get_technicals` cached 1h - Minervini scores and entries stale on high-volatility days. Mention if comparing during earnings week.
- Mismatched fiscal year-ends (e.g. AAPL vs MSFT) - TTM windows differ; flag when revenue growth comparison spans different quarters.
- `analyst_target` upside % is asymmetric noise: $5 stock with $6 target is +20% but lower-conviction than $200 stock with $220 target. Don't rank purely on % upside.
- One US ticker + one .TO ticker: yfinance fundamentals gap on TSX side will skew matrix. Use WebSearch to fill or mark "TSX gap" in cell.
- Both tickers crypto or one is crypto: `get_fundamentals` returns empty - use `get_crypto_data` for crypto leg, adjust matrix rows.
- Dividend yield across borders: 4% US yield in Non-Reg loses 15% withholding → real yield 3.4%. Apply before declaring income winner.
- Beta from yfinance uses 5-year monthly vs S&P 500 - for .TO ticker relevant benchmark is TSX, beta not strictly comparable. Note in row.
