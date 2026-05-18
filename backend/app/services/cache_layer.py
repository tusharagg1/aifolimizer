"""Shared L2 disk cache for cross-process reuse between FastAPI + MCP.

In-process dict caches (L1) inside each service stay fast for hot-path repeats
within one Python process. But the MCP server and the FastAPI server run as
separate processes — without a shared layer they each pay full yfinance cost
on cold start.

This module wraps `diskcache.Cache` with a tiny API matched to existing usage:

  cache_get(namespace, key)               → value | None
  cache_set(namespace, key, value, ttl)   → None

`diskcache` is thread+process safe (SQLite under the hood). Values are pickled.
Failures are swallowed and logged — never block the caller.

Storage path: .claude/context/.diskcache/   (in repo root, gitignored).
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

try:
    from diskcache import Cache as _DiskCache
except Exception:  # pragma: no cover — optional dep
    _DiskCache = None  # type: ignore[assignment]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_CACHE_DIR = _REPO_ROOT / ".claude" / "context" / ".diskcache"

_cache_singleton: Any = None
_lock = threading.Lock()


def _get_cache() -> Any:
    """Lazy singleton — survive missing diskcache lib (returns None)."""
    global _cache_singleton
    if _cache_singleton is not None:
        return _cache_singleton
    if _DiskCache is None:
        return None
    with _lock:
        if _cache_singleton is None:
            try:
                _CACHE_DIR.mkdir(parents=True, exist_ok=True)
                _cache_singleton = _DiskCache(
                    str(_CACHE_DIR),
                    size_limit=200 * 1024 * 1024,  # 200 MB cap
                )
            except Exception as e:
                print(f"[cache_layer] init failed: {e}", flush=True)
                _cache_singleton = None
    return _cache_singleton


def _build_key(namespace: str, key: str) -> str:
    return f"{namespace}::{key}"


def cache_get(namespace: str, key: str) -> Any | None:
    """Return cached value or None on miss / lib missing / error."""
    c = _get_cache()
    if c is None:
        return None
    try:
        return c.get(_build_key(namespace, key))
    except Exception as e:
        print(f"[cache_layer] get {namespace}/{key} failed: {e}", flush=True)
        return None


def cache_set(namespace: str, key: str, value: Any, ttl_seconds: int) -> None:
    """Store value with TTL. Swallow errors so caller never breaks."""
    c = _get_cache()
    if c is None:
        return
    try:
        c.set(_build_key(namespace, key), value, expire=int(ttl_seconds))
    except Exception as e:
        print(f"[cache_layer] set {namespace}/{key} failed: {e}", flush=True)


def cache_clear_namespace(namespace: str) -> int:
    """Remove all keys for a namespace. Returns deleted count."""
    c = _get_cache()
    if c is None:
        return 0
    deleted = 0
    try:
        prefix = f"{namespace}::"
        for k in list(c.iterkeys()):
            if isinstance(k, str) and k.startswith(prefix):
                if c.delete(k):
                    deleted += 1
    except Exception as e:
        print(f"[cache_layer] clear {namespace} failed: {e}", flush=True)
    return deleted
