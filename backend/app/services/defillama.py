"""DefiLlama crypto macro via public API (free, no key).

Total DeFi TVL + top chains, and aggregate stablecoin market cap + top issuers.
Rising stablecoin supply = dry powder entering crypto; falling TVL/stablecoins
= risk-off. Macro-health context for the crypto sleeve, alongside CoinGecko
prices and the Crypto Fear & Greed index.

Docs: https://defillama.com/docs/api
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.security import get_logger

_LOG = get_logger("aifolimizer.services.defillama")

_CHAINS = "https://api.llama.fi/v2/chains"
_STABLES = "https://stablecoins.llama.fi/stablecoins?includePrices=false"
_TIMEOUT = 20.0
_TTL = 1800  # 30m
_cache: tuple[dict, float] | None = None


def _b(x: float) -> float:
    """USD → billions, 2dp."""
    return round((x or 0) / 1e9, 2)


def _circulating(asset: dict) -> float:
    return float((asset.get("circulating") or {}).get("peggedUSD") or 0)


def crypto_macro() -> dict[str, Any]:
    """Total DeFi TVL + top chains and aggregate stablecoin supply. Cached 30m."""
    global _cache
    now = time.time()
    if _cache and now - _cache[1] < _TTL:
        return _cache[0]

    out: dict[str, Any] = {"data_source": "DefiLlama (free, no key)"}

    try:
        _r = httpx.get(_CHAINS, timeout=_TIMEOUT)
        _r.raise_for_status()
        chains = _r.json()
        total_tvl = sum(c.get("tvl") or 0 for c in chains)
        top = sorted(chains, key=lambda c: c.get("tvl") or 0, reverse=True)[:8]
        out["total_defi_tvl_b"] = _b(total_tvl)
        out["top_chains"] = [{"name": c.get("name"), "tvl_b": _b(c.get("tvl") or 0)} for c in top]
    except Exception as e:
        _LOG.warning(f"[defillama] chains fetch failed: {e}")
        out["tvl_error"] = "fetch_failed"

    try:
        _rs = httpx.get(_STABLES, timeout=_TIMEOUT)
        _rs.raise_for_status()
        stables = _rs.json()
        pegged = stables.get("peggedAssets") or []
        total_sc = sum(_circulating(a) for a in pegged)
        top_sc = sorted(pegged, key=_circulating, reverse=True)[:5]
        out["total_stablecoin_mcap_b"] = _b(total_sc)
        out["top_stablecoins"] = [{"symbol": a.get("symbol"), "mcap_b": _b(_circulating(a))} for a in top_sc]
    except Exception as e:
        _LOG.warning(f"[defillama] stablecoins fetch failed: {e}")
        out["stablecoin_error"] = "fetch_failed"

    _cache = (out, now)
    return out
