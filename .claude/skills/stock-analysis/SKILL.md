---
name: stock-analysis
description: Run a Goldman Sachs + Citadel combined fundamental and technical analysis on a specific ticker in the user's portfolio. Use when the user asks about a specific stock, wants a deep dive, asks for entry/exit points, or asks "should I hold X?". Fetches portfolio context from the aifolimizer MCP server.
---

# Stock Analysis (Goldman Sachs + Citadel)

## How to run

1. Call `mcp__aifolimizer__get_profile` - account types, cash balances, total capital. Frame tax placement recommendation at end
2. Identify ticker user is asking about (or use largest position if unspecified)
3. Call `mcp__aifolimizer__get_portfolio` - confirm ticker is in portfolio + get cost basis and current weight
4. Call `mcp__aifolimizer__get_fundamentals` with `symbols=[ticker]` - P/E, EPS, dividend yield, market cap, analyst target, institutional ownership, beta
5. Call `mcp__aifolimizer__get_technicals` with `symbols=[ticker]` - SMA20/50/200, RSI, MACD, Bollinger Bands, trend signal
6. Call `mcp__aifolimizer__get_news_headlines` with `ticker=ticker` - recent news
7. Call `mcp__aifolimizer__get_positioning_signals` with `symbols=[ticker]` - crowding score, institutional ownership, short interest, headline velocity. Flag "edge already priced" before issuing buy
8. Use MCP data as primary source. WebSearch only for: recent earnings call quotes, analyst upgrade/downgrade news, or gaps in MCP response

## Investor profile

- Age: 32, Canadian investor
- Time horizons: short-term trading + long-term (10yr+) holding
- Account types and capital: always read from `get_profile` - never hardcode

## Output structure

### FUNDAMENTAL (Goldman Sachs)
1. Business model and primary revenue streams
2. Financial health: revenue trend, margins, cash flow (3yr)
3. Competitive moat rating (none/narrow/wide) with reasoning
4. Growth catalysts (next 12 months) and key headwinds
5. Valuation vs sector peers: P/E, P/S, EV/EBITDA
6. Insider trading and institutional ownership trend
7. Bear case + bull case with 12-month price targets
8. Recommendation: Buy / Hold / Sell with entry zone and stop-loss

### TECHNICAL (Citadel)
9. Trend on daily and weekly timeframes
10. Key support/resistance levels - use `pivot_levels.s1/s2` (support) and `pivot_levels.r1/r2` (resistance) from technicals data directly. These are classic floor pivots from the last closed bar. Do NOT invent levels.
11. RSI, MACD, Bollinger Bands - plain English
12. Volume trend - use `volume_score` (current vol / 20d avg). `>1.5` = above-avg conviction, `>2.0` = surge, `<0.5` = low-conviction move. Buyer vs seller dominance
13. Chart pattern (if any)
14. **Minervini stage + score** - `stage` (1=basing, 2=uptrend, 3=distribution, 4=decline), `minervini_score` /7. Score ≥5 = institutional-quality setup
15. **52-week context** - `pct_from_52w_high` and `pct_from_52w_low` from technicals data
16. **Technical composite score** - `technical_score` /1.0 (0.40×Minervini + 0.25×trend + 0.20×RSI position + 0.10×MACD + 0.05×volume). ≥0.65 = strong setup, 0.45-0.65 = mixed, <0.45 = weak
17. Ideal entry: use `pivot_levels.s1` as initial support; stop-loss below `pivot_levels.s2`; profit target at `pivot_levels.r1` (conservative) or `r2` (extended)
18. Risk-to-reward ratio (entry→target / entry→stop). Minimum 2:1 to recommend
19. Confidence rating: Strong Buy / Buy / Neutral / Sell / Strong Sell

### INVESTOR LENSES

Apply the 2 most relevant lenses for this stock type. Skip inapplicable ones - state why in one line.

**Graham (Deep Value):** P/E < 15? Debt/equity < 1? Positive net current assets? 3/3 pass = "Graham would buy at this price." 1-2/3 = note which criteria miss. 0/3 = "Too expensive for value mandate."

**Buffett (Quality Moat):** Moat rating wide/narrow (from fundamental section)? Profit margins stable or expanding over 3yr? ROIC proxy = `profit_margin × revenue / market_cap`-style qualitative read. If wide moat + stable margins: "Buffett-quality compounder - hold forever at right price." Weak moat: "Pass - no durable advantage."

**Lynch (GARP):** Compute PEG = `pe_ratio / (eps_growth_yoy × 100)`. PEG < 1.0 = undervalued grower, 1.0-2.0 = fairly priced growth, > 2.0 = growth already priced in. State PEG explicitly. If `eps_growth_yoy` null, state "PEG unavailable."

**Druckenmiller (Macro Momentum):** Does `get_macro_snapshot` (rates/CPI/CAD-USD) support this sector's tailwind? Does the chart (stage 2 uptrend + volume confirmation) validate the macro thesis? Risk/reward ≥ 3:1 AND macro + chart aligned → "Druck would size up." Misalignment → "Wait for macro confirmation before entering."

Lens selection guide (use as default, override with judgment):
- Dividend / value stock → Graham + Buffett
- Large-cap compounder → Buffett + Lynch
- Growth / tech → Lynch + Druckenmiller
- Macro-sensitive (energy, banks, rates, commodities) → Druckenmiller + Graham

### CROWDING (Goldman / BlackRock 2025 - AI consensus risk)
19. **Crowding score** /100 + label (consensus / neutral / contrarian) from `get_positioning_signals`
20. **Edge-already-priced flag** - if `consensus_flag=True`, downgrade confidence by 1 notch and state "AI/retail consensus already long; late entry has negative expected alpha"
21. **Contrarian opportunity flag** - if `contrarian_flag=True` AND fundamentals + technicals strong, upgrade confidence by 1 notch
22. Headline velocity ratio - `>2.0` = retail attention surge, late-cycle; `<0.5` = forgotten name, potential setup

## Rules

- Under 600 words
- Cite user's actual cost basis from portfolio data to frame recommendation
- For Canadian tickers (.TO suffix), use TSX context

## Gotchas

- `get_fundamentals` cached 6h - `analyst_target` can be stale within trading day; flag if last update >24h.
- `get_technicals` cached 1h - entry zones/stop-loss stale on high-volatility days. Mention timestamp.
- Never invent price target - only quote `analyst_target` from MCP or derive from explicit valuation math you show.
- `minervini_score` requires all 7 sub-criteria - if any field null, score invalid; state "incomplete data".
- `pct_from_52w_high/low` from technicals - use directly, do NOT recompute from price guess.
- For .TO tickers, yfinance fundamentals sparse - institutional ownership and analyst recs often empty. Note "TSX coverage gap" rather than fabricating.
- `crowding_score` uses 4 weighted signals; if 3+ inputs null (common for small caps/TSX), label unreliable - state "positioning data sparse".
- Crowding ≠ overvalued. Consensus name can still grind higher on earnings beats. Flag adjusts conviction, doesn't invert call.
- Headline velocity counts yfinance news only - misses Reddit/X chatter. Underestimates retail surge.
- `pivot_levels` null for symbols with <2 trading days of data (new listings, halted). State "pivot data unavailable" rather than guessing.
- `volume_score` null when volume data missing (common for some TSX ETFs). Do not comment on volume conviction in that case.
- `technical_score` weights are fixed (40/25/20/10/5). Treat as screening signal, not a precise model output.
