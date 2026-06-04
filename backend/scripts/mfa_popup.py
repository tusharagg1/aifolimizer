"""Local Tk popup to enter Wealthsimple MFA code and refresh the session.

Spawned by aifolimizer-launch.ps1 (or invoked directly) when
~/.aifolimizer/ws_session.json is missing or rejected by WS. Reads
WS_EMAIL / WS_PASSWORD from backend/.env, triggers the OTP request,
shows a Tk InputBox, and persists the new session on success.

Same on-disk schema as mcp_login.py / wealthsimple._persist_session.
Closes the notify-throttle stamp on success so heads-up notifications
resume cleanly the next time the session expires.

Purpose
    GUI Tk fallback to prompt for the Wealthsimple MFA code when no
    terminal is attached — e.g. when the launcher fires from a Windows
    Scheduled Task, a shortcut, or any context without an interactive
    console for stdin-based prompts.

Security
    The entered MFA digit is NEVER logged, printed, or persisted to
    disk. It is held in a local variable only long enough to pass to
    ws-api in-memory for the OTP exchange, after which only the
    resulting session token is written (mode 0600) to
    ~/.aifolimizer/ws_session.json. The raw code does not appear in
    logs, the session file, or any error message.

Limitations
    Requires an interactive desktop session (Windows: an attached
    user session; X11/Wayland: a live DISPLAY). On a headless service
    or over SSH without X-forwarding, Tk cannot create a window and
    this script will fail at root = tk.Tk(); callers must fall back
    to the terminal/CLI flow (mcp_login.py) in those environments.

Trigger
    Invoked by scripts/aifolimizer-launch.ps1 (and indirectly by
    backend/scripts/mfa_notify.py) whenever the saved WS session is
    missing/expired and stdin is not a TTY, so a console prompt would
    silently hang the run-claude-skill.ps1 pipeline.

Exit codes:
    0  session refreshed
    1  config / login error
    2  user cancelled the popup
    3  code rejected
    4  WS_EMAIL / WS_PASSWORD missing in backend/.env
"""

from __future__ import annotations

import logging
import os
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import simpledialog, messagebox

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv()

from ws_api import (  # noqa: E402
    WealthsimpleAPI,
    OTPRequiredException,
    LoginFailedException,
    WSAPISession,
)

SESSION_FILE = Path.home() / ".aifolimizer" / "ws_session.json"
NOTIFY_FILE = Path.home() / ".aifolimizer" / ".mfa-notify.last"


def _persist(session: WSAPISession, email: str) -> None:
    from app.services.wealthsimple import _atomic_write_json

    _atomic_write_json(
        SESSION_FILE,
        {
            "email": email,
            "session_json": session.to_json(),
            "saved_utc": time.time(),
        },
    )


def _ask_otp() -> str | None:
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    code = simpledialog.askstring(
        "aifolimizer · WS MFA",
        "Enter 6-digit Wealthsimple MFA code:",
        parent=root,
    )
    root.destroy()
    if code is None:
        return None
    digits = "".join(c for c in code if c.isdigit())
    return digits if 4 <= len(digits) <= 8 else None


def _msg(kind: str, text: str) -> None:
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    if kind == "error":
        messagebox.showerror("aifolimizer", text, parent=root)
    else:
        messagebox.showinfo("aifolimizer", text, parent=root)
    root.destroy()


def main() -> int:
    email = os.getenv("WS_EMAIL", "").strip()
    password = os.getenv("WS_PASSWORD", "").strip()
    if not email or not password:
        _msg("error", "WS_EMAIL / WS_PASSWORD missing in backend/.env")
        return 4

    def _noop(_s, _u=None):
        pass

    try:
        session = WealthsimpleAPI.login(
            username=email,
            password=password,
            otp_answer=None,
            persist_session_fct=_noop,
        )
        # WS didn't demand OTP — this login produced a fresh, valid session.
        # Persist it so the launcher's silent-success path actually refreshes
        # ws_session.json instead of leaving the old (possibly stale) token.
        _persist(session, email)
        _msg("info", "WS session already valid. No MFA required.")
        return 0
    except OTPRequiredException:
        pass
    except LoginFailedException as e:
        _msg("error", f"WS rejected credentials: {e}")
        return 1

    code = _ask_otp()
    if code is None:
        return 2

    try:
        session = WealthsimpleAPI.login(
            username=email,
            password=password,
            otp_answer=code,
            persist_session_fct=_noop,
        )
    except OTPRequiredException:
        _msg("error", "Code rejected. Re-run launcher to try again.")
        return 3
    except LoginFailedException as e:
        _msg("error", f"Login error: {e}")
        return 1

    _persist(session, email)
    try:
        NOTIFY_FILE.unlink(missing_ok=True)
    except OSError:
        logging.getLogger(__name__).debug("suppressed exception", exc_info=True)
    _msg("info", "WS session refreshed. Skills can run for 8h.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
