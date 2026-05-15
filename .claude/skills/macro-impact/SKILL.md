---
name: macro-impact
description: Run a McKinsey-style macro economic impact briefing on the user's portfolio. Use when the user asks about rate impact, inflation, CAD/USD, recession risk, Fed/BoC policy, or how macro affects their holdings. Fetches portfolio via aifolimizer MCP.
---

# Macro Impact Analysis (McKinsey style)

## How to run

1. Call `mcp__aifolimizer__get_profile` — actual account types and capital. CAD/USD macro impact matters more for USD-heavy accounts
2. Call `mcp__aifolimizer__get_portfolio` for current holdings
3. Call `mcp__aifolimizer__get_macro_snapshot` for live FRED data (Fed funds, 10Y yield, US/Canada CPI, CAD/USD, BoC rate, unemployment)
4. Call `mcp__aifolimizer__get_market_breadth` — VIX, SPY regime (bull/bear vs SMA200), composite market_regime signal
5. Use WebSearch only if you need details FRED doesn't cover (geopolitics, breaking news)
6. Map each macro factor to specific holdings in the portfolio
7. Use `market_regime` to calibrate portfolio risk stance (bull_low_fear → risk-on; bear_high_fear → defensive)

## Investor profile

- Age: 32, Canadian resident
- Account types and capital: always read from `get_profile` — never hardcode
- Holds equities, ETFs, crypto across registered + non-registered accounts
- Long-term wealth building with some short-term trading

## Output structure

1. **Interest rate environment** (BoC + Fed) — impact on growth vs value holdings
2. **Inflation trend** — which holdings benefit or suffer
3. **CAD/USD outlook** — impact on USD-denominated positions
4. **GDP forecast** (Canada + US) — implications for corporate earnings
5. **Employment + consumer spending** — what they mean for consumer-facing stocks
6. **BoC policy outlook** (next 6 months) — impact on rate-sensitive positions
7. **Global risk factors** — geopolitics, trade, supply chains
8. **Sector rotation** recommendation based on current cycle phase
9. **3 specific portfolio adjustments** — name actual tickers from the portfolio
10. **Timeline** — when these factors most likely impact this portfolio

## Rules

- Executive briefing format with action plan table at top
- Under 500 words
- Use current real macro data (search for it), not stale assumptions
