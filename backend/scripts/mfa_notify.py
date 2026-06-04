"""Send a one-shot Telegram heads-up when WS session is expired.

Triggered by mfa-watchdog.ps1 every 30 min. Idempotent within COOLDOWN_SEC
so the user does not get a stream of identical messages while away from
their PC. Does NOT wait for a reply — this is notification-only. The user
runs aifolimizer-launch.ps1 (or invokes mfa_popup.py directly) to actually
re-auth via a local popup when they sit down.

Exit codes:
    0  notification sent (or cooldown active — silently skipped)
    1  telegram config missing
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv()

from app.core.config import settings  # noqa: E402

NOTIFY_FILE = Path.home() / ".aifolimizer" / ".mfa-notify.last"
# One heads-up per expiry EVENT, then at most a daily reminder while still
# expired. clear_flag() (called on successful re-auth) resets this so the
# next expiry notifies immediately rather than waiting out the window.
COOLDOWN_SEC = 24 * 3600
TG_API = "https://api.telegram.org"


def _last_sent() -> float:
    if not NOTIFY_FILE.exists():
        return 0.0
    try:
        return float(NOTIFY_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0.0


def _stamp() -> None:
    NOTIFY_FILE.parent.mkdir(parents=True, exist_ok=True)
    NOTIFY_FILE.write_text(str(time.time()), encoding="utf-8")


def clear_flag() -> None:
    """Reset the notify cursor on successful auth so the next real expiry
    fires a fresh heads-up instead of being suppressed by the daily window."""
    try:
        NOTIFY_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def main() -> int:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        print("[mfa_notify] telegram not configured", flush=True)
        return 1
    if time.time() - _last_sent() < COOLDOWN_SEC:
        return 0
    text = "🔐 aifolimizer · WS session expired.\nRun aifolimizer-launch.ps1 at your PC to re-auth via popup."
    try:
        r = httpx.post(
            f"{TG_API}/bot{settings.telegram_bot_token}/sendMessage",
            json={"chat_id": settings.telegram_chat_id, "text": text},
            timeout=10,
        )
        r.raise_for_status()
    except Exception as e:
        print(f"[mfa_notify] send failed: {e}", flush=True)
        return 1
    _stamp()
    return 0


if __name__ == "__main__":
    sys.exit(main())
