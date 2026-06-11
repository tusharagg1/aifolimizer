"""
Interactive MFA login helper for aifolimizer MCP server.

Run once to authenticate with Wealthsimple (including MFA) and cache the
session token to ~/.aifolimizer/ws_session.json. The MCP server reloads this file
on each tool call so you only need to re-run when the session expires (~8h).

Usage:
    cd backend
    .venv/Scripts/python mcp_login.py
"""

import getpass
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Must import after dotenv so PYTHONPATH includes app/
sys.path.insert(0, str(Path(__file__).parent))

from ws_api import WealthsimpleAPI, OTPRequiredException, LoginFailedException, WSAPISession
from ws_api.exceptions import CurlException

# WS sits behind Cloudflare; the default python-requests User-Agent is
# flagged/rate-limited (CF error 1015) faster than a browser UA.
_WS_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
WealthsimpleAPI.set_user_agent(_WS_USER_AGENT)


def _exit_rate_limited(exc: Exception) -> None:
    s = str(exc)
    if "1015" in s or "Expecting value" in s:
        print(
            "ERROR: Wealthsimple is rate-limiting this IP (Cloudflare 1015).\n"
            "Wait 15-60 min, then retry ONCE - repeated attempts extend it."
        )
    else:
        print(f"ERROR: WS request failed - {exc}")
    sys.exit(1)


# Unified WS session file - same path app.services.wealthsimple persists/reads.
# Schema must match wealthsimple._persist_session so refresh + restore + MCP all
# share one file (token rotation never orphans the session the MCP server reads).
SESSION_FILE = Path.home() / ".aifolimizer" / "ws_session.json"


def main() -> None:
    print("aifolimizer - Wealthsimple MFA Login")
    print("=" * 40)

    email = os.getenv("WS_EMAIL", "").strip()
    if not email:
        email = input("Email: ").strip()

    password = os.getenv("WS_PASSWORD", "").strip()
    if not password:
        password = getpass.getpass("Password: ")

    print("Logging in...", flush=True)

    def _noop_persist(_sess, _uname=None):
        pass

    try:
        session: WSAPISession = WealthsimpleAPI.login(
            username=email,
            password=password,
            otp_answer=None,
            persist_session_fct=_noop_persist,
        )
    except OTPRequiredException:
        otp = input("MFA code (check your email/authenticator): ").strip()
        try:
            session = WealthsimpleAPI.login(
                username=email,
                password=password,
                otp_answer=otp,
                persist_session_fct=_noop_persist,
            )
        except OTPRequiredException:
            print("ERROR: OTP rejected. Try again.")
            sys.exit(1)
        except LoginFailedException as e:
            print(f"ERROR: Login failed - {e}")
            sys.exit(1)
        except CurlException as e:
            _exit_rate_limited(e)
    except LoginFailedException as e:
        print(f"ERROR: Login failed - {e}")
        sys.exit(1)
    except CurlException as e:
        _exit_rate_limited(e)

    from app.services.wealthsimple import _atomic_write_json

    _atomic_write_json(
        SESSION_FILE,
        {
            "email": email,
            "session_json": session.to_json(),
            "saved_utc": time.time(),
        },
    )
    print(f"\nSession cached to {SESSION_FILE}")
    print("MCP server + backend share this file; it auto-refreshes on use.")
    print("Re-run this script only when Wealthsimple forces re-auth (MFA).")


if __name__ == "__main__":
    main()
