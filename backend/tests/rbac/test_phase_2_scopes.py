import pytest
from httpx import AsyncClient
from app.db import Database

@pytest.mark.asyncio
class TestPhase2Scopes:
    async def get_token(self, client: AsyncClient) -> str:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@test.local", "password": "Test1234!"}
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["access_token"]

    async def test_scope_crud(self, client: AsyncClient, bootstrapped_db: Database):
        token = await self.get_token(client)
        headers = {"Authorization": f"Bearer {token}"}
        
        # 1. Create a scope
        resp = await client.post(
            "/api/v1/scopes",
            json={"name": "Payment Platform", "description": "All payment related entities", "sensitivity": "internal"},
            headers=headers
        )
        assert resp.status_code == 200, resp.text
        scope = resp.json()
        assert scope["name"] == "Payment Platform"
        assert scope["status"] == "proposed"
        scope_id = scope["id"]
        
        # 2. List scopes
        resp = await client.get("/api/v1/scopes", headers=headers)
        assert resp.status_code == 200
        scopes = resp.json()
        assert len(scopes) >= 1
        assert any(s["id"] == scope_id for s in scopes)
        
        # 3. Update scope
        resp = await client.patch(
            f"/api/v1/scopes/{scope_id}",
            json={"sensitivity": "confidential"},
            headers=headers
        )
        assert resp.status_code == 200
        assert resp.json()["sensitivity"] == "confidential"
        
        # 4. Materialize scope
        resp = await client.post(
            f"/api/v1/scopes/{scope_id}/materialize",
            json={
                "boundary_kind": "all_org",
                "boundary_spec": {}
            },
            headers=headers
        )
        assert resp.status_code == 200
        version_id = resp.json()["version_id"]
        
        # 5. List versions
        resp = await client.get(f"/api/v1/scopes/{scope_id}/versions", headers=headers)
        assert resp.status_code == 200
        versions = resp.json()
        assert len(versions) == 1
        assert versions[0]["id"] == version_id
        assert versions[0]["status"] == "candidate"
        
        # 6. Approve version (bootstrap admin has org-supervisor role)
        resp = await client.post(f"/api/v1/scopes/{scope_id}/versions/{version_id}/approve", headers=headers)
        assert resp.status_code == 200
        
        # 7. Check scope is updated
        resp = await client.get(f"/api/v1/scopes/{scope_id}", headers=headers)
        assert resp.status_code == 200
        scope_updated = resp.json()
        assert scope_updated["status"] == "approved"
        assert scope_updated["active_version_id"] == version_id
        
        # 8. Diff versions (stub)
        resp = await client.get(f"/api/v1/scopes/{scope_id}/versions/{version_id}/diff", headers=headers)
        assert resp.status_code == 200
        
        # 9. Deprecate scope
        resp = await client.post(f"/api/v1/scopes/{scope_id}/deprecate", headers=headers)
        assert resp.status_code == 200
        resp = await client.get(f"/api/v1/scopes/{scope_id}", headers=headers)
        assert resp.json()["status"] == "deprecated"
