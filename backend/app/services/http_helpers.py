"""HTTP helpers for upstream provider calls.

`request_with_retry_after` wraps an httpx call so a single `429 Too Many
Requests` (or `503 Service Unavailable`) doesn't immediately trip the
circuit breaker. The provider tells us how long to wait via the
`Retry-After` header (seconds or HTTP-date); we sleep up to a cap and
retry exactly once. Anything beyond that bubbles up so the breaker can
take over.

This module replaces ad-hoc `httpx.get(...)` calls scattered through
adapters / Telegram / Sentry. Existing callers can migrate gradually -
unwrapped callers still work, they just get the previous behaviour.

Why one retry only:
- Two retries on the same upstream is rarely better than failing fast.
- The fallback chain in `data_router` already provides cross-provider
  redundancy.
- Multiple in-process retries amplify load on a struggling upstream.
"""

from __future__ import annotations

import time
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

_RETRY_STATUSES = frozenset({429, 503})
_RETRY_AFTER_CAP_S = 30.0


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    try:
        seconds = float(value)
        if seconds < 0:
            return None
        return seconds
    except ValueError:
        pass
    # HTTP-date form per RFC 7231.
    try:
        dt = parsedate_to_datetime(value)
        if dt is None:
            return None
        delta = dt.timestamp() - time.time()
        return max(0.0, delta)
    except Exception:
        return None


def request_with_retry_after(
    method: str,
    url: str,
    *,
    client: httpx.Client | None = None,
    cap_seconds: float = _RETRY_AFTER_CAP_S,
    **kwargs: Any,
) -> httpx.Response:
    """Issue one HTTP request, honor a single `Retry-After` if returned.

    Caller controls timeout, headers, etc via kwargs. Reuse a long-lived
    `httpx.Client` when calling repeatedly to amortize TLS handshake.
    """
    own_client = client is None
    cli = client or httpx.Client()
    try:
        resp = cli.request(method, url, **kwargs)
        if resp.status_code in _RETRY_STATUSES:
            sleep_for = _parse_retry_after(resp.headers.get("Retry-After"))
            if sleep_for is not None:
                time.sleep(min(cap_seconds, sleep_for))
                resp = cli.request(method, url, **kwargs)
        return resp
    finally:
        if own_client:
            cli.close()


def get_with_retry_after(url: str, **kwargs: Any) -> httpx.Response:
    return request_with_retry_after("GET", url, **kwargs)
