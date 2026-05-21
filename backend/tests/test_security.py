"""Integration tests for session cookie + rate limit + tenant namespacing.

No external services hit. Uses FastAPI TestClient with mocked wealthsimple.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.security import (
    SESSION_COOKIE_NAME,
    RateLimiter,
    get_rate_limiter,
    session_from_request,
)


@pytest.fixture
def client():
    # Reset the global limiter between tests so counts don't leak.
    get_rate_limiter().__init__()
    # Import here so configure_logging() at module load runs once.
    from main import app
    return TestClient(app)


# ── Rate limiter unit tests ──────────────────────────────────────────────────

class TestRateLimiter:
    def test_allows_up_to_max_hits(self):
        rl = RateLimiter()
        for i in range(5):
            allowed, remaining, retry = rl.hit("x", "1.1.1.1",
                                               max_hits=5, window_seconds=60)
            assert allowed, f"hit {i} should be allowed"
            assert remaining == 4 - i

    def test_blocks_after_max(self):
        rl = RateLimiter()
        for _ in range(5):
            rl.hit("x", "1.1.1.1", max_hits=5, window_seconds=60)
        allowed, _, retry = rl.hit("x", "1.1.1.1",
                                   max_hits=5, window_seconds=60)
        assert not allowed
        assert retry > 0

    def test_separate_identities_have_independent_buckets(self):
        rl = RateLimiter()
        for _ in range(5):
            rl.hit("x", "ip1", max_hits=5, window_seconds=60)
        # ip2 should still have full budget
        allowed, remaining, _ = rl.hit("x", "ip2",
                                       max_hits=5, window_seconds=60)
        assert allowed
        assert remaining == 4

    def test_separate_scopes_have_independent_buckets(self):
        rl = RateLimiter()
        for _ in range(5):
            rl.hit("scope_a", "ip", max_hits=5, window_seconds=60)
        allowed, _, _ = rl.hit("scope_b", "ip",
                               max_hits=5, window_seconds=60)
        assert allowed


# ── Login endpoint: rate limit + cookie ──────────────────────────────────────

class TestLoginRateLimit:
    def test_returns_429_after_ip_budget_exhausted(self, client):
        with patch("app.api.ws.wealthsimple.login") as mock_login:
            mock_login.side_effect = ValueError("bad creds")
            # 10 attempts allowed per IP/5min
            for i in range(10):
                r = client.post("/ws/login",
                                json={"email": f"u{i}@x.com", "password": "p"})
                assert r.status_code == 401, f"attempt {i}: {r.status_code}"
            # 11th should be 429
            r = client.post("/ws/login",
                            json={"email": "u11@x.com", "password": "p"})
            assert r.status_code == 429
            body = r.json()
            assert "rate_limit_exceeded" in json.dumps(body)
            assert r.headers.get("Retry-After")

    def test_email_budget_blocks_targeted_account(self, client):
        with patch("app.api.ws.wealthsimple.login") as mock_login:
            mock_login.side_effect = ValueError("bad creds")
            # 5 attempts allowed per email/15min
            for _ in range(5):
                r = client.post("/ws/login",
                                json={"email": "target@x.com", "password": "p"})
                assert r.status_code == 401
            # 6th attempt on same email should be 429 even from same IP
            r = client.post("/ws/login",
                            json={"email": "target@x.com", "password": "p"})
            assert r.status_code == 429


class TestSessionCookie:
    def test_cookie_set_on_successful_login(self, client):
        with patch("app.api.ws.wealthsimple.login") as mock_login:
            mock_login.return_value = {
                "session_id": "sess-abc-123",
                "needs_otp": False,
                "profile": None,
            }
            r = client.post("/ws/login",
                            json={"email": "ok@x.com", "password": "p"})
            assert r.status_code == 200
            assert SESSION_COOKIE_NAME in r.cookies
            assert r.cookies[SESSION_COOKIE_NAME] == "sess-abc-123"

    def test_cookie_not_set_when_otp_needed(self, client):
        with patch("app.api.ws.wealthsimple.login") as mock_login:
            mock_login.return_value = {
                "session_id": "sess-otp",
                "needs_otp": True,
            }
            r = client.post("/ws/login",
                            json={"email": "otp@x.com", "password": "p"})
            assert r.status_code == 200
            # Pre-OTP session should not establish auth cookie
            assert SESSION_COOKIE_NAME not in r.cookies

    def test_logout_clears_cookie(self, client):
        r = client.post("/ws/logout")
        assert r.status_code == 200
        # Set-Cookie with empty value or expired Max-Age clears
        cookie_header = r.headers.get("set-cookie", "")
        assert SESSION_COOKIE_NAME in cookie_header


# ── session_from_request: cookie precedence + fallback ───────────────────────

class TestSessionResolution:
    def test_cookie_takes_precedence_over_query(self):
        from starlette.requests import Request
        scope = {
            "type": "http", "headers": [
                (b"cookie", f"{SESSION_COOKIE_NAME}=cookie-sid".encode()),
            ],
        }
        req = Request(scope)
        assert session_from_request(req, "query-sid") == "cookie-sid"

    def test_falls_back_to_query_when_no_cookie(self):
        from starlette.requests import Request
        scope = {"type": "http", "headers": []}
        req = Request(scope)
        assert session_from_request(req, "query-sid") == "query-sid"

    def test_returns_none_when_neither_set(self):
        from starlette.requests import Request
        scope = {"type": "http", "headers": []}
        req = Request(scope)
        assert session_from_request(req, None) is None


# ── Tenant-namespaced snapshots ──────────────────────────────────────────────

class TestTenantNamespacing:
    def test_tenant_snapshots_isolated_from_global(self, tmp_path, monkeypatch):
        # Redirect snapshot dir so test doesn't pollute real cache
        from app.services import skill_runner
        monkeypatch.setattr(skill_runner, "_SNAP_DIR", tmp_path)
        snap_global = {"skill": "portfolio-health", "status": "ok",
                       "summary": {"score": 99}, "expires_at": 0,
                       "computed_at": "t", "ttl_minutes": 30,
                       "confidence_source": "experimental",
                       "actionable": [], "alerts": [], "error": None}
        snap_tenant_a = dict(snap_global, summary={"score": 50})
        snap_tenant_b = dict(snap_global, summary={"score": 10})

        skill_runner.write_snapshot(snap_global, tenant_id=None)
        skill_runner.write_snapshot(snap_tenant_a, tenant_id="user-a")
        skill_runner.write_snapshot(snap_tenant_b, tenant_id="user-b")

        # Each tenant reads its own
        a = skill_runner.read_snapshot("portfolio-health", tenant_id="user-a")
        b = skill_runner.read_snapshot("portfolio-health", tenant_id="user-b")
        g = skill_runner.read_snapshot("portfolio-health", tenant_id=None)
        assert a["summary"]["score"] == 50
        assert b["summary"]["score"] == 10
        assert g["summary"]["score"] == 99

    def test_tenant_path_is_hashed(self, tmp_path, monkeypatch):
        from app.services import skill_runner
        monkeypatch.setattr(skill_runner, "_SNAP_DIR", tmp_path)
        d = skill_runner._tenant_dir("session-xyz-pii")
        # Path must NOT contain raw tenant id
        assert "session-xyz-pii" not in str(d)
        assert (tmp_path / "tenants").exists()

    def test_read_falls_back_to_global_on_first_run(self, tmp_path, monkeypatch):
        from app.services import skill_runner
        monkeypatch.setattr(skill_runner, "_SNAP_DIR", tmp_path)
        global_snap = {"skill": "macro-impact", "status": "ok",
                       "summary": {}, "expires_at": 0, "computed_at": "t",
                       "ttl_minutes": 60, "confidence_source": "experimental",
                       "actionable": [], "alerts": [], "error": None}
        skill_runner.write_snapshot(global_snap, tenant_id=None)
        # New tenant with no snapshot should still see global as fallback
        out = skill_runner.read_snapshot("macro-impact", tenant_id="new-user")
        assert out is not None
        assert out["skill"] == "macro-impact"


# ── Skills REST endpoints: cookie-driven tenant resolution ───────────────────

class TestSkillsEndpointsTenantResolution:
    def test_snapshots_endpoint_uses_cookie_tenant(self, client, tmp_path, monkeypatch):
        from app.services import skill_runner
        monkeypatch.setattr(skill_runner, "_SNAP_DIR", tmp_path)
        tenant_snap = {"skill": "stock-analysis", "status": "ok",
                       "summary": {"unique": "tenant-data"},
                       "expires_at": 0, "computed_at": "t",
                       "ttl_minutes": 15, "confidence_source": "experimental",
                       "actionable": [], "alerts": [], "error": None}
        skill_runner.write_snapshot(tenant_snap, tenant_id="cookie-sid")
        r = client.get("/skills/snapshots",
                       cookies={SESSION_COOKIE_NAME: "cookie-sid"})
        assert r.status_code == 200
        body = r.json()
        assert any(s["summary"].get("unique") == "tenant-data"
                   for s in body["snapshots"])

    def test_refresh_without_session_returns_401(self, client):
        r = client.post("/skills/refresh")
        assert r.status_code == 401
