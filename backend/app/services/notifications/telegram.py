"""Single Telegram sendMessage owner for in-app notifications.

Consolidates the formatter that lived in alerts._push_telegram plus the
"read settings -> delegate" boilerplate that event_dispatcher and the scheduler
each re-implemented. Errors are swallowed so one failed push never blocks the
caller.

- send(bot_token, chat_id, ...): format + post with explicit creds. alerts
  imports this as `_push_telegram` so its existing callers are unchanged.
- push(...): settings-aware; no-op when creds unset.

main.py's bespoke plain-text "online" ping (with its own 30-min marker) and the
standalone scripts keep their own minimal senders - different message shapes,
separate processes.
"""

from __future__ import annotations

import logging

import httpx

_LOG = logging.getLogger(__name__)
_TELEGRAM_API = "https://api.telegram.org"

_EMOJI_MAP = {
    "price_drop_intraday": "📉",
    "rsi_oversold": "⬇️",
    "rsi_overbought": "⬆️",
    "earnings_imminent": "📅",
    "concentration_single": "⚠️",
    "concentration_sector": "⚠️",
}


def send(
    bot_token: str,
    chat_id: str,
    title: str,
    body: str,
    severity: str = "medium",
    rule: str = "",
) -> None:
    """Format + send one Telegram message. Swallows errors."""
    try:
        emoji = _EMOJI_MAP.get(rule, "🔔")
        severity_tag = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(severity, "🔵")
        text = f"{severity_tag} <b>{emoji} {title}</b>\n{body}"
        httpx.post(
            f"{_TELEGRAM_API}/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=5.0,
        )
    except Exception as e:
        _LOG.warning(f"telegram send failed: {e}")


def push(title: str, body: str, severity: str = "medium", rule: str = "") -> None:
    """Settings-aware push: no-op when creds unset, else send()."""
    from app.core.config import settings

    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return
    send(
        settings.telegram_bot_token,
        settings.telegram_chat_id,
        title,
        body,
        severity,
        rule,
    )
