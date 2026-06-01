"""Shared L2 disk cache for cross-process reuse between FastAPI + MCP.

In-process dict caches (L1) inside each service stay fast for hot-path repeats
within one Python process. But the MCP server and the FastAPI server run as
separate processes — without a shared layer they each pay full yfinance cost
on cold start.

This module wraps `diskcache.Cache` with a tiny API matched to existing usage:

  cache_get(namespace, key)               → value | None
  cache_set(namespace, key, value, ttl)   → None

`diskcache` is thread+process safe (SQLite under the hood). Failures are
swallowed and logged — never block the caller.

Security note (CVE-2025-69872, CVSS 9.8):
  Stock diskcache pickles every cached value. An attacker with write access
  to the cache directory can plant a pickle gadget that achieves arbitrary
  code execution the next time the application reads that entry. The
  project's threat model is single-user / local-machine, so a same-OS
  attacker is already trusted; nevertheless we defense-in-depth by:
    1. Forcing JSON serialization via `diskcache.JSONDisk`. Pickle is
       never invoked on read, so the gadget vector is closed even if the
       directory is somehow writable by another principal.
    2. Tightening the cache directory to owner-only (mode 0700 on POSIX;
       Windows inherits the user-profile ACL).
  Every cached payload in this project is already a JSON-friendly dict
  (quotes, fundamentals, positioning), so JSONDisk is a drop-in.

Storage path: .claude/context/.diskcache/   (in repo root, gitignored).
"""

from __future__ import annotations

import os
import stat
import threading
from pathlib import Path
from typing import Any
from app.security import get_logger

_LOG = get_logger("aifolimizer.services.cache_layer")


try:
    from diskcache import Cache as _DiskCache, JSONDisk as _JSONDisk
except Exception:  # pragma: no cover — optional dep
    _DiskCache = None  # type: ignore[assignment]
    _JSONDisk = None  # type: ignore[assignment]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_CACHE_DIR = _REPO_ROOT / ".claude" / "context" / ".diskcache"

_cache_singleton: Any = None
_lock = threading.Lock()


def _harden_dir_permissions(path: Path) -> None:
    """Best-effort owner-only mode on POSIX. No-op on Windows (NTFS ACL)."""
    if os.name == "nt":
        return
    try:
        path.chmod(stat.S_IRWXU)  # 0o700
    except Exception as e:  # pragma: no cover
        _LOG.warning(f"[cache_layer] chmod 0700 failed for {path}: {e}")


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
                _harden_dir_permissions(_CACHE_DIR)
                # JSONDisk: serializes values via json.dumps/loads — no
                # pickle on read, neutralises CVE-2025-69872 even on a
                # tampered cache directory.
                disk_kwargs: dict[str, Any] = {}
                if _JSONDisk is not None:
                    disk_kwargs["disk"] = _JSONDisk
                _cache_singleton = _DiskCache(
                    str(_CACHE_DIR),
                    size_limit=200 * 1024 * 1024,  # 200 MB cap
                    **disk_kwargs,
                )
            except Exception as e:
                _LOG.warning(f"[cache_layer] init failed: {e}")
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
        _LOG.warning(f"[cache_layer] get {namespace}/{key} failed: {e}")
        return None


def cache_set(namespace: str, key: str, value: Any, ttl_seconds: int) -> None:
    """Store value with TTL. Swallow errors so caller never breaks."""
    c = _get_cache()
    if c is None:
        return
    try:
        c.set(_build_key(namespace, key), value, expire=int(ttl_seconds))
    except Exception as e:
        _LOG.warning(f"[cache_layer] set {namespace}/{key} failed: {e}")


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
        _LOG.warning(f"[cache_layer] clear {namespace} failed: {e}")
    return deleted
