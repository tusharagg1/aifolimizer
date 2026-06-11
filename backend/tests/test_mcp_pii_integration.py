"""Integration test: assert no PII leaks through MCP tools returning portfolio data.

Best-effort: when run without a live Wealthsimple session, tools that require
auth raise and the test skips that tool. CI runs it nonetheless to catch
hardcoded PII (e.g. an email accidentally embedded in a fixture or default
return value).
"""

from __future__ import annotations

import inspect
import re

import pytest

try:
    from backend import mcp_server as _mcp  # type: ignore
except ImportError:
    try:
        import mcp_server as _mcp  # type: ignore
    except ImportError:
        _mcp = None  # type: ignore


pytestmark = pytest.mark.skipif(_mcp is None, reason="backend.mcp_server not importable in this environment")


EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
LONG_DIGIT_RE = re.compile(r"\d{14,}")
FORBIDDEN_LITERALS = ("WS_EMAIL", "WS_PASSWORD", "session_id", "refresh_token")

TOOLS_TO_SCAN = (
    "get_profile",
    "get_portfolio",
    "get_xray",
    "get_concentration_warnings",
    "get_tax_loss_candidates",
)


def _resolve(tool_name: str):
    """Return the underlying callable for an MCP tool, or None if unavailable."""
    if _mcp is None:
        return None
    fn = getattr(_mcp, tool_name, None)
    if fn is None:
        return None
    # FastMCP wraps functions; unwrap to the underlying callable if needed.
    for attr in ("fn", "func", "__wrapped__"):
        inner = getattr(fn, attr, None)
        if callable(inner):
            fn = inner
            break
    return fn if callable(fn) else None


def _call_tool(fn):
    """Invoke a tool with no args; handle async + missing-session gracefully."""
    try:
        result = fn()
    except TypeError:
        # Some tools accept a single optional arg (e.g. account_id="").
        result = fn("")
    if inspect.iscoroutine(result):
        import asyncio

        # `asyncio.get_event_loop()` raises on Python 3.12+ when there is
        # no running loop in the main thread. `asyncio.run` creates a
        # fresh loop and tears it down per call - exactly what we need
        # for a one-shot test invocation.
        result = asyncio.run(result)
    return result


@pytest.mark.parametrize("tool_name", TOOLS_TO_SCAN)
def test_mcp_tool_does_not_leak_pii(tool_name: str) -> None:
    fn = _resolve(tool_name)
    if fn is None:
        pytest.skip(f"{tool_name} not found on mcp_server")

    try:
        result = _call_tool(fn)
    except RuntimeError as e:
        msg = str(e).lower()
        if "session" in msg or "auth" in msg or "login" in msg or "ws" in msg:
            pytest.skip(f"{tool_name}: no live WS session ({e})")
        raise
    except Exception as e:  # noqa: BLE001
        # Network / yfinance / WS connectivity hiccups: skip rather than fail.
        msg = str(e).lower()
        if any(k in msg for k in ("session", "auth", "login", "token", "connection", "timeout")):
            pytest.skip(f"{tool_name}: environmental failure ({e})")
        raise

    blob = str(result)

    emails = EMAIL_RE.findall(blob)
    assert not emails, f"{tool_name} leaked email-like substrings: {emails[:3]}"

    long_digits = LONG_DIGIT_RE.findall(blob)
    assert not long_digits, f"{tool_name} leaked 14+ digit sequences (possible account IDs): {long_digits[:3]}"

    for literal in FORBIDDEN_LITERALS:
        assert literal not in blob, f"{tool_name} leaked forbidden literal {literal!r}"
