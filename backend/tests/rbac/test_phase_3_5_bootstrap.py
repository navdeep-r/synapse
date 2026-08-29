import pytest
from httpx import AsyncClient
from app.db import Database
from app.config import get_settings
import uuid
from datetime import datetime, timezone
from kg_acl.bootstrap import _hash_password

@pytest.mark.asyncio
class TestPhase35Bootstrap:
    async def get_token(self, client: AsyncClient) -> str:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@test.local", "password": "Test1234!"}
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["access_token"]

    async def test_bootstrap_scope_injected(self, client: AsyncClient, bootstrapped_db: Database):
        token = await self.get_token(client)
    
        # Override settings for this test to enable rbac_transition_mode
        client._transport.app.state.synapse.settings.rbac_transition_mode = True
    
        from fastapi import APIRouter, Depends
        from kg_acl.principal import require_principal
        from kg_acl.models import Principal
    
        router = APIRouter()
        @router.get("/api/v1/test_me")
        def get_me(principal: Principal = Depends(require_principal)):
            return {"active_scope_ids": principal.active_scope_ids}
    
        client._transport.app.include_router(router)
    
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.get("/api/v1/test_me", headers=headers)
        assert resp.status_code == 200, resp.text
    
        data = resp.json()
        assert "scope-bootstrap-all" in data["active_scope_ids"]
    
        # Now let's test a non-supervisor user with no scopes
        dummy_user_id = f"user-{uuid.uuid4()}"
        now = datetime.now(timezone.utc)
        pwd_hash = _hash_password("dummy")
        
        await bootstrapped_db.execute(
            "INSERT INTO users (id, email, display_name, password_hash, auth_provider, is_active, token_version, created_at, updated_at) VALUES (?, ?, 'Dummy User', ?, 'local', 1, 1, ?, ?)",
            (dummy_user_id, "dummy@test.local", pwd_hash, now, now)
        )
    

    
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "dummy@test.local", "password": "dummy"}
        )
        assert resp.status_code == 200, resp.text
        dummy_token = resp.json()["access_token"]
    
        headers = {"Authorization": f"Bearer {dummy_token}"}
        resp = await client.get("/api/v1/test_me", headers=headers)
        assert resp.status_code == 200, resp.text
    
        data = resp.json()
        print("DUMMY USER DATA:", data)
        assert "scope-bootstrap-all" in data["active_scope_ids"]
