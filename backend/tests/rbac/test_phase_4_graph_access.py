import pytest
from app.db import Database
from kg_acl.models import Principal
from kg_acl.authorized_graph import AuthorizedGraphRepository
import uuid
from datetime import datetime, timezone
from fastapi import HTTPException

class MockGraphService:
    def __init__(self):
        self._entities = []
        self._edges = []
        
    async def entities(self, group_ids):
        return self._entities
        
    async def edges(self, group_ids, include_invalid=True):
        return self._edges
        
    async def search(self, query, group_ids=None, limit=10):
        return self._edges
        
    async def entity_count(self, group_ids):
        return len(self._entities)
        
    async def episode_count(self, group_ids):
        return 0
        
    async def episode_uuids(self, group_ids):
        return set()
        
    async def query(self, cypher, group_id=None, **params):
        return [{"n": 1}]
        
    async def query_groups(self, cypher, group_ids, **params):
        return [{"n": 1}]

@pytest.mark.asyncio
class TestPhase4GraphAccess:
    async def test_supervisor_has_full_access(self, bootstrapped_db: Database):
        graph = MockGraphService()
        graph._entities = [{"uuid": "e1"}, {"uuid": "e2"}]
        graph._edges = [{"uuid": "edge1"}]
        
        supervisor = Principal(
            user_id="supervisor-1",
            email="sup@test.com",
            display_name="Supervisor",
            is_org_supervisor=True,
            active_scope_ids=[],
            active_scope_version_ids=[]
        )
        
        repo = AuthorizedGraphRepository(graph, bootstrapped_db, supervisor)
        
        # Test entities pass-through
        entities = await repo.entities(["group_1"])
        assert len(entities) == 2
        
        # Test query pass-through
        res = await repo.query("RETURN 1 AS n", "group_1")
        assert res[0]["n"] == 1
        
    async def test_non_supervisor_access_denied_for_raw_cypher(self, bootstrapped_db: Database):
        graph = MockGraphService()
        user = Principal(
            user_id="user-1",
            email="user@test.com",
            display_name="User",
            is_org_supervisor=False,
            active_scope_ids=[],
            active_scope_version_ids=[]
        )
        
        repo = AuthorizedGraphRepository(graph, bootstrapped_db, user)
        
        with pytest.raises(HTTPException) as exc:
            await repo.query("RETURN 1", "group_1")
        assert exc.value.status_code == 403
        
        with pytest.raises(HTTPException) as exc:
            await repo.query_groups("RETURN 1", ["group_1"])
        assert exc.value.status_code == 403
        
        with pytest.raises(HTTPException) as exc:
            await repo.episode_count(["group_1"])
        assert exc.value.status_code == 403

    async def test_non_supervisor_sees_only_scoped_entities_and_edges(self, bootstrapped_db: Database):
        graph = MockGraphService()
        
        entity1_uuid = str(uuid.uuid4())
        entity2_uuid = str(uuid.uuid4())
        edge_uuid = str(uuid.uuid4())
        
        # Mock graph has all entities and edges
        graph._entities = [
            {"uuid": entity1_uuid, "name": "Entity 1"},
            {"uuid": entity2_uuid, "name": "Entity 2"}
        ]
        
        graph._edges = [
            {"uuid": edge_uuid}
        ]
        
        scope_version_id = f"ver-{uuid.uuid4()}"
        user = Principal(
            user_id="user-2",
            email="user2@test.com",
            display_name="User 2",
            is_org_supervisor=False,
            active_scope_ids=["scope-1"],
            active_scope_version_ids=[scope_version_id]
        )
        
        # Materialize entity1 into the scope
        now = datetime.now(timezone.utc)
        await bootstrapped_db.execute(
            "INSERT INTO scope_entities (scope_version_id, entity_uuid, included_at) VALUES (?, ?, ?)",
            (scope_version_id, entity1_uuid, now)
        )
        
        repo = AuthorizedGraphRepository(graph, bootstrapped_db, user)
        
        # Test Entities filtering
        scoped_entities = await repo.entities(["group_1"])
        assert len(scoped_entities) == 1
        assert scoped_entities[0]["uuid"] == entity1_uuid
        
        # Test Edges filtering
        scoped_edges = await repo.edges(["group_1"])
        assert len(scoped_edges) == 0
        
        # Test Search filtering
        scoped_search = await repo.search("query")
        assert len(scoped_search) == 0
        
        # Now add the edge to the scope
        await bootstrapped_db.execute(
            "INSERT INTO scope_edges (scope_version_id, edge_uuid, included_at) VALUES (?, ?, ?)",
            (scope_version_id, edge_uuid, now)
        )
        
        scoped_edges_after = await repo.edges(["group_1"])
        assert len(scoped_edges_after) == 1
        assert scoped_edges_after[0]["uuid"] == edge_uuid
        
        scoped_search_after = await repo.search("query")
        assert len(scoped_search_after) == 1
        assert scoped_search_after[0]["uuid"] == edge_uuid
