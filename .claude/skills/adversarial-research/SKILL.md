---
name: adversarial-research
description: Run a parallel bull/bear adversarial research pipeline on a specific ticker. Use when the user asks for "adversarial research", "bull bear analysis", "deep research on [ticker]", a bull-bear debate on X, or wants a rigorous debate-style investment thesis. (Bare "should I buy X?" belongs to pre-trade-check.) Then spawns parallel sub-agents over live data.
---

# Adversarial Research Pipeline (Stage 0-5)

> **Data-grounding contract:** every numeric claim made by ANY advocate or the
> PM must trace to a Layer-1 tool response in this run. Restate the verified
> figures up front; cite only those. No recalled or estimated numbers — if it
> wasn't fetched, it's "not available". This is what keeps the debate honest.

Modelled on TradingAgents multi-agent hedge fund workflow. Explicit DAG: memory recall → parallel data → parallel advocates → three-tier risk debate → portfolio manager synthesis → log decision. Each layer waits for prior layer to complete.

```
Layer 0 (serial):   get_profile + get_ticker_decision_history + get_cross_ticker_lessons + recall_preferences
Layer 1 (parallel): get_portfolio | get_fundamentals | get_technicals |
                    get_news_headlines | get_macro_snapshot | get_positioning_signals |
                    get_stocktwits_sentiment | get_community_sentiment |
                    get_insider_sentiment | get_finnhub_news | get_recent_filings |
                    get_search_interest
Layer 2 (parallel): Bull Agent | Bear Agent | Consensus Agent
                    (all receive identical Layer 1 snapshot + Layer 0 memory context)
Layer 2.5 (serial): Probability Assignment - PM reads Layer 2 outputs, assigns scenario probs,
                    produces ARBITER_MEMO before risk managers see anything
Layer 3 (parallel): Risk Aggressive | Risk Neutral | Risk Conservative
                    (all receive Layer 1 + Layer 2 outputs + ARBITER_MEMO with anchored probs)
Layer 4 (serial):   Portfolio Manager synthesis (this context window)
Layer 5 (serial):   Decision output + log_trade_decision
```

Rules for DAG execution:
- Layer 0: 4 MCP calls in ONE message (true parallel)
- Layer 1: 12 MCP calls in ONE message (true parallel); the 4 US-only adds (insider/news/filings/search) return empty for .TO names — that's fine, agents note "unavailable"
- Layer 2: 3 Agent calls in ONE message (true parallel); pass identical data to all three
- Layer 2.5: IN MAIN CONTEXT - read all three Layer 2 outputs, produce ARBITER_MEMO (see below). Do NOT spawn agent.
- Layer 3: 3 Agent calls in ONE message (true parallel); pass Layer 2 outputs + Layer 1 data + ARBITER_MEMO
- Layer 4+5: synthesize in main context; do NOT spawn more agents

## Stage 0 - Memory & Profile (call all 4 in parallel)

1. `mcp__aifolimizer__get_profile` - account types, capital, cash available
2. `mcp__aifolimizer__get_ticker_decision_history` with `ticker=TICKER, max_decisions=5` - past decisions, outcomes, reflections
3. `mcp__aifolimizer__get_cross_ticker_lessons` with `max_lessons=3` - portfolio-level win/loss patterns
4. `mcp__aifolimizer__recall_preferences` with `query="TICKER investment"` - stored investor preferences for this name

**Memory injection rule**: If Stage 0 returns past decisions for this ticker, prepend a short summary to ALL Layer 2 agent prompts:
> "Past decisions on [TICKER]: [date] [action] → [outcome] ([pnl]%). Reflection: [text]. Do NOT repeat failed thesis unless new data overrides it."

If cross-ticker lessons contain patterns relevant to this ticker (same sector, same setup), inject them too.

## Stage 1 - Data collection (call all 12 in parallel)

1. `mcp__aifolimizer__get_portfolio` - confirm ticker is held + cost basis, weight, total return
2. `mcp__aifolimizer__get_fundamentals` with `symbols=[ticker]` - P/E, EPS, growth, dividend, analyst target
3. `mcp__aifolimizer__get_technicals` with `symbols=[ticker]` - SMA, RSI, MACD, Bollinger Bands, trend, ATR
4. `mcp__aifolimizer__get_news_headlines` with `ticker=ticker` - latest headlines
5. `mcp__aifolimizer__get_macro_snapshot` - rates, CPI, CAD/USD context
6. `mcp__aifolimizer__get_positioning_signals` with `symbols=[ticker]` - crowding score, institutional ownership, short interest
7. `mcp__aifolimizer__get_stocktwits_sentiment` with `ticker=ticker` - real-time retail labeled sentiment
8. `mcp__aifolimizer__get_community_sentiment` with `ticker=ticker` - Reddit community signal
9. `mcp__aifolimizer__get_insider_sentiment` with `ticker=ticker` - insider MSPR buying-pressure trend (feeds Bull/Bear conviction)
10. `mcp__aifolimizer__get_finnhub_news` with `ticker=ticker` - news bull/bear tally + net_sentiment (corroborates or contradicts headline narrative)
11. `mcp__aifolimizer__get_recent_filings` with `ticker=ticker` - recent material SEC filings; an 8-K in the last week is a catalyst both advocates must address
12. `mcp__aifolimizer__get_search_interest` with `keywords=[company name]` - retail search-demand proxy; a surge is crowding confirmation for the Consensus agent

(Items 9-12 are US-only — empty for .TO tickers; pass through as "unavailable".)

## Stage 2 - Adversarial sub-agents (spawn ALL THREE in parallel)

Pass each agent: full Layer 1 data snapshot + memory context from Stage 0.

**Bull Agent prompt:**
> [MEMORY CONTEXT IF ANY]
> You are a senior equity analyst at a long-only growth fund. Given the following data for [TICKER], construct the strongest possible 12-month bull case. No hedging. Be specific about catalysts, price targets, and entry rationale. Data: [paste full fundamentals + technicals + news + macro + stocktwits + reddit]
>
> Respond in this exact structure:
> THESIS: [1 sentence core thesis]
> CATALYSTS: [3 bullets, each with expected timing]
> PRICE_TARGET: $X (method: [DCF/multiple expansion/technical])
> UPSIDE_DRIVERS: [top 2 quantitative drivers]
> BULL_INVALIDATION: [exact condition that kills this thesis]
> CONVICTION: [HIGH / MEDIUM / LOW] because [1 reason]

**Bear Agent prompt:**
> [MEMORY CONTEXT IF ANY]
> You are a short-seller at a quantitative hedge fund. Given the following data for [TICKER], construct the strongest possible bear case for the next 12 months. No hedging. Be specific about failure modes, downside targets, and what would trigger a sell. Data: [paste full fundamentals + technicals + news + macro + stocktwits + reddit]
>
> Respond in this exact structure:
> THESIS: [1 sentence core thesis]
> FAILURE_MODES: [3 bullets, each with probability of occurring]
> DOWNSIDE_TARGET: $X (method: [trough multiple/technical support/DCF bear case])
> BEAR_ACCELERANTS: [top 2 factors that speed the decline]
> BEAR_INVALIDATION: [exact condition that kills this thesis]
> CONVICTION: [HIGH / MEDIUM / LOW] because [1 reason]

**Consensus Agent prompt (crowding + retail sentiment lens):**
> [MEMORY CONTEXT IF ANY]
> You are a positioning analyst at a multi-strategy hedge fund. Given the positioning and sentiment data for [TICKER] (institutional crowding, short interest, StockTwits bull/bear count, Reddit community score, analyst coverage, headline velocity), determine: (1) Is this name consensus-crowded by AI-driven retail + quant flows? (2) What does the labeled StockTwits sentiment say - is retail positioned ahead of or behind the move? (3) What is the marginal buyer thesis - who is left to buy? (4) What's the contrarian view that current price ignores? (5) If consensus is wrong, what's the unwind path? Treat a `get_search_interest` surge + consensus crowding as late-cycle retail fuel; treat insider MSPR buying against bearish retail as a contrarian tell. No hedging. Data: [paste positioning_signals + stocktwits + reddit + search_interest + insider_sentiment + fundamentals + news]
>
> Respond in this exact structure:
> CROWDING_VERDICT: [CROWDED / NEUTRAL / CONTRARIAN] - score: [X/100]
> RETAIL_POSITION: [AHEAD_OF_MOVE / BEHIND_MOVE / NEUTRAL]
> MARGINAL_BUYER: [who is left to buy, 1 sentence]
> CONTRARIAN_VIEW: [what price ignores, 1 sentence]
> UNWIND_PATH: [if consensus wrong, how does it unwind, 1 sentence]
> SENTIMENT_EDGE: [BULLISH_EDGE / BEARISH_EDGE / NO_EDGE]

## Stage 2.5 - Probability Assignment (main context, NO new agents)

After Stage 2 agents return, YOU (PM in main context) read all three structured outputs and produce ARBITER_MEMO before Stage 3:

```
ARBITER_MEMO for [TICKER]:

Bull conviction: [HIGH/MEDIUM/LOW] | Bear conviction: [HIGH/MEDIUM/LOW] | Crowding: [CROWDED/NEUTRAL/CONTRARIAN]

Probability assignment:
- Bull scenario: [X]%  - anchored by: [bull CONVICTION + BULL_INVALIDATION status]
- Base scenario: [X]%  - anchored by: [consensus trend + technicals trend signal]
- Bear scenario: [X]%  - anchored by: [bear CONVICTION + BEAR_ACCELERANTS]

Probability adjustment rules (apply in order):
1. If Bull CONVICTION=HIGH AND Bear CONVICTION=LOW → shift +10% to Bull, -10% from Bear
2. If Bear CONVICTION=HIGH AND Bull CONVICTION=LOW → shift +10% to Bear, -10% from Bull
3. If CROWDING_VERDICT=CROWDED → shift -5% from Bull (late-entry risk), +5% to Bear
4. If CROWDING_VERDICT=CONTRARIAN AND Bull CONVICTION≥MEDIUM → shift +5% to Bull
5. If memory shows recent failed bull thesis on this ticker → shift -5% from Bull
6. Probabilities must sum to 100%; base absorbs remainder

Arbiter read: [1 sentence on which side has stronger structural case]
Key risk to base case: [1 sentence]
```

Pass ARBITER_MEMO verbatim to all three Stage 3 agents.

## Stage 3 - Three-tier risk debate (spawn ALL THREE in parallel)

Receives: full Layer 1 data + Bull/Bear/Consensus outputs from Stage 2 + ARBITER_MEMO from Stage 2.5.

**Aggressive Risk Analyst prompt:**
> You are an aggressive risk analyst at a growth hedge fund. Your mandate: maximize upside capture, accept higher volatility. Given the bull/bear debate outputs, ARBITER_MEMO with probability weights, and portfolio data for [TICKER], argue for the most aggressive position size and entry that is still within Kelly criterion bounds. Data: [paste portfolio weights + technicals + positioning + bull/bear outputs + ARBITER_MEMO]
>
> Respond in this exact structure:
> RECOMMENDED_SIZE: [X]% of portfolio (max Kelly)
> RATIONALE: [why bull thesis at assigned probability justifies this size, 1-2 sentences]
> ENTRY_ZONE: $X-$X
> STOP: $X (ties to: [bull invalidation condition from Stage 2])
> CHANGE_MY_MIND: [specific data point or price level that would flip you]

**Neutral Risk Analyst prompt:**
> You are a risk-neutral portfolio analyst. Your mandate: balance upside with drawdown protection. Given the bull/bear debate, ARBITER_MEMO with probability weights, and portfolio data for [TICKER], argue for a moderate position size that respects both opportunity and downside. Data: [paste portfolio weights + technicals + positioning + bull/bear outputs + ARBITER_MEMO]
>
> Respond in this exact structure:
> RECOMMENDED_SIZE: [X]% of portfolio (half-Kelly)
> BULL_WEIGHT: [X]% | BEAR_WEIGHT: [X]% (your read vs arbiter assignment - state if you agree/disagree)
> GUARDRAILS: [2 specific risk controls, e.g. trailing stop, scale-in trigger]
> ENTRY_ZONE: $X-$X
> STOP: $X

**Conservative Risk Analyst prompt:**
> You are a conservative risk manager at a pension-influenced multi-asset fund. Your mandate: capital preservation first, participation second. Given the bull/bear debate, ARBITER_MEMO with probability weights, and portfolio data for [TICKER], argue for the smallest position size that captures meaningful upside without meaningful drawdown. Issue VETO if: existing weight >8%, beta >2.0 in fear regime, no viable stop, crowding_score ≥85. Data: [paste portfolio weights + technicals + positioning + bull/bear outputs + ARBITER_MEMO]
>
> Respond in this exact structure:
> VETO: [YES reason / NO]
> RECOMMENDED_SIZE: [X]% of portfolio (quarter-Kelly or flat; 0% if VETO)
> KEY_RISKS: [top 2 risks that make you cautious]
> ENTRY_ZONE: $X-$X (or "do not enter" if VETO)
> STOP: $X (or "N/A" if VETO)

## Stage 4 - Portfolio Manager synthesis

Portfolio Manager (main context window) synthesizes all six agent outputs:

**Decision framework:**
1. Use probability weights from ARBITER_MEMO (NOT default 35/40/25 - those were already adjusted in Stage 2.5)
2. Read Aggressive, Neutral, Conservative risk outputs → if Conservative issues VETO, state VETO and exit
3. If no VETO: size = median of three recommended sizes, capped at Conservative's RECOMMENDED_SIZE
4. Compute probability-weighted EV: (bull_target × bull_prob) + (base_target × base_prob) + (bear_target × bear_prob)
5. Cross-check: ARBITER_MEMO already applied memory penalty - verify it was applied if Stage 0 found failed thesis
6. Stop-loss = consensus of the three risk agents' STOP levels (use most conservative non-VETO stop)

**Scenario table:**

| Scenario | Probability | Price Target | Key Driver |
|----------|------------|--------------|------------|
| Bull     | [X]%       | $X           | [catalyst] |
| Base     | [Y]%       | $X           | [trend]    |
| Bear     | [Z]%       | $X           | [risk]     |

## Stage 5 - Decision output + log

Format final output:

---
**[TICKER] - Adversarial Research Summary**

**Decision summary (read this first):**
[2-3 sentences: recommended action, conviction level, primary reason]

**Past decision context:** [If Stage 0 found prior decisions: "Last decision [date]: [action] → [outcome]. [reflection]. This analysis [confirms/overrides] that view because [reason]." If no prior: "First analysis of this ticker."]

**Probability-weighted EV:** $X vs current price $X → implied upside/downside: X%

**Bull case (X%):** [3 bullets]
**Bear case (X%):** [3 bullets]
**Base case (Y%):** [2 bullets]
**Sentiment read:** [StockTwits bull/bear count + community_score; Reddit community_score; divergence note if retail vs institutional positioning differs]
**Consensus / crowding:** [crowding score + label; marginal-buyer thesis; "edge already priced" or "contrarian opportunity"]

**Risk debate verdict:** [Aggressive: X% / Neutral: X% / Conservative: X% - final size: X% (~$Y). VETO reason if applicable.]

**Entry zone:** $X-$X
**Stop-loss:** $X (invalidates bull thesis if breached)
**12-month target:** $X

**Canadian context:**
- Optimal account: TFSA / RRSP / Non-Reg
- Tax note: [relevant based on account types from get_profile]

**Confidence rating:** Strong Buy / Buy / Neutral / Sell / Strong Sell
---

**After outputting:** Call `mcp__aifolimizer__log_trade_decision` with the final action, conviction, entry_price, target_price, stop_price, thesis_summary (1-2 sentences), skill_used="adversarial-research". This enables Phase B/C outcome tracking for future analyses.

## Rules

- Layer 0 memory calls are MANDATORY - never skip. A prior stop-out on this ticker that repeats the same thesis is a signal failure.
- Always run Stage 2 in parallel (3 Agent calls in one message)
- Always run Stage 3 in parallel (3 Agent calls in one message) - do NOT merge with Stage 2
- Never invent data - if MCP returns empty for field, note "data unavailable"
- Full output under 800 words
- Reference user's actual cost basis and current portfolio weight in decision framing
- For Canadian tickers (.TO suffix): use TSX context, note CAD/USD impact if applicable
- StockTwits and Reddit divergence is signal: retail bullish + institutional short = crowded long at risk; retail bearish + institutional long = contrarian setup

## Gotchas

- Bull/Bear/Consensus agents MUST see same data snapshot - collect Layer 1 first, pass identical data. Asymmetric inputs invalidate comparison.
- Spawn Layer 2 agents in ONE message (3 calls); spawn Layer 3 agents in ONE separate message (3 calls). Sequential spawning is not parallel.
- Sub-agents MUST NOT hedge - if either returns "on the other hand..." reasoning, prompt failed. Reject and re-prompt.
- Probability weights (35/40/25) are default - adjust to evidence; do NOT force template when data clearly leans one way.
- `get_macro_snapshot` cached 12h - for rate-decision-week analyses, WebSearch before relying on it.
- Stop-loss must invalidate BULL thesis specifically - tie to thesis breakpoint (e.g. "below 200-SMA breaks uptrend assumption"), not generic % drop.
- Confidence rating must reflect data completeness - if 2+ MCP fields null, max rating is Neutral.
- Consensus agent should NOT default to bearish - crowded long can keep working. Job is surface marginal-buyer thesis. Reject output if it just restates Bear case.
- Conservative analyst VETO is binding - Portfolio Manager cannot override a VETO. If VETO issued, state reason and do not size a position.
- StockTwits `.TO` tickers automatically strip suffix - no manual handling needed.
- `log_trade_decision` is NOT optional - call it after every completed analysis. Skipping breaks the Phase B/C feedback loop.
- When `crowding_score >= 70` AND `Bull case` dominant, downgrade confidence one notch (consensus risk on late entries).
- When `crowding_score <= 30` AND `Bull case` dominant AND data complete, contrarian setup - flag explicitly.
- Stage 2.5 ARBITER_MEMO is mandatory - do NOT skip to Stage 3 without it. Risk managers sizing without anchored probabilities produce incoherent position recommendations.
- Sub-agent structured output format is enforced - if an agent returns unstructured prose instead of labeled fields (THESIS:, CONVICTION:, etc.), the output is invalid. Note the failure and use raw content with caveat.
- Probability adjustment rules in Stage 2.5 are applied IN ORDER and are cumulative - all applicable adjustments stack, then normalize to 100%.
- Stage 3 agents may DISAGREE with ARBITER_MEMO probability weights - this disagreement is signal, not error. Note it in Stage 4 synthesis.
- Stop-loss consensus rule: if VETO is not issued but Conservative's stop is >15% below entry, flag as "wide stop - consider scaling entry to tighten risk".
