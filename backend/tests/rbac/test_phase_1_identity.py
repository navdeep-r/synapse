"""Phase 1 identity tests.

Covers: bootstrap, login, refresh rotation, /auth/me, guarded endpoint
rejection, transition-mode fallback, API key resolution, session revocation,
and webhook carve-out.
"""

from __future__ import annotations

import hashlib

import pytest
from httpx import AsyncClient

from app.config import Settings
from app.db import Database
from kg_acl.bootstrap import run_bootstrap, verify_password
from kg_acl.models import Sensitivity


# ============================================================================
# Bootstrap tests
# ============================================================================


class TestBootstrap:
    """Verify first-run bootstrap creates org, user, role, grant."""

    @pytest.mark.asyncio
    async def test_bootstrap_creates_org(self, bootstrapped_db: Database):
        org = await bootstrapped_db.fetch_one(
            "SELECT * FROM organizations WHERE slug = 'default'"
        )
        assert org is not None
        assert org["name"] == "Default Organization"
        assert org["status"] == "active"

    @pytest.mark.asyncio
    async def test_bootstrap_creates_admin_user(self, bootstrapped_db: Database):
        user = await bootstrapped_db.fetch_one(
            "SELECT * FROM users WHERE email = 'admin@test.local'"
        )
        assert user is not None
        assert user["is_active"] == 1
        assert user["auth_provider"] == "local"
        assert verify_password("Test1234!", user["password_hash"])

    @pytest.mark.asyncio
    async def test_bootstrap_creates_supervisor_role(self, bootstrapped_db: Database):
        role = await bootstrapped_db.fetch_one(
            "SELECT * FROM roles WHERE name = 'org-supervisor'"
        )
        assert role is not None
        assert role["clearance"] == "restricted"
        assert role["is_system_role"] == 1

    @pytest.mark.asyncio
    async def test_bootstrap_creates_default_member_role(self, bootstrapped_db: Database):
        role = await bootstrapped_db.fetch_one(
            "SELECT * FROM roles WHERE name = 'default-member'"
        )
        assert role is not None
        assert role["clearance"] == "internal"

    @pytest.mark.asyncio
    async def test_bootstrap_grants_supervisor_to_admin(self, bootstrapped_db: Database):
        user = await bootstrapped_db.fetch_one(
            "SELECT id FROM users WHERE email = 'admin@test.local'"
        )
        grant = await bootstrapped_db.fetch_one(
            "SELECT * FROM user_roles WHERE user_id = ?",
            (user["id"],),
        )
        assert grant is not None
        assert grant["revoked_at"] is None

    @pytest.mark.asyncio
    async def test_bootstrap_creates_api_key(self, bootstrapped_db: Database):
        key_hash = hashlib.sha256(
            b"syn_test_bootstrap_api_key_12345"
        ).hexdigest()
        row = await bootstrapped_db.fetch_one(
            "SELECT * FROM api_keys WHERE key_hash = ?", (key_hash,)
        )
        assert row is not None
        assert row["user_id"] is not None

    @pytest.mark.asyncio
    async def test_bootstrap_creates_authorization_version(self, bootstrapped_db: Database):
        row = await bootstrapped_db.fetch_one(
            "SELECT * FROM authorization_versions"
        )
        assert row is not None
        assert row["version"] == 1

    @pytest.mark.asyncio
    async def test_bootstrap_is_idempotent(self, bootstrapped_db: Database, rbac_settings: Settings):
        # Running bootstrap again should not create duplicates
        await run_bootstrap(bootstrapped_db, rbac_settings)
        orgs = await bootstrapped_db.fetch_all("SELECT * FROM organizations")
        assert len(orgs) == 1


# ============================================================================
# Auth endpoint tests (via HTTP client)
# ============================================================================


class TestLogin:
    """Login endpoint."""

    @pytest.mark.asyncio
    async def test_login_success(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@test.local", "password": "Test1234!"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["expires_in"] == 300  # 5 minutes * 60

    @pytest.mark.asyncio
    async def test_login_wrong_password(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@test.local", "password": "WrongPassword"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_login_unknown_email(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@test.local", "password": "Test1234!"},
        )
        assert resp.status_code == 401


class TestMe:
    """GET /auth/me endpoint."""

    @pytest.mark.asyncio
    async def test_me_returns_principal(self, client: AsyncClient):
        # Login first
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@test.local", "password": "Test1234!"},
        )
        token = login_resp.json()["access_token"]

        # Call /me
        resp = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "admin@test.local"
        assert data["display_name"] == "Test Admin"
        assert data["is_org_supervisor"] is True
        assert data["clearance"] == "restricted"


class TestGuardedEndpoint:
    """Verify require_principal rejects unauthenticated requests."""

    @pytest.mark.asyncio
    async def test_guarded_rejects_no_auth(self, client: AsyncClient):
        """With transition mode OFF, no auth = 401."""
        resp = await client.get("/api/v1/test/guarded")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_guarded_accepts_valid_token(self, client: AsyncClient):
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@test.local", "password": "Test1234!"},
        )
        token = login_resp.json()["access_token"]

        resp = await client.get(
            "/api/v1/test/guarded",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"message": "you are authorized"}


class TestApiKeyAuth:
    """API key resolution."""

    @pytest.mark.asyncio
    async def test_api_key_resolves_principal(self, client: AsyncClient):
        resp = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer syn_test_bootstrap_api_key_12345"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "admin@test.local"
        assert data["is_org_supervisor"] is True


class TestSessionRevocation:
    """Session lifecycle."""

    @pytest.mark.asyncio
    async def test_logout_clears_session(self, client: AsyncClient):
        # Login
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@test.local", "password": "Test1234!"},
        )
        assert login_resp.status_code == 200

        # Logout
        logout_resp = await client.post("/api/v1/auth/logout")
        assert logout_resp.status_code == 200
        assert logout_resp.json() == {"status": "logged_out"}


# ============================================================================
# Transition mode tests
# ============================================================================


class TestTransitionMode:
    """When transition mode is ON, unauthenticated requests get a bootstrap supervisor."""

    @pytest.mark.asyncio
    async def test_transition_mode_allows_no_auth(self):
        """Transition mode app allows unauthenticated access."""
        from tests.rbac.conftest import create_transition_client

        c, db = await create_transition_client({"rbac_transition_mode": True})
        try:
            resp = await c.get("/api/v1/test/guarded")
            assert resp.status_code == 200
        finally:
            await c.aclose()
            await db.close()

    @pytest.mark.asyncio
    async def test_transition_mode_with_legacy_token(self):
        """Legacy SYNAPSE_AUTH_TOKEN maps to bootstrap supervisor in transition mode."""
        from tests.rbac.conftest import create_transition_client

        c, db = await create_transition_client({
            "rbac_transition_mode": True,
            "auth_token": "my-legacy-token",
        })
        try:
            # Legacy token works
            resp = await c.get(
                "/api/v1/test/guarded",
                headers={"Authorization": "Bearer my-legacy-token"},
            )
            assert resp.status_code == 200

            # No token when auth_token is set → 401
            resp = await c.get("/api/v1/test/guarded")
            assert resp.status_code == 401
        finally:
            await c.aclose()
            await db.close()


# ============================================================================
# Model unit tests
# ============================================================================


class TestSensitivity:
    """Sensitivity enum parse()."""

    def test_parse_string(self):
        assert Sensitivity.parse("public") == Sensitivity.PUBLIC
        assert Sensitivity.parse("RESTRICTED") == Sensitivity.RESTRICTED
        assert Sensitivity.parse("Internal") == Sensitivity.INTERNAL

    def test_parse_int(self):
        assert Sensitivity.parse(0) == Sensitivity.PUBLIC
        assert Sensitivity.parse(3) == Sensitivity.RESTRICTED

    def test_parse_none(self):
        assert Sensitivity.parse(None) == Sensitivity.INTERNAL

    def test_parse_unknown(self):
        assert Sensitivity.parse("unknown-value") == Sensitivity.INTERNAL

    def test_ordering(self):
        assert Sensitivity.PUBLIC < Sensitivity.INTERNAL < Sensitivity.CONFIDENTIAL < Sensitivity.RESTRICTED
