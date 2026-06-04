"""Trade-idea ranking — single source shared by the MCP tool and the headless
fallback runner.

`rank_trade_ideas` turns a list of recommendation dicts (from
recommendations.get_recommendations) into ranked, decision-ready trade ideas,
applying the same filters the dashboard uses: drop non-actionable actions, drop
wait-for-pullback entries, drop ideas below the risk/reward floor. Pure — no I/O,
no session — so both `mcp_server.get_trade_ideas` and the codified fallback
runner produce identical output.
"""

from __future__ import annotations

_SKIP_ACTIONS = {"HOLD", "WATCH", "PASS", "NO_EDGE"}


def rank_trade_ideas(
    recs: list[dict],
    held: set[str],
    *,
    top_n: int = 3,
    min_risk_reward: float = 1.5,
) -> dict:
    """Filter + rank recommendation dicts into trade ideas.

    Returns {ideas, min_risk_reward, scored, actionable}. `scored` is the count
    of recommendations considered; `actionable` is the count surviving filters
    (before the top_n cut).
    """
    ideas: list[dict] = []
    for r in recs:
        action = (r.get("action") or "").upper()
        if action in _SKIP_ACTIONS:
            continue
        if r.get("entry_timing") == "wait_pullback":
            continue
        rr = r.get("risk_reward")
        if rr is not None and rr < min_risk_reward:
            continue
        sym = r.get("symbol")
        ideas.append(
            {
                "symbol": sym,
                "name": r.get("name") or sym,
                "action": action,
                "held": sym in held,
                "score": r.get("score"),
                "conviction": r.get("confidence"),
                "current_price": r.get("current_price"),
                "entry_timing": r.get("entry_timing"),
                "stop_loss": r.get("stop_loss"),
                "take_profit": r.get("take_profit"),
                "risk_reward": rr,
                "kelly_pct": r.get("kelly_pct"),
                "currency": r.get("currency"),
                "reasons": (r.get("reasons") or [])[:3],
            }
        )

    ideas.sort(key=lambda x: x.get("score") or 0, reverse=True)
    return {
        "ideas": ideas[: max(0, top_n)],
        "min_risk_reward": min_risk_reward,
        "scored": len(recs),
        "actionable": len(ideas),
    }
