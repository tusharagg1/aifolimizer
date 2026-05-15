"""
Interactive MFA login helper for aifolimizer MCP server.

Run once to authenticate with Wealthsimple (including MFA) and cache the
session token to backend/.ws_session.json. The MCP server reloads this file
on each tool call so you only need to re-run when the session expires (~8h).

Usage:
    cd backend
    .venv/Scripts/python mcp_login.py
"""

import getpass
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Must import after dotenv so PYTHONPATH includes app/
sys.path.insert(0, str(Path(__file__).parent))

from ws_api import WealthsimpleAPI, OTPRequiredException, LoginFailedException, WSAPISession

SESSION_FILE = Path(__file__).parent / ".ws_session.json"


def main() -> None:
    print("aifolimizer — Wealthsimple MFA Login")
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
            print(f"ERROR: Login failed — {e}")
            sys.exit(1)
    except LoginFailedException as e:
        print(f"ERROR: Login failed — {e}")
        sys.exit(1)

    payload = {"session_json": session.to_json(), "email": email}
    SESSION_FILE.write_text(json.dumps(payload), encoding="utf-8")
    print(f"\nSession cached to {SESSION_FILE}")
    print("MCP server will use this session for ~8 hours.")
    print("Re-run this script when the session expires.")


if __name__ == "__main__":
    main()
