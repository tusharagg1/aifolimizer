"""Security helpers: session cookies, rate limiting, structured logging.

No new dependencies - uses stdlib only (logging, time, threading, json).

## Session cookie
`SESSION_COOKIE_NAME = "aifolimizer_session"`. Set via `set_session_cookie` on
successful login/verify-otp. Read via `session_from_request` which prefers the
cookie but falls back to the legacy `session_id` query parameter so older
clients keep working during the migration.

Cookie attributes: HttpOnly, SameSite=Lax, Secure when SESSION_COOKIE_SECURE
env var is "1". 8h TTL matches Wealthsimple token TTL in `wealthsimple.py`.

## Rate limiting
`RateLimiter` is a sliding-window counter keyed by `(scope, identity)`. Used
for login + OTP - strict limits protect against credential stuffing and OTP
brute force.

## Logging
`configure_logging()` installs a JSON-line formatter when STRUCTURED_LOGS=1,
otherwise a friendly text format. `get_logger(name)` returns a stdlib logger.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from typing import Optional

from fastapi import HTTPException, Request, Response


SESSION_COOKIE_NAME = "aifolimizer_session"
SESSION_TTL_SECONDS = 8 * 60 * 60  # match WS access-token TTL


# ── Logging ──────────────────────────────────────────────────────────────────


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "name": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for k, v in record.__dict__.items():
            if k in payload or k.startswith("_"):
                continue
            if k in {
                "args",
                "msg",
                "exc_info",
                "exc_text",
                "stack_info",
                "lineno",
                "filename",
                "module",
                "pathname",
                "funcName",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "processName",
                "process",
                "levelno",
                "levelname",
                "name",
                "created",
            }:
                continue
            try:
                json.dumps(v)
                payload[k] = v
            except (TypeError, ValueError):
                payload[k] = repr(v)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Install root logger handler. Idempotent - safe to call repeatedly."""
    root = logging.getLogger()
    # Strip prior handlers to avoid duplicate lines on reload
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler()
    if os.environ.get("STRUCTURED_LOGS") == "1":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


# ── Session cookie helpers ───────────────────────────────────────────────────


def _secure_flag() -> bool:
    return os.environ.get("SESSION_COOKIE_SECURE") == "1"


def set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=_secure_flag(),
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        samesite="lax",
        secure=_secure_flag(),
    )


def session_from_request(
    request: Request,
    legacy_query_value: Optional[str] = None,
) -> Optional[str]:
    """Return session_id from httpOnly cookie when present, else legacy query.

    Backwards-compatible during migration - once all clients use cookies the
    legacy fallback can be removed.
    """
    sid = request.cookies.get(SESSION_COOKIE_NAME)
    if sid:
        return sid
    return legacy_query_value or None


# ── In-memory sliding-window rate limiter ────────────────────────────────────


class RateLimiter:
    """Threadsafe sliding-window rate limiter, keyed by (scope, identity).

    Not durable across restarts and not multi-process - for that move to Redis.
    For a single-host backend this is sufficient to defeat credential stuffing
    and OTP brute-force on the login surface.
    """

    def __init__(self) -> None:
        self._buckets: dict[tuple[str, str], deque[float]] = {}
        self._lock = threading.Lock()

    def hit(
        self,
        scope: str,
        identity: str,
        *,
        max_hits: int,
        window_seconds: int,
    ) -> tuple[bool, int, float]:
        """Record one hit. Returns (allowed, remaining, retry_after_seconds)."""
        now = time.time()
        cutoff = now - window_seconds
        key = (scope, identity)
        with self._lock:
            dq = self._buckets.setdefault(key, deque())
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= max_hits:
                retry = max(0.0, dq[0] + window_seconds - now)
                return False, 0, retry
            dq.append(now)
            return True, max_hits - len(dq), 0.0


_LIMITER = RateLimiter()


def get_rate_limiter() -> RateLimiter:
    return _LIMITER


def enforce_rate_limit(
    request: Request,
    scope: str,
    *,
    max_hits: int,
    window_seconds: int,
    identity_override: Optional[str] = None,
) -> None:
    """Raise HTTP 429 if the (scope, identity) bucket is exhausted.

    Identity defaults to client IP. Pass `identity_override` (e.g. email) for
    user-tier limits when the request body carries a stable identifier.
    """
    identity = identity_override or (request.client.host if request.client else "unknown")
    allowed, _remaining, retry = _LIMITER.hit(
        scope,
        identity,
        max_hits=max_hits,
        window_seconds=window_seconds,
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limit_exceeded",
                "scope": scope,
                "retry_after_seconds": round(retry, 1),
            },
            headers={"Retry-After": str(max(1, int(retry)))},
        )
