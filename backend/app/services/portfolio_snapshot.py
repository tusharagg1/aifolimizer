"""Shared read access to the cross-process portfolio snapshot (L2 diskcache).

Single source of truth for the namespaces/keys that mcp_server writes on every
live portfolio fetch. Background scripts (run_alerts, run_maintenance) read the
snapshot through here INSTEAD of making their own Wealthsimple round-trip - a
bg WS call rotates the shared single-use refresh token and races interactive
sessions into forced MFA. Reading the snapshot touches no WS token.
"""

from __future__ import annotations

from app.models.portfolio import PortfolioResponse
from app.services import cache_layer

SNAPSHOT_NS = "portfolio_snapshot"
LASTGOOD_NS = "portfolio_lastgood"


def key_for(account_id: str = "") -> str:
    return account_id or "_ALL_"


def read(account_id: str = "") -> PortfolioResponse | None:
    """Freshest available snapshot: short-TTL snapshot first, else last-good.

    Returns None when neither is present (caller should skip rather than fall
    back to a live WS fetch, to keep bg processes off the shared token).
    """
    key = key_for(account_id)
    for ns in (SNAPSHOT_NS, LASTGOOD_NS):
        cached = cache_layer.cache_get(ns, key)
        if cached:
            try:
                return PortfolioResponse(**cached)
            except Exception:
                continue
    return None
