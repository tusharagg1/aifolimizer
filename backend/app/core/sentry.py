"""Sentry init (Phase 15).

Opt-in via SENTRY_DSN. Empty DSN = no-op.

PII guard: portfolio / WS credentials / account ids must never reach
Sentry servers. `send_default_pii=False` plus a custom `before_send`
filter drops any event whose serialized form contains forbidden tokens.
"""
from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger(__name__)

# Substrings that should never appear in events sent to Sentry.
# Case-insensitive match drops the entire event.
_PII_TOKENS = (
    "password",
    "ws_token",
    "ws_session",
    "ws_password",
    "ws_email",
    "account_id",
    "account_number",
    "wealthsimple.com",
    "tusharagg1@",
)


def _strip_pii(event: dict[str, Any], hint: dict[str, Any] | None = None):
    try:
        s = json.dumps(event, default=str).lower()
    except Exception:
        return event
    for tok in _PII_TOKENS:
        if tok in s:
            return None  # drop entire event
    return event


def init_sentry(settings) -> bool:
    dsn = getattr(settings, "sentry_dsn", "") or ""
    if not dsn:
        log.info("sentry: disabled (no SENTRY_DSN)")
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.asyncio import AsyncioIntegration
    except ImportError:
        log.warning("sentry: SDK not installed; skipping")
        return False
    try:
        sentry_sdk.init(
            dsn=dsn,
            traces_sample_rate=0.1,
            profiles_sample_rate=0.0,
            send_default_pii=False,
            integrations=[FastApiIntegration(), AsyncioIntegration()],
            environment=getattr(settings, "environment", "dev"),
            release=getattr(settings, "app_version", "unknown"),
            before_send=_strip_pii,
        )
        log.info(
            "sentry: enabled (env=%s, release=%s)",
            getattr(settings, "environment", "dev"),
            getattr(settings, "app_version", "unknown"),
        )
        return True
    except Exception as e:
        log.warning("sentry: init failed: %s", e)
        return False
