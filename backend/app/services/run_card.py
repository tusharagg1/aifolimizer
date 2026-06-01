"""SHA256-based run card provenance for backtests.

Each backtest emits a run_card.json recording the strategy name, config hash,
metrics, validation results, and UTC timestamp — making backtest claims auditable.

Adapted from Vibe-Trading (HKUDS/Vibe-Trading, MIT License).
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

_RUN_CARDS_DIR = Path(__file__).parent.parent.parent / "data" / "run_cards"
_SCHEMA_VERSION = "1.0"


def _sha256_short(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()[:16]


def generate_run_card(
    strategy: str,
    config: dict,
    metrics: dict,
    validation: dict | None = None,
    symbols: list[str] | None = None,
) -> dict:
    """Build a provenance record for one backtest run."""
    config_str = json.dumps(config, sort_keys=True)
    config_hash = _sha256_short(config_str)
    strategy_hash = _sha256_short(strategy)
    run_id = f"{strategy_hash[:8]}-{config_hash[:8]}"

    return {
        "schema_version": _SCHEMA_VERSION,
        "run_id": run_id,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "strategy": strategy,
        "strategy_hash": strategy_hash,
        "config_hash": config_hash,
        "config": config,
        "symbols": symbols or [],
        "metrics": metrics,
        "validation": validation or {},
    }


def save_run_card(run_card: dict) -> str:
    """Persist run card to disk. Returns path string."""
    _RUN_CARDS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{run_card['run_id']}_{int(time.time())}.json"
    path = _RUN_CARDS_DIR / filename
    path.write_text(json.dumps(run_card, indent=2))
    return str(path)


def list_run_cards(limit: int = 20) -> list[dict]:
    """Return recent run cards sorted newest-first."""
    if not _RUN_CARDS_DIR.exists():
        return []
    paths = sorted(
        _RUN_CARDS_DIR.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    result = []
    for p in paths[:limit]:
        try:
            card = json.loads(p.read_text())
            result.append(
                {
                    "run_id": card.get("run_id"),
                    "timestamp_utc": card.get("timestamp_utc"),
                    "strategy": card.get("strategy"),
                    "symbols": card.get("symbols", []),
                    "config_hash": card.get("config_hash"),
                    "metrics": card.get("metrics", {}),
                }
            )
        except Exception:
            continue
    return result
