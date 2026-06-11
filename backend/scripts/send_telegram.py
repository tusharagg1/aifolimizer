"""Pipe stdin text to Telegram. Used by run-claude-skill.ps1 to push skill output.

Reads bot token + chat id from app.core.config.settings (backend/.env). Sends as
plain text (no parse_mode) on purpose: arbitrary skill/markdown output would
break HTML/MarkdownV2 parsing on special chars, whereas plain text never errors.
Chunks to Telegram's 4096-char message limit on line boundaries.

Usage:
    echo "the brief" | python scripts/send_telegram.py --title "aifolimizer · daily-briefing"
Exit codes: 0 ok · 1 send error · 2 not configured · 3 empty input.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Run from anywhere - put backend/ on path so app.core.config resolves.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings
from app.services.http_helpers import request_with_retry_after

_API = "https://api.telegram.org"
_MAX = 4096


def _chunks(text: str, limit: int = _MAX) -> list[str]:
    """Split text into <=limit pieces, preferring line boundaries."""
    if len(text) <= limit:
        return [text]
    out: list[str] = []
    cur = ""
    for line in text.split("\n"):
        while len(line) > limit:  # single line longer than a whole message
            if cur:
                out.append(cur)
                cur = ""
            out.append(line[:limit])
            line = line[limit:]
        if cur and len(cur) + len(line) + 1 > limit:
            out.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        out.append(cur)
    return out


def send(text: str, title: str | None = None) -> int:
    token = settings.telegram_bot_token
    chat = settings.telegram_chat_id
    if not token or not chat:
        print(
            "telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)",
            file=sys.stderr,
        )
        return 2
    body = (f"{title}\n\n{text}" if title else text).strip()
    if not body:
        print("empty input - nothing to send", file=sys.stderr)
        return 3
    try:
        for chunk in _chunks(body):
            r = request_with_retry_after(
                "POST",
                f"{_API}/bot{token}/sendMessage",
                json={
                    "chat_id": chat,
                    "text": chunk,
                    "disable_web_page_preview": True,
                },
                timeout=10.0,
            )
            r.raise_for_status()
    except Exception as e:  # noqa: BLE001 - report and signal failure to caller
        print(f"telegram send failed: {e}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Pipe stdin to Telegram.")
    ap.add_argument("--title", default=None, help="optional header line")
    args = ap.parse_args()
    text = sys.stdin.read()
    return send(text, args.title)


if __name__ == "__main__":
    raise SystemExit(main())
