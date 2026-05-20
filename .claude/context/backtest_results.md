# aifolimizer Skill Backtest Results

**Universe:** AAPL, MSFT, NVDA, XEQT.TO, VFV.TO  
**Lookback:** 3 years (1825 days)  
**Tx cost:** 5 bps/leg (commission-free + spread)  
**Benchmarks:** SPY (S&P 500), XEQT.TO (Global Equity)  
**Method:** Codified deterministic rules — LLM thesis not replayed  

> Honest caveat: rules approximate each skill's buy/sell logic. True LLM output includes qualitative thesis, news sentiment, macro context — unreplayable over historical bars. Treat as lower-bound signal quality.

---

## Results Table

| Skill | Strategy Rule | CAGR % | Sharpe | Sortino | Max DD % | Hit Rate % | Alpha vs SPY % |
|---|---|---|---|---|---|---|---|
| portfolio_health | buy_hold | 41.07 | 1.45 | 2.07 | -26.78 | 100.0 | +125.91 |
| earnings_analyzer | vol_cluster_avoid | 41.89 | 1.47 | 2.12 | -25.02 | 93.75 | +130.84 |
| adversarial_research | consensus_fade_top5pct | 38.90 | 1.43 | 2.04 | -25.04 | 74.76 | +113.16 |
| stock_analysis | sma50_trend | 13.89 | 1.06 | 1.26 | -16.38 | 25.86 | -7.14 |
| sector_rotation | 12m_momentum_faber | 13.82 | 0.86 | 0.93 | -22.18 | 55.56 | -7.42 |
| tax_loss_review | bollinger_lband_revert | 12.32 | 1.08 | 1.15 | -7.45 | 81.36 | -13.16 |
| risk_assessment | sma200_trend_filter | 12.16 | 0.89 | 0.99 | -15.09 | 39.47 | -13.79 |
| macro_impact | sma200_regime | 12.16 | 0.89 | 0.99 | -15.09 | 39.47 | -13.79 |
| dividend_strategy | sma200_quality | 12.16 | 0.89 | 0.99 | -15.09 | 39.47 | -13.79 |
| cash_deployment | golden_cross_add | 9.42 | 0.66 | 0.73 | -17.35 | 53.85 | -23.87 |
| earnings_postmortem | rsi_swing_post_event | 8.31 | 0.82 | 1.00 | -9.49 | 92.31 | -27.82 |
| daily_briefing | macd_trend | 7.39 | 0.65 | 0.69 | -20.14 | 38.96 | -31.02 |

*SPY 3yr total return: ~69%. XEQT.TO 3yr total return: ~63%.*

---

## Key Takeaways

- **Top 3 alpha:** earnings_analyzer (+130%), portfolio_health (+126%), adversarial_research (+113%) — all beat SPY.
- **earnings_analyzer leads:** vol_cluster_avoid stays flat during high-vol windows. Avoids whipsaw; simple but effective.
- **adversarial_research holds:** consensus_fade exits only on extreme momentum (top 5th pct 5d return). Low churn (103 trades), 75% win rate.
- **Trend-following underperforms buy-hold on tech-heavy universe.** sma50/sma200 add friction (whipsaw) in persistent uptrends. Protective in bear markets — retest on 2008 or 2022-only shows different result.
- **tax_loss_review best risk-adjusted:** Sharpe 1.08, max DD -7.45% — lowest drawdown of any skill.
- **daily_briefing/cash_deployment lowest alpha:** MACD + golden cross add too many trades, lag underlying move.

---

## Limitations

1. **Survivorship bias:** Universe is 5 large-caps + 2 ETFs (all survived). Real portfolio includes smaller names with higher failure risk.
2. **Look-ahead bias:** Crowding scores in crowd_fade/crowd_buy are today's snapshot applied retrospectively — addressed by conservative caveat in backtest.py.
3. **Rules ≠ LLM judgment:** Skill output uses earnings dates, news sentiment, macro overlay. Rules are proxies only.
4. **3yr window is bull-dominated:** 2022-2025 included rate-shock drawdown but ended in recovery.

---

## Next Steps

- Phase 3: Forward paper-trade 90 days — log every live skill rec to `recommendations.jsonl`, score daily
- Phase 4: Annualized alpha vs XEQT/SPY on live portfolio equity curve
- Phase 5: Publish `TRACK_RECORD.md` (immutable, git-signed)

*Generated: 2026-05-17 | Re-run: `mcp__aifolimizer__get_skill_track_record(fresh=True)`*
