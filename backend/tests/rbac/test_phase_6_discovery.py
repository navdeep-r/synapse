import pytest
from httpx import AsyncClient
from app.db import Database

@pytest.fixture
def mock_graph_for_discovery(monkeypatch):
    mock_entities = [
        {"uuid": "e1", "name": "Hub 1"},
        {"uuid": "e2", "name": "Spoke 1"},
        {"uuid": "e3", "name": "Spoke 2"},
        {"uuid": "e4", "name": "Isolated 1"},
    ]
    mock_edges = [
        {"source_uuid": "e1", "target_uuid": "e2"},
        {"source_uuid": "e2", "target_uuid": "e1"},
        {"source_uuid": "e1", "target_uuid": "e3"},
        {"source_uuid": "e3", "target_uuid": "e1"},
    ]
    
    async def mock_read_graph(state, graph):
        return mock_entities, mock_edges

    monkeypatch.setattr("kg_acl.discovery.read_graph", mock_read_graph)

@pytest.mark.asyncio
class TestPhase6Discovery:
    async def get_token(self, client: AsyncClient) -> str:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@test.local", "password": "Test1234!"}
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["access_token"]

    async def test_discovery_endpoints(self, client: AsyncClient, bootstrapped_db: Database, mock_graph_for_discovery):
        token = await self.get_token(client)
        headers = {"Authorization": f"Bearer {token}"}
        
        # 1. Generate proposals
        resp = await client.post("/api/v1/scopes/proposals/generate", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["count"] == 1
        
        # 2. List proposals
        resp = await client.get("/api/v1/scopes/proposals", headers=headers)
        assert resp.status_code == 200
        proposals = resp.json()
        assert len(proposals) == 1
        p = proposals[0]
        assert p["estimated_entity_count"] == 3
        assert p["estimated_edge_count"] == 4
        assert "Hub 1" in p["suggested_name"]
        proposal_id = p["id"]
        
        # 3. Approve proposal
        resp = await client.post(f"/api/v1/scopes/proposals/{proposal_id}/approve", headers=headers)
        assert resp.status_code == 200
        res = resp.json()
        assert res["status"] == "approved"
        scope_id = res["scope_id"]
        assert "boundary_spec" in res
        
        # Verify scope was created
        resp = await client.get(f"/api/v1/scopes/{scope_id}", headers=headers)
        assert resp.status_code == 200
        scope = resp.json()
        assert scope["status"] == "proposed"
        assert scope["discovery_origin"] == proposal_id
        
        # Verify proposal status updated
        resp = await client.get("/api/v1/scopes/proposals", headers=headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 0 # Only lists 'open' proposals
