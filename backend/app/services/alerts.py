"""Portfolio alerts: evaluate rules, push to ntfy.sh, persist history.

Rules:
- price_drop_intraday   day_change_pct <= -threshold
- rsi_oversold          RSI(14) <= 30
- rsi_overbought        RSI(14) >= 75
- earnings_imminent     earnings within N days
- concentration_single  single position >= single_max_pct
- concentration_sector  sector >= sector_max_pct

Dedup: same (rule, symbol, day) only fires once.
History: JSONL at .claude/context/alerts.jsonl for MCP read.
Push: POST to https://ntfy.sh/<NTFY_TOPIC> if NTFY_TOPIC set.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from app.models.portfolio import PortfolioResponse
from app.services import (
    fundamentals as fundamentals_svc,
    portfolio_analytics,
    technicals as technicals_svc,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CTX_DIR = _REPO_ROOT / ".claude" / "context"
_STATE_FILE = _CTX_DIR / "alerts_state.json"
_HISTORY_FILE = _CTX_DIR / "alerts.jsonl"

_NTFY_BASE = "https://ntfy.sh"


def _load_state() -> dict[str, str]:
    if not _STATE_FILE.exists():
        return {}
    try:
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict[str, str]) -> None:
    _CTX_DIR.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(
        json.dumps(state, indent=2), encoding="utf-8"
    )


def _dedup_key(rule: str, symbol: str, today: date) -> str:
    return f"{rule}:{symbol}:{today.isoformat()}"


def _append_history(alert: dict) -> None:
    _CTX_DIR.mkdir(parents=True, exist_ok=True)
    with _HISTORY_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(alert) + "\n")


def _push_ntfy(
    topic: str,
    title: str,
    body: str,
    priority: str = "default",
    tags: str = "",
) -> None:
    """POST to ntfy.sh. Swallow errors so one bad alert doesn't block."""
    try:
        headers = {"Title": title, "Priority": priority}
        if tags:
            headers["Tags"] = tags
        httpx.post(
            f"{_NTFY_BASE}/{topic}",
            data=body.encode("utf-8"),
            headers=headers,
            timeout=5.0,
        )
    except Exception as e:
        print(f"[alerts] ntfy push failed: {e}", flush=True)


def evaluate(
    portfolio: PortfolioResponse,
    *,
    price_drop_pct: float = 5.0,
    rsi_oversold: float = 30.0,
    rsi_overbought: float = 75.0,
    earnings_within_days: int = 3,
    single_max_pct: float = 10.0,
    sector_max_pct: float = 35.0,
    top_n_for_technicals: int = 15,
) -> list[dict[str, Any]]:
    """Run all rules. Return triggered alerts (pre-dedup, pre-push)."""
    triggered: list[dict[str, Any]] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    # 1. Intraday price drops
    for pos in portfolio.positions:
        if pos.day_change_pct <= -abs(price_drop_pct):
            sev = "high" if pos.day_change_pct <= -10 else "medium"
            body = (
                f"{pos.symbol} ({pos.name}) intraday "
                f"{pos.day_change_pct:.2f}% — weight {pos.weight:.1f}%, "
                f"value {pos.market_value_cad:.0f} CAD."
            )
            triggered.append({
                "rule": "price_drop_intraday",
                "symbol": pos.symbol,
                "severity": sev,
                "title": f"{pos.symbol} down {pos.day_change_pct:.1f}%",
                "body": body,
                "ts": now_iso,
            })

    # 2. Concentration warnings (delegate to portfolio_analytics)
    conc = portfolio_analytics.concentration_warnings(
        portfolio, single_max_pct, sector_max_pct
    )
    for w in conc:
        kind = w.get("type", "")
        rule = (
            "concentration_single"
            if kind == "single_position"
            else "concentration_sector"
        )
        label = w.get("symbol") or w.get("sector") or "unknown"
        triggered.append({
            "rule": rule,
            "symbol": label,
            "severity": "medium",
            "title": (
                f"Concentration: {label} "
                f"{w.get('weight_pct', 0):.1f}%"
            ),
            "body": w.get("note") or json.dumps(w),
            "ts": now_iso,
        })

    # 3. Technicals — RSI oversold/overbought on top N
    top = sorted(
        portfolio.positions, key=lambda p: p.weight, reverse=True
    )[:top_n_for_technicals]
    tech_symbols = [p.symbol for p in top]
    if tech_symbols:
        try:
            tech = technicals_svc.get_technicals(tech_symbols)
        except Exception as e:
            print(f"[alerts] technicals fetch failed: {e}", flush=True)
            tech = {}
        for sym, data in tech.items():
            rsi = data.get("rsi_14")
            if rsi is None:
                continue
            if rsi <= rsi_oversold:
                triggered.append({
                    "rule": "rsi_oversold",
                    "symbol": sym,
                    "severity": "low",
                    "title": f"{sym} RSI oversold ({rsi:.0f})",
                    "body": (
                        f"{sym} RSI(14) at {rsi:.1f} — potential "
                        f"bounce setup. Review entry."
                    ),
                    "ts": now_iso,
                })
            elif rsi >= rsi_overbought:
                triggered.append({
                    "rule": "rsi_overbought",
                    "symbol": sym,
                    "severity": "low",
                    "title": f"{sym} RSI overbought ({rsi:.0f})",
                    "body": (
                        f"{sym} RSI(14) at {rsi:.1f} — extended; "
                        f"consider trim or hedge."
                    ),
                    "ts": now_iso,
                })

    # 4. Earnings within N days
    try:
        fund_syms = [p.symbol for p in portfolio.positions]
        fund_data = (
            fundamentals_svc.get_fundamentals(fund_syms)
            if fund_syms else {}
        )
    except Exception as e:
        print(f"[alerts] fundamentals fetch failed: {e}", flush=True)
        fund_data = {}
    today = date.today()
    cutoff = today + timedelta(days=earnings_within_days)
    for sym, data in fund_data.items():
        ed = data.get("earnings_date")
        if not ed:
            continue
        try:
            ed_date = date.fromisoformat(str(ed)[:10])
        except Exception:
            continue
        if today <= ed_date <= cutoff:
            days_until = (ed_date - today).days
            triggered.append({
                "rule": "earnings_imminent",
                "symbol": sym,
                "severity": "medium",
                "title": (
                    f"{sym} earnings in {days_until}d "
                    f"({ed_date.isoformat()})"
                ),
                "body": (
                    f"{sym} reports {ed_date.isoformat()}. Run "
                    f"earnings_analyzer skill to decide hold-through."
                ),
                "ts": now_iso,
            })

    return triggered


_PRIORITY_MAP = {"high": "high", "medium": "default", "low": "low"}
_TAG_MAP = {
    "price_drop_intraday": "chart_with_downwards_trend",
    "rsi_oversold": "arrow_down",
    "rsi_overbought": "arrow_up",
    "earnings_imminent": "calendar",
    "concentration_single": "warning",
    "concentration_sector": "warning",
}


def dispatch(
    triggered: list[dict[str, Any]],
    *,
    ntfy_topic: str | None = None,
    write_history: bool = True,
) -> dict[str, int]:
    """Dedup, append to history, push to ntfy. Returns counts."""
    state = _load_state()
    today = date.today()
    pushed = 0
    skipped = 0
    new_state = dict(state)

    # Trim state to last 7 days so file doesn't grow unbounded
    keep_cutoff = (today - timedelta(days=7)).isoformat()
    new_state = {
        k: v for k, v in new_state.items() if v >= keep_cutoff
    }

    for alert in triggered:
        key = _dedup_key(alert["rule"], alert["symbol"], today)
        if key in new_state:
            skipped += 1
            continue
        new_state[key] = today.isoformat()
        if write_history:
            _append_history(alert)
        if ntfy_topic:
            priority = _PRIORITY_MAP.get(
                alert.get("severity", ""), "default"
            )
            tags = _TAG_MAP.get(alert["rule"], "")
            _push_ntfy(
                ntfy_topic,
                alert["title"],
                alert["body"],
                priority=priority,
                tags=tags,
            )
        pushed += 1

    _save_state(new_state)
    return {
        "triggered": len(triggered),
        "pushed": pushed,
        "deduped": skipped,
    }


def read_recent_history(
    since_hours: int = 24, limit: int = 100
) -> list[dict]:
    """Read JSONL history. Return alerts newer than cutoff. Newest first."""
    if not _HISTORY_FILE.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    out: list[dict] = []
    try:
        with _HISTORY_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                ts = rec.get("ts")
                if not ts:
                    continue
                try:
                    rec_dt = datetime.fromisoformat(ts)
                except Exception:
                    continue
                if rec_dt >= cutoff:
                    out.append(rec)
    except Exception as e:
        print(f"[alerts] history read failed: {e}", flush=True)
        return []
    out.sort(key=lambda r: r.get("ts", ""), reverse=True)
    return out[:limit]
