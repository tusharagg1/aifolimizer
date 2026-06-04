"""Shadow account: extract behavioral trading rules from transaction history.

Pipeline:
1. FIFO-pair buy→sell roundtrips from raw transactions
2. Engineer features: holding_days, entry_hour, entry_dow, return_pct
3. K-means cluster roundtrips (k=2..4, inertia-elbow scored; pure numpy)
4. Extract a rule per cluster: holding_days bounds + entry_hour mode
5. Report: win-rate, avg return, behavioral summary per cluster

Input format for transactions list:
  [{"symbol": "AAPL", "side": "buy"|"sell", "price": 150.0,
    "quantity": 10, "date": "2024-01-15T10:30:00"}, ...]

Adapted from Vibe-Trading (HKUDS/Vibe-Trading, MIT License).
No scikit-learn dependency — numpy-only k-means.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np


def _parse_date(d: str | datetime) -> datetime:
    if isinstance(d, datetime):
        return d
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(d, fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {d!r}")


def _fifo_pair(transactions: list[dict]) -> list[dict]:
    """Pair buys→sells per symbol using FIFO with quantity tracking.

    Earlier impl popped the entire buy lot on the first matching sell, ignoring
    quantity. Scale-in (multiple buys then a single sell) and scale-out (one
    buy then multiple partial sells) both got mispaired — partial sells
    consumed whole prior buys and starved later sells of inventory.

    Now each buy lot keeps a `remaining_qty`; each sell consumes
    `min(buy_remaining, sell_remaining)` and emits a roundtrip per consumed
    slice. Lots fully drained leave the queue; partial buys stay in front
    until depleted.
    """
    by_symbol: dict[str, list[dict]] = {}
    for t in transactions:
        sym = str(t.get("symbol", "")).upper()
        if sym:
            by_symbol.setdefault(sym, []).append(t)

    roundtrips = []
    for sym, trades in by_symbol.items():
        trades_sorted = sorted(trades, key=lambda x: _parse_date(x["date"]))
        # buy_queue carries (buy_dict, remaining_qty) — not the raw row,
        # because we mutate the qty as sells consume the lot.
        buy_queue: list[tuple[dict, float]] = []
        for t in trades_sorted:
            side = str(t.get("side", "")).lower()
            if side in ("buy", "b", "purchase"):
                qty = float(t.get("quantity", 1) or 0)
                if qty <= 0:
                    qty = 1.0
                buy_queue.append((t, qty))
                continue
            if side not in ("sell", "s", "sale"):
                continue
            sell_remaining = float(t.get("quantity", 1) or 0)
            if sell_remaining <= 0:
                sell_remaining = 1.0
            xp = float(t.get("price", 0))
            exit_dt = _parse_date(t["date"])
            while sell_remaining > 1e-9 and buy_queue:
                buy, buy_remaining = buy_queue[0]
                consumed = min(buy_remaining, sell_remaining)
                entry_dt = _parse_date(buy["date"])
                holding = max(0, (exit_dt - entry_dt).days)
                ep = float(buy.get("price", 0))
                ret_pct = (xp - ep) / ep * 100 if ep > 0 else 0.0
                roundtrips.append(
                    {
                        "symbol": sym,
                        "entry_date": entry_dt.strftime("%Y-%m-%d"),
                        "exit_date": exit_dt.strftime("%Y-%m-%d"),
                        "entry_price": round(ep, 4),
                        "exit_price": round(xp, 4),
                        "quantity": round(consumed, 6),
                        "holding_days": holding,
                        "entry_hour": entry_dt.hour,
                        "entry_dow": entry_dt.weekday(),
                        "return_pct": round(ret_pct, 2),
                        "profitable": ret_pct > 0,
                    }
                )
                buy_remaining -= consumed
                sell_remaining -= consumed
                if buy_remaining <= 1e-9:
                    buy_queue.pop(0)
                else:
                    buy_queue[0] = (buy, buy_remaining)
    return roundtrips


def _normalize(X: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std[std == 0] = 1.0
    return (X - mean) / std, mean, std


def _kmeans_numpy(
    X: np.ndarray,
    k: int,
    max_iter: int = 100,
    seed: int = 42,
) -> tuple[np.ndarray, float]:
    """Pure numpy K-means. Returns (labels, inertia)."""
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(X), size=k, replace=False)
    centroids = X[idx].copy()

    labels = np.zeros(len(X), dtype=int)
    for _ in range(max_iter):
        dists = np.linalg.norm(X[:, None] - centroids[None, :], axis=2)
        new_labels = np.argmin(dists, axis=1)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for ki in range(k):
            mask = labels == ki
            if mask.any():
                centroids[ki] = X[mask].mean(axis=0)

    inertia = float(
        sum(np.linalg.norm(X[labels == ki] - centroids[ki]) ** 2 for ki in range(k) if (labels == ki).any())
    )
    return labels, inertia


def _cluster_trades(roundtrips: list[dict], max_k: int = 4) -> list[dict]:
    """Cluster roundtrips; extract behavioral rule per cluster."""
    if len(roundtrips) < 6:
        return []

    features = np.array(
        [[r["holding_days"], r["entry_hour"], r["entry_dow"], r["return_pct"]] for r in roundtrips], dtype=float
    )
    X, _, _ = _normalize(features)

    # Elbow: pick k where marginal inertia drop flattens
    k_range = range(2, min(max_k + 1, len(roundtrips)))
    inertias: dict[int, float] = {}
    all_labels: dict[int, np.ndarray] = {}
    for k in k_range:
        labels, inertia = _kmeans_numpy(X, k)
        inertias[k] = inertia
        all_labels[k] = labels

    # Pick best k via largest elbow drop
    best_k = 2
    if len(inertias) > 1:
        ks = sorted(inertias)
        drops = {ks[i]: inertias[ks[i - 1]] - inertias[ks[i]] for i in range(1, len(ks))}
        best_k = max(drops, key=lambda k: drops[k])

    best_labels = all_labels[best_k]
    rules = []
    for ki in range(best_k):
        mask = best_labels == ki
        cluster = [r for r, m in zip(roundtrips, mask) if m]
        if not cluster:
            continue
        hold = [r["holding_days"] for r in cluster]
        rets = [r["return_pct"] for r in cluster]
        hours = [r["entry_hour"] for r in cluster]
        rules.append(
            {
                "cluster": ki,
                "n_trades": len(cluster),
                "win_rate_pct": round(sum(1 for r in cluster if r["profitable"]) / len(cluster) * 100, 1),
                "avg_return_pct": round(float(np.mean(rets)), 2),
                "holding_days_min": int(min(hold)),
                "holding_days_max": int(max(hold)),
                "holding_days_median": int(np.median(hold)),
                "entry_hour_mode": int(np.bincount(hours).argmax()),
                "symbols": list({r["symbol"] for r in cluster}),
                "behavioral_rule": (
                    f"Hold {int(np.median(hold))}d median "
                    f"(range {min(hold)}–{max(hold)}d); "
                    f"enter around hour {int(np.bincount(hours).argmax())}; "
                    f"win-rate {round(sum(1 for r in cluster if r['profitable']) / len(cluster) * 100, 1)}%"
                ),
            }
        )
    return rules


def _detect_biases(roundtrips: list[dict]) -> dict[str, Any]:
    """Diagnose cognitive trading biases from realized roundtrips.

    Reuses the FIFO-paired data — no extra fetch. Each bias carries its
    evidence and a flagged bool so a skill can act only on confirmed skew.
    """
    if len(roundtrips) < 4:
        return {"note": f"need ≥4 roundtrips for bias detection; have {len(roundtrips)}"}

    wins = [r for r in roundtrips if r["profitable"]]
    losses = [r for r in roundtrips if not r["profitable"]]
    enough = len(roundtrips) >= 6
    out: dict[str, Any] = {}

    # Disposition effect (Odean 1998): sell winners early, ride losers.
    if wins and losses:
        win_hold = float(np.median([r["holding_days"] for r in wins]))
        loss_hold = float(np.median([r["holding_days"] for r in losses]))
        ratio = loss_hold / win_hold if win_hold > 0 else None
        flagged = bool(enough and loss_hold > win_hold * 1.3)
        out["disposition_effect"] = {
            "flagged": flagged,
            "median_hold_winners_days": round(win_hold, 1),
            "median_hold_losers_days": round(loss_hold, 1),
            "loser_to_winner_hold_ratio": round(ratio, 2) if ratio else None,
            "interpretation": (
                "Holding losers longer than winners — disposition effect "
                "(cutting winners early, riding losers). Drags returns."
                if flagged
                else "No strong disposition skew."
            ),
        }

    # Gain/loss asymmetry: average win size vs average loss size.
    if wins and losses:
        avg_gain = float(np.mean([r["return_pct"] for r in wins]))
        avg_loss = float(np.mean([abs(r["return_pct"]) for r in losses]))
        payoff = avg_gain / avg_loss if avg_loss > 0 else None
        flagged = bool(payoff is not None and payoff < 1.0)
        out["gain_loss_asymmetry"] = {
            "flagged": flagged,
            "avg_gain_pct": round(avg_gain, 2),
            "avg_loss_pct": round(avg_loss, 2),
            "payoff_ratio": round(payoff, 2) if payoff else None,
            "interpretation": (
                "Average loss exceeds average gain — letting losers run. Needs >50% win-rate just to break even."
                if flagged
                else "Average gain exceeds average loss — favourable payoff."
            ),
        }

    # Overtrading: cadence and hold length.
    dates = sorted(_parse_date(r["entry_date"]) for r in roundtrips)
    span_days = max(1, (dates[-1] - dates[0]).days)
    per_month = len(roundtrips) / (span_days / 30.0)
    median_hold = float(np.median([r["holding_days"] for r in roundtrips]))
    flagged = bool(per_month > 8 or median_hold < 3)
    out["overtrading"] = {
        "flagged": flagged,
        "roundtrips_per_month": round(per_month, 1),
        "median_holding_days": round(median_hold, 1),
        "interpretation": (
            "High churn — frequent short-hold roundtrips. Costs and taxes erode edge; tighten entry criteria."
            if flagged
            else "Trade cadence reasonable."
        ),
    }

    # Anchoring: entries clustering at round-number prices.
    entries = [r["entry_price"] for r in roundtrips if r.get("entry_price")]
    if entries:

        def _near_round(p: float) -> bool:
            for base in (round(p), round(p / 5) * 5):
                if base > 0 and abs(p - base) / p <= 0.01:
                    return True
            return False

        frac = sum(1 for p in entries if _near_round(p)) / len(entries)
        flagged = bool(enough and frac > 0.5)
        out["anchoring"] = {
            "flagged": flagged,
            "entries_near_round_numbers_pct": round(frac * 100, 1),
            "interpretation": (
                "Over half of entries cluster at round-number prices — anchoring to psychological levels over signal."
                if flagged
                else "Entries not unduly clustered at round numbers."
            ),
        }

    out["biases_flagged"] = [k for k, v in out.items() if isinstance(v, dict) and v.get("flagged")]
    return out


def analyze_shadow_account(transactions: list[dict]) -> dict[str, Any]:
    """Full shadow account pipeline. Returns roundtrips + extracted rules + summary."""
    if not transactions:
        return {"error": "no_transactions"}

    try:
        roundtrips = _fifo_pair(transactions)
    except Exception as e:
        return {"error": f"pairing_failed: {e}"}

    if not roundtrips:
        return {
            "error": "no_roundtrips_found",
            "raw_transaction_count": len(transactions),
            "hint": "Ensure transactions have 'symbol', 'side' (buy/sell), 'price', 'date' fields",
        }

    profitable = [r for r in roundtrips if r["profitable"]]
    rets = [r["return_pct"] for r in roundtrips]

    rules: list[dict] = []
    cluster_note: str | None = None
    if len(roundtrips) >= 6:
        try:
            rules = _cluster_trades(roundtrips)
        except Exception as e:
            cluster_note = f"clustering_failed: {e}"
    else:
        cluster_note = f"need ≥6 roundtrips for clustering; have {len(roundtrips)}"

    result: dict[str, Any] = {
        "summary": {
            "total_roundtrips": len(roundtrips),
            "profitable_count": len(profitable),
            "win_rate_pct": round(len(profitable) / len(roundtrips) * 100, 1),
            "avg_return_pct": round(float(np.mean(rets)), 2),
            "median_return_pct": round(float(np.median(rets)), 2),
            "avg_holding_days": round(float(np.mean([r["holding_days"] for r in roundtrips])), 1),
            "symbols_traded": sorted(set(r["symbol"] for r in roundtrips)),
        },
        "extracted_rules": rules,
        "behavioral_biases": _detect_biases(roundtrips),
        "roundtrips": roundtrips[:100],
    }
    if cluster_note:
        result["cluster_note"] = cluster_note
    return result
