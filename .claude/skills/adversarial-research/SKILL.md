---
name: adversarial-research
description: Run a parallel bull/bear adversarial research pipeline on a specific ticker. Use when the user asks for "adversarial research", "bull bear analysis", "deep research on [ticker]", "should I buy X?", or wants a rigorous debate-style investment thesis. Fetches live data via aifolimizer MCP then spawns parallel sub-agents.
---

# Adversarial Research Pipeline (Stage 1-5)

Modelled on a multi-agent hedge fund workflow. Two sub-agents argue opposing sides simultaneously. You synthesize into probability-weighted scenarios.

## How to run

### Stage 0 — Profile

Call `mcp__aifolimizer__get_profile` first — account types, capital, and cash available. Used to tailor Canadian tax placement in Stage 4.

### Stage 1 — Data collection (call all 5 MCP tools in parallel)

1. Call `mcp__aifolimizer__get_portfolio` — confirm the ticker is held + get cost basis, weight, total return
2. Call `mcp__aifolimizer__get_fundamentals` with `symbols=[ticker]` — P/E, EPS, growth, dividend, analyst target, ownership
3. Call `mcp__aifolimizer__get_technicals` with `symbols=[ticker]` — SMA, RSI, MACD, Bollinger Bands, trend
4. Call `mcp__aifolimizer__get_news_headlines` with `ticker=ticker` — latest headlines
5. Call `mcp__aifolimizer__get_macro_snapshot` — rates, CPI, CAD/USD context

### Stage 2 — Adversarial sub-agents (spawn BOTH in parallel via Agent tool)

**Bull Agent prompt:**
> You are a senior equity analyst at a long-only growth fund. Given the following data for [TICKER], construct the strongest possible 12-month bull case. No hedging. Be specific about catalysts, price targets, and entry rationale. Data: [paste full fundamentals + technicals + news + macro snapshot]

**Bear Agent prompt:**
> You are a short-seller at a quantitative hedge fund. Given the following data for [TICKER], construct the strongest possible bear case for the next 12 months. No hedging. Be specific about failure modes, downside targets, and what would trigger a sell. Data: [paste full fundamentals + technicals + news + macro snapshot]

Run both agents in parallel — do NOT wait for one before starting the other.

### Stage 3 — Scenario modeling

Build three scenarios from the agent outputs:

| Scenario | Probability | Price Target | Key Driver |
|----------|------------|--------------|------------|
| Bull     | 35%        | $X           | [catalyst] |
| Base     | 40%        | $X           | [trend]    |
| Bear     | 25%        | $X           | [risk]     |

Probability-weighted expected value = (bull_target × 0.35) + (base_target × 0.40) + (bear_target × 0.25)

### Stage 4 — Decision output

Format the final output as:

---
**[TICKER] — Adversarial Research Summary**

**Decision summary (read this first):**
[2-3 sentences: recommended action, conviction level, primary reason]

**Probability-weighted EV:** $X vs current price $X → implied upside/downside: X%

**Bull case (35%):** [Key thesis in 3 bullets]
**Bear case (25%):** [Key risks in 3 bullets]
**Base case (40%):** [Most likely path in 2 bullets]

**Entry zone:** $X–$X
**Stop-loss:** $X (invalidates bull thesis if breached)
**12-month target:** $X

**Canadian context:**
- Optimal account: TFSA (capital gains tax-free) / RRSP (US withholding applies) / Non-Reg
- Tax note: [relevant based on account types from get_profile]

**Confidence rating:** Strong Buy / Buy / Neutral / Sell / Strong Sell
---

## Rules

- Always run Stage 2 agents in parallel (single message, two Agent tool calls)
- Never invent data — if MCP returns empty for a field, note "data unavailable"
- Keep the full output under 700 words
- Reference the user's actual cost basis and current portfolio weight in the decision framing
- For Canadian tickers (.TO suffix): use TSX context and note CAD/USD impact if applicable
