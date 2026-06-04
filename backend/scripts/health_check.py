#!/usr/bin/env python3
"""Self-diagnostic for the aifolimizer harness wiring.

Audits the parts that fail silently: MCP server importability + tool count,
Wealthsimple session token presence/freshness, settings.json hooks, and core
service imports. Prints a PASS/WARN/FAIL report. No network, no PII.

Run: backend/.venv/Scripts/python.exe backend/scripts/health_check.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Ensure the backend package root is importable regardless of cwd.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_OK, _WARN, _FAIL = "PASS", "WARN", "FAIL"
_results: list[tuple[str, str, str]] = []


def _add(name: str, status: str, detail: str) -> None:
    _results.append((name, status, detail))


def _check_python() -> None:
    v = sys.version_info
    ok = (v.major, v.minor) >= (3, 12)
    _add(
        "python_version",
        _OK if ok else _WARN,
        f"{v.major}.{v.minor}.{v.micro} (project requires 3.12+)",
    )


def _check_mcp_server() -> None:
    try:
        import mcp_server as m  # noqa: F401

        tools = [t for t in dir(m) if not t.startswith("_") and callable(getattr(m, t))]
        _add("mcp_server_import", _OK, f"imported; ~{len(tools)} top-level callables")
    except Exception as exc:
        _add("mcp_server_import", _FAIL, f"import failed: {exc}")


def _check_services() -> None:
    core = [
        "wealthsimple",
        "market_data",
        "fundamentals",
        "technicals",
        "portfolio_optimizer",
        "dcf",
        "backtest_stats",
        "hypotheses",
    ]
    bad = []
    for name in core:
        try:
            __import__(f"app.services.{name}")
        except Exception as exc:
            bad.append(f"{name} ({exc})")
    if bad:
        _add("core_services", _FAIL, "failed: " + "; ".join(bad))
    else:
        _add("core_services", _OK, f"{len(core)} core services import clean")


def _check_ws_session() -> None:
    f = Path.home() / ".aifolimizer" / "ws_session.json"
    if not f.exists():
        _add("ws_session", _WARN, "no token file — first run will require WS login")
        return
    age_h = (time.time() - f.stat().st_mtime) / 3600
    ttl_h = float(os.environ.get("WS_TOKEN_TTL_HOURS", "336"))
    if age_h > ttl_h:
        _add("ws_session", _WARN, f"stale: {age_h:.0f}h old > {ttl_h:.0f}h TTL — re-auth needed")
    else:
        _add("ws_session", _OK, f"fresh: {age_h:.0f}h old (TTL {ttl_h:.0f}h)")


def _check_settings_hooks() -> None:
    candidates = [
        Path.home() / ".claude" / "settings.json",
        Path.cwd() / ".claude" / "settings.json",
    ]
    found = False
    for p in candidates:
        if not p.exists():
            continue
        found = True
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            _add(f"settings:{p.parent.parent.name}", _FAIL, f"invalid JSON: {exc}")
            continue
        hooks = data.get("hooks", {})
        events = sorted(hooks.keys())
        _add(
            f"settings_hooks:{p.parent.parent.name}", _OK if events else _WARN, f"events: {', '.join(events) or 'none'}"
        )
    if not found:
        _add("settings_hooks", _WARN, "no settings.json found")


def _check_env_file() -> None:
    f = Path(__file__).resolve().parents[1] / ".env"
    if not f.exists():
        _add("env_file", _WARN, "backend/.env missing — copy from .env.example")
        return
    email = ""
    for line in f.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("WS_EMAIL="):
            email = line.split("=", 1)[1].strip()
            break
    if not email or email == "your@email.com":
        _add("env_file", _WARN, "WS_EMAIL not set — portfolio skills need it")
    else:
        _add("env_file", _OK, "backend/.env present, WS_EMAIL set")


def _check_mcp_registered() -> None:
    claude = shutil.which("claude")
    if not claude:
        _add("mcp_registered", _WARN, "claude CLI not on PATH — cannot verify")
        return
    try:
        out = subprocess.run(
            [claude, "mcp", "list"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        ).stdout or ""
    except Exception as exc:
        _add("mcp_registered", _WARN, f"`claude mcp list` failed: {exc}")
        return
    if "aifolimizer" in out:
        _add("mcp_registered", _OK, "aifolimizer registered with Claude")
    else:
        _add("mcp_registered", _WARN, "not registered — run setup.sh / setup.ps1")


def main() -> int:
    _check_python()
    _check_mcp_server()
    _check_services()
    _check_ws_session()
    _check_settings_hooks()
    _check_env_file()
    _check_mcp_registered()

    width = max(len(n) for n, _, _ in _results)
    print("aifolimizer health check")
    print("=" * (width + 30))
    worst = _OK
    for name, status, detail in _results:
        print(f"{name.ljust(width)}  [{status}]  {detail}")
        if status == _FAIL:
            worst = _FAIL
        elif status == _WARN and worst != _FAIL:
            worst = _WARN
    print("=" * (width + 30))
    print(f"OVERALL: {worst}")
    return 1 if worst == _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
