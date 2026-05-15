---
name: sector-rotation
description: Run a Renaissance-style sector rotation and pattern analysis. Use when the user asks about sector trends, "what sectors should I overweight?", money flows, sector leadership, or institutional positioning. Fetches portfolio via aifolimizer MCP.
---

# Sector Rotation Detector (Renaissance + quantitative style)

## How to run

1. Call `mcp__aifolimizer__get_profile` — account types and capital. Rotation trades in TFSA are tax-free; non-reg triggers capital gains
2. Call `mcp__aifolimizer__get_portfolio` to see current sector exposure
3. Call `mcp__aifolimizer__get_xray` to see true sector + geographic exposure after ETF expansion
4. Call `mcp__aifolimizer__get_market_breadth` — VIX, SPY regime (bull/bear vs SMA200). Use `market_regime` to calibrate rotation conviction: bull_low_fear = high conviction rotations; bear_high_fear = defensive only
5. Use WebSearch for: 30-day S&P 500 and TSX sector performance, relative strength rotations, ETF money flows, recent 13F filings (Berkshire, Renaissance, Bridgewater)
6. Identify rotations and translate to actions for the user's portfolio

## Investor profile

- Age: 32, Canadian investor
- Account types and capital: always read from `get_profile` — never hardcode
- Equities, ETFs, crypto exposure
- Wants to spot institutional moves before they're obvious

## Output structure

1. **Current economic cycle phase** (expansion / peak / contraction / trough) and implied sector leadership
2. **Last 30 days:** sectors flipping from negative to positive relative strength
3. **Sectors losing vs gaining momentum** — cite specific data signals
4. **Highest-conviction rotation trade** right now: overweight X, reduce Y
5. **3 ETFs** (Canadian or US listed) to express the rotation — with tickers
6. **Seasonal patterns** — which months favour the rotating-into sectors
7. **Unusual money flows** — signals of institutional accumulation in quiet sectors
8. **Institutional footprint** — sectors with rising 13F ownership
9. **Impact on user's portfolio** — which existing holdings benefit / are at risk
10. **Recommended adjustment** — specific tickers to add/trim

## Rules

- Quantitative research memo format with sector scorecard table
- Under 500 words
- Use live data via WebSearch — sector rotation analysis is time-sensitive
