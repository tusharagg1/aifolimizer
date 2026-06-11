---
name: sector-rotation
description: Run a Renaissance-style sector rotation and pattern analysis. Use when the user asks about sector trends, "what sectors should I overweight?", money flows, sector leadership, or institutional positioning.
---

# Sector Rotation Detector (Renaissance + quantitative style)

## Decision Memory Protocol (load first, log after)

**Before** forming any view, load prior decisions so verdicts stay consistent across sessions:
- `mcp__aifolimizer__get_cross_ticker_lessons` (`max_lessons=3`) - portfolio-level win/loss patterns
- For any name you issue a per-ticker BUY/SELL/TRIM/HOLD/ADD on, also load `mcp__aifolimizer__get_ticker_decision_history` (`ticker=…, max_decisions=5`) and `mcp__aifolimizer__get_ticker_reflection` (`symbol=…, n=3`). If a prior decision exists and this run flips it, state explicitly WHY (new data / catalyst / price); never silently contradict a logged decision.

**After** output, log every actionable verdict: for each BUY/SELL/TRIM/ADD/HOLD issued, call `mcp__aifolimizer__log_recommendation` (`skill="sector-rotation", ticker, action, conviction, rationale, target_pct, stop_pct`). Skipping breaks the cross-session feedback loop and causes drift.

## How to run

1. Call `mcp__aifolimizer__get_profile` - account types and capital. Rotation trades in TFSA tax-free; non-reg triggers capital gains
2. Call `mcp__aifolimizer__get_portfolio` - current sector exposure
3. Call `mcp__aifolimizer__get_xray` - true sector + geographic exposure after ETF expansion
4. Call `mcp__aifolimizer__get_market_breadth` - VIX, SPY regime (bull/bear vs SMA200). Use `market_regime` to calibrate rotation conviction: bull_low_fear = high conviction; bear_high_fear = defensive only
5. Call `mcp__aifolimizer__get_factor_snapshot` - Fama-French factor leadership. Factor rotation drives sector rotation: value leading → financials/energy/industrials; growth/momentum leading → tech/discretionary; quality (RMW) leading → defensives/staples
6. WebSearch for: 30-day S&P 500 and TSX sector performance, relative strength rotations, ETF money flows, recent 13F filings (Berkshire, Renaissance, Bridgewater)
7. Identify rotations and translate to actions for user's portfolio

## Investor profile

- Canadian retail investor
- Account types and capital: always read from `get_profile` - never hardcode
- Equities, ETFs, crypto exposure
- Wants to spot institutional moves before they're obvious

## Output structure

1. **Current economic cycle phase** (expansion / peak / contraction / trough) and implied sector leadership
2. **Last 30 days:** sectors flipping from negative to positive relative strength
3. **Sectors losing vs gaining momentum** - cite specific data signals
4. **Highest-conviction rotation trade** right now: overweight X, reduce Y
5. **3 ETFs** (Canadian or US listed) to express rotation - with tickers
6. **Seasonal patterns** - which months favour rotating-into sectors
7. **Unusual money flows** - signals of institutional accumulation in quiet sectors
8. **Institutional footprint** - sectors with rising 13F ownership
9. **Impact on user's portfolio** - which existing holdings benefit/at risk
10. **Crowding gate on add candidates** - before naming anything to add, call `mcp__aifolimizer__get_positioning_signals` (`symbols=[add candidates]`). Defer adds where `crowding_score >= 70` (consensus-crowded → negative expected alpha); favor names `<= 30` (contrarian edge)
11. **Recommended adjustment** - specific tickers to add/trim

## Rules

- Quantitative research memo format with sector scorecard table
- Under 500 words
- Use live data via WebSearch - sector rotation is time-sensitive

## Gotchas

- `get_xray` ETF expansion is static sector mapping - not live ETF holdings. Use for "true sector tilt" approximation, NOT precise weight claims.
- `get_market_breadth` cached - verify with WebSearch on volatile days before sizing rotation trade.
- 13F filings lag 45 days - never present 13F-derived positioning as "current".
- Rotation trades in non-reg trigger capital gains; in TFSA tax-free - always pair recommendation with right account.
- Seasonal patterns are statistical priors, NOT predictions - present as base rate, not directive.
- Don't confuse sector ETF performance with underlying sector - track index, not just ETF, when comparing.
- `get_factor_snapshot` trailing-252d shows the established regime; latest-daily is noise. Cite the trailing window for rotation direction, not one day.
