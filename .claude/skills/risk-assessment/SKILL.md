---
name: risk-assessment
description: Run a Bridgewater-style risk assessment on the user's portfolio. Use when the user asks about risk, drawdown, concentration, correlation, stress test, or hedging. Fetches live Wealthsimple data via the aifolimizer MCP server.
---

# Risk Assessment (Bridgewater style)

## Decision Memory Protocol (load first, log after)

**Before** forming any view, load prior decisions so verdicts stay consistent across sessions:
- `mcp__aifolimizer__get_cross_ticker_lessons` (`max_lessons=3`) — portfolio-level win/loss patterns
- For any name you issue a per-ticker BUY/SELL/TRIM/HOLD/ADD on, also load `mcp__aifolimizer__get_ticker_decision_history` (`ticker=…, max_decisions=5`) and `mcp__aifolimizer__get_ticker_reflection` (`symbol=…, n=3`). If a prior decision exists and this run flips it, state explicitly WHY (new data / catalyst / price); never silently contradict a logged decision.

**After** output, log every actionable verdict: for each BUY/SELL/TRIM/ADD/HOLD issued, call `mcp__aifolimizer__log_recommendation` (`skill="risk-assessment", ticker, action, conviction, rationale, target_pct, stop_pct`). Skipping breaks the cross-session feedback loop and causes drift.

## How to run

1. Call `mcp__aifolimizer__get_profile` - account context
2. Call `mcp__aifolimizer__get_portfolio` - current holdings
3. Call `mcp__aifolimizer__get_risk_metrics` - vol, Sharpe, Sortino, VaR, expected shortfall
4. Call `mcp__aifolimizer__get_correlation_matrix` - which positions move together
5. Call `mcp__aifolimizer__get_concentration_warnings` - over-allocation flags
6. Call `mcp__aifolimizer__get_factor_exposure` for the top 3-5 holdings by weight - multi-factor betas (market/size/value/profitability/investment/momentum) + annualized alpha. Surfaces hidden FACTOR concentration that name/sector diversification hides (e.g. 4 different names all loaded on momentum = one factor bet)
7. Call `mcp__aifolimizer__get_factor_snapshot` - current factor regime. A book heavy on a factor that is rolling over is a live risk even if the names look uncorrelated
8. Use Ray Dalio's all-weather/radical-transparency framework

## Investor profile

- Canadian retail investor
- Multi-risk-profile investor (conservative, moderate, aggressive buckets)
- Exposure goals: growth stocks, index ETFs, dividends, crypto
- Account types and capital: always read from `get_profile` - never hardcode

## Output structure

1. **Correlation analysis** - holdings moving together (cluster risk)
2. **Sector concentration** - % breakdown, max recommended vs actual
3. **Geographic + currency risk** - CAD vs USD vs international exposure
4. **Interest rate sensitivity** - most rate-sensitive positions
5. **Recession stress test** - estimated drawdown in -30% equity bear market
6. **Liquidity risk rating** per holding (high/medium/low)
7. **Single-asset concentration warnings** - any position >10% of portfolio
8. **Factor concentration** - shared factor loadings across holdings (from `get_factor_exposure`); flag if 3+ names share a dominant factor beta
9. **Top 3 tail-risk scenarios** with probability estimates
10. **Hedging strategies** to reduce top 2 risks
11. **Rebalancing suggestions** with specific target allocations

## Rules

- Format as risk management report with heat-map table at top
- Under 600 words
- Use actual ticker data from MCP - never invent positions

## Gotchas

- `get_risk_metrics` cached 1h - state as-of timestamp; never claim "real-time" risk.
- `get_correlation_matrix` covers top-N holdings only; small positions excluded - do NOT claim full-portfolio correlation.
- VaR/ES are historical-distribution estimates - fail in regime changes. State this when stress-testing.
- Sharpe/Sortino use yfinance daily closes - illiquid tickers (e.g. some .TO microcaps) produce unreliable annualized vol. Flag tickers with <60 days of returns as low-confidence.
- Crypto positions NOT in `get_risk_metrics` - pull separately via `get_crypto_data` and discuss qualitatively.
- `get_factor_exposure` uses US Fama-French factors - loadings for .TO/non-US names are directional only; low R² (<0.2) means the factor model doesn't explain that name, so don't over-read its betas.
