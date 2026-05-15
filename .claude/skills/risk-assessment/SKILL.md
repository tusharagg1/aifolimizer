---
name: risk-assessment
description: Run a Bridgewater-style risk assessment on the user's portfolio. Use when the user asks about risk, drawdown, concentration, correlation, stress test, or hedging. Fetches live Wealthsimple data via the aifolimizer MCP server.
---

# Risk Assessment (Bridgewater style)

## How to run

1. Call `mcp__aifolimizer__get_profile` for account context
2. Call `mcp__aifolimizer__get_portfolio` for current holdings
3. Call `mcp__aifolimizer__get_risk_metrics` for vol, Sharpe, Sortino, VaR, expected shortfall
4. Call `mcp__aifolimizer__get_correlation_matrix` to see which positions move together
5. Call `mcp__aifolimizer__get_concentration_warnings` for over-allocation flags
6. Use Ray Dalio's all-weather / radical-transparency framework for the analysis

## Investor profile

- Age: 32, Canadian resident
- Multi-risk-profile investor (conservative, moderate, aggressive buckets)
- Exposure goals: growth stocks, index ETFs, dividends, crypto
- Account types and capital: always read from `get_profile` — never hardcode

## Output structure

1. **Correlation analysis** — which holdings move together (cluster risk)
2. **Sector concentration** — % breakdown, max recommended vs actual
3. **Geographic + currency risk** — CAD vs USD vs international exposure
4. **Interest rate sensitivity** — which positions are most rate-sensitive
5. **Recession stress test** — estimated drawdown in a -30% equity bear market
6. **Liquidity risk rating** per holding (high/medium/low)
7. **Single-asset concentration warnings** — any position >10% of portfolio
8. **Top 3 tail-risk scenarios** with probability estimates
9. **Hedging strategies** to reduce top 2 risks
10. **Rebalancing suggestions** with specific target allocations

## Rules

- Format as a risk management report with a heat-map table at top
- Under 600 words
- Use actual ticker data from MCP — never invent positions
