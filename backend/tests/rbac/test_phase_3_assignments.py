import pytest
from httpx import AsyncClient
from app.db import Database
from datetime import datetime, timezone
import uuid

@pytest.mark.asyncio
class TestPhase3Assignments:
    async def get_token(self, client: AsyncClient) -> str:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@test.local", "password": "Test1234!"}
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["access_token"]

    async def test_assignments_and_resolver(self, client: AsyncClient, bootstrapped_db: Database):
        token = await self.get_token(client)
        headers = {"Authorization": f"Bearer {token}"}
        
        # 1. Create a scope
        resp = await client.post(
            "/api/v1/scopes",
            json={"name": "Engineering Scope", "description": "Eng", "sensitivity": "internal"},
            headers=headers
        )
        assert resp.status_code == 200
        scope_id = resp.json()["id"]

        # 2. Materialize and approve to make it active
        resp = await client.post(
            f"/api/v1/scopes/{scope_id}/materialize",
            json={"boundary_kind": "all_org", "boundary_spec": {}},
            headers=headers
        )
        assert resp.status_code == 200
        version_id = resp.json()["version_id"]

        resp = await client.post(f"/api/v1/scopes/{scope_id}/versions/{version_id}/approve", headers=headers)
        assert resp.status_code == 200
        
        # 3. Create a team
        resp = await client.post(
            "/api/v1/teams",
            json={"name": "Engineering Team"},
            headers=headers
        )
        assert resp.status_code == 200
        team_id = resp.json()["id"]
        
        dummy_user_id = f"user-{uuid.uuid4()}"
        now = datetime.now(timezone.utc)
        await bootstrapped_db.execute(
            "INSERT INTO users (id, email, password_hash, is_active, created_at, updated_at) VALUES (?, ?, 'dummy', 1, ?, ?)",
            (dummy_user_id, "eng@test.local", now, now)
        )
        await bootstrapped_db.execute(
            "INSERT INTO team_members (id, team_id, user_id, joined_at) VALUES (?, ?, ?, ?)",
            (f"tm-{uuid.uuid4()}", team_id, dummy_user_id, now)
        )
        
        # 4. Assign scope to team
        resp = await client.post(
            f"/api/v1/scopes/{scope_id}/assignments",
            json={"assignee_kind": "team", "assignee_id": team_id},
            headers=headers
        )
        assert resp.status_code == 200
        assignment_id = resp.json()["id"]
        
        # 5. Check effective scopes for dummy user
        from kg_acl.resolver import resolve_effective_scopes
        scope_ids, version_ids = await resolve_effective_scopes(
            bootstrapped_db,
            user_id=dummy_user_id,
            role_ids=[],
            is_supervisor=False
        )
        assert scope_id in scope_ids
        assert version_id in version_ids
        
        # 6. Revoke assignment
        resp = await client.delete(f"/api/v1/scopes/{scope_id}/assignments/{assignment_id}", headers=headers)
        assert resp.status_code == 200
        
        # 7. Check effective scopes again
        scope_ids, version_ids = await resolve_effective_scopes(
            bootstrapped_db,
            user_id=dummy_user_id,
            role_ids=[],
            is_supervisor=False
        )
        assert scope_id not in scope_ids
