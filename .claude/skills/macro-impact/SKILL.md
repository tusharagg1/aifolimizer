---
name: macro-impact
description: Run a McKinsey-style macro economic impact briefing on the user's portfolio. Use when the user asks about rate impact, inflation, CAD/USD, recession risk, Fed/BoC policy, or how macro affects their holdings.
---

# Macro Impact Analysis (McKinsey style)

## Decision Memory Protocol (load first, log after)

**Before** forming any view, load prior decisions so verdicts stay consistent across sessions:
- `mcp__aifolimizer__get_cross_ticker_lessons` (`max_lessons=3`) — portfolio-level win/loss patterns
- For any name you issue a per-ticker BUY/SELL/TRIM/HOLD/ADD on, also load `mcp__aifolimizer__get_ticker_decision_history` (`ticker=…, max_decisions=5`) and `mcp__aifolimizer__get_ticker_reflection` (`symbol=…, n=3`). If a prior decision exists and this run flips it, state explicitly WHY (new data / catalyst / price); never silently contradict a logged decision.

**After** output, log every actionable verdict: for each BUY/SELL/TRIM/ADD/HOLD issued, call `mcp__aifolimizer__log_recommendation` (`skill="macro-impact", ticker, action, conviction, rationale, target_pct, stop_pct`). Skipping breaks the cross-session feedback loop and causes drift.

## How to run

1. Call `mcp__aifolimizer__get_profile` - actual account types and capital. CAD/USD macro impact matters more for USD-heavy accounts
2. Call `mcp__aifolimizer__get_portfolio` - current holdings
3. Call `mcp__aifolimizer__get_macro_snapshot` - live FRED data (Fed funds, 10Y yield, US/Canada CPI, CAD/USD, BoC rate, unemployment)
4. Call `mcp__aifolimizer__get_boc_snapshot` - authoritative Bank of Canada data (BoC overnight target, USD/CAD, GoC 2/5/10y yields, 10y-2y curve slope). Prefer over FRED's lagged BoC mirror for Canadian rates; cite `curve_signal` (inverted/normal)
5. Call `mcp__aifolimizer__get_statcan_snapshot` - official StatCan CPI YoY inflation + unemployment (use over FRED's Canadian mirror)
6. Call `mcp__aifolimizer__get_factor_snapshot` - which Fama-French style factors (value/size/momentum/quality) lead now; feeds the sector-rotation call in section 8
7. Call `mcp__aifolimizer__get_market_breadth` - VIX, SPY regime (bull/bear vs SMA200), composite market_regime signal
8. WebSearch only if you need details the above don't cover (geopolitics, breaking news)
9. Map each macro factor to specific holdings in portfolio
10. Before issuing any ADD in section 9, call `mcp__aifolimizer__get_positioning_signals` (`symbols=[those names]`) — macro tailwinds alone don't justify adding to a crowded name. Defer ADDs with `crowding_score >= 70` (consensus-crowded, negative expected alpha); favor `crowding_score <= 30` (contrarian edge).
11. Use `market_regime` to calibrate portfolio risk stance (bull_low_fear → risk-on; bear_high_fear → defensive)

## Investor profile

- Canadian retail investor
- Account types and capital: always read from `get_profile` - never hardcode
- Holds equities, ETFs, crypto across registered + non-registered accounts
- Long-term wealth building with some short-term trading

## Output structure

1. **Interest rate environment** (BoC + Fed) - impact on growth vs value holdings
2. **Inflation trend** - which holdings benefit or suffer
3. **CAD/USD outlook** - impact on USD-denominated positions
4. **GDP forecast** (Canada + US) - implications for corporate earnings
5. **Employment + consumer spending** - what they mean for consumer-facing stocks
6. **BoC policy outlook** (next 6 months) - impact on rate-sensitive positions
7. **Global risk factors** - geopolitics, trade, supply chains
8. **Sector rotation** recommendation based on current cycle phase
9. **3 specific portfolio adjustments** - name actual tickers from portfolio
10. **Timeline** - when these factors most likely impact this portfolio

## Rules

- Executive briefing format with action plan table at top
- Under 500 words
- Use current real macro data (search for it), not stale assumptions

## Gotchas

- `get_macro_snapshot` cached 12h - Fed/BoC decision days break this. If user mentions recent rate move, WebSearch to confirm before using MCP data.
- FRED has NO geopolitics, NO earnings, NO breaking news - WebSearch mandatory for those factors, do NOT extrapolate from rates alone.
- `market_regime` is composite (VIX + SPY vs SMA200) - bear_high_fear ≠ recession; state components, not just label.
- CAD/USD impact applies only to USD-denominated holdings; .TO tickers already CAD-quoted - don't double-count FX.
- BoC and Fed rate differentials matter more than absolute levels for CAD/USD direction; cite spread, not just one rate.
- `get_boc_snapshot` / `get_statcan_snapshot` are official Canadian sources — when they disagree with `get_macro_snapshot`'s FRED mirror, trust BoC/StatCan and note the FRED lag.
- `get_factor_snapshot` returns US factors (Ken French) — read factor leadership as a global style signal, not a Canada-specific one.
