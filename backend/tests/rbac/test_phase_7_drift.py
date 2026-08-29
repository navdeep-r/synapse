import pytest
from httpx import AsyncClient

from kg_acl.models import Principal, Sensitivity
from app.state import AppState
from kg_acl.drift import detect_and_propose_drift
from kg_acl.schemas import AllOrgBoundary

@pytest.fixture
def mock_graph_for_drift(monkeypatch):
    from unittest.mock import AsyncMock
    # Initial state
    mock_entities = [
        {"uuid": "e1", "name": "Hub 1"},
        {"uuid": "e2", "name": "Spoke 1"},
    ]
    mock_edges = [
        {"uuid": "edge1", "source": "e1", "target": "e2"},
    ]
    
    # We will track how many times query is called to simulate drift
    state = {"drifted": False}
    
    async def mock_query(cypher, **kwargs):
        if "RETURN n.uuid AS uuid" in cypher:
            if not state["drifted"]:
                # First materialization
                return mock_entities
            else:
                # Drift detected - new entity added
                return mock_entities + [{"uuid": "e3", "name": "New Spoke"}]
                
        if "RETURN r.uuid" in cypher:
            if not state["drifted"]:
                return mock_edges
            else:
                return mock_edges + [{"uuid": "edge2", "source": "e1", "target": "e3"}]
        return []

    class MockGraphService:
        def trigger_drift(self):
            state["drifted"] = True
            
        async def query(self, cypher, **kwargs):
            return await mock_query(cypher, **kwargs)

    return MockGraphService()

from app.db import Database

@pytest.mark.asyncio
async def test_drift_detection(bootstrapped_db: Database, mock_graph_for_drift) -> None:
    # 1. Setup - we mock the graph entirely
    
    # Create an initial scope and materialize it
    from kg_acl.materializer import materialize_scope
    
    # Delete bootstrap scope to avoid it triggering drift due to mock differences
    await bootstrapped_db.execute("DELETE FROM knowledge_scopes WHERE id = 'scope-bootstrap-all'")
    
    scope_id = "scope-drift-test"
    await bootstrapped_db.execute(
        "INSERT INTO knowledge_scopes (id, name, status, review_status, created_at, updated_at) VALUES (?, ?, 'approved', 'current', '', '')",
        (scope_id, "Test Scope")
    )
    
    boundary = AllOrgBoundary(kind="all_org")
    version_id = await materialize_scope(
        db=bootstrapped_db,
        graph=mock_graph_for_drift,
        scope_id=scope_id,
        boundary_spec=boundary,
        proposed_by="test-user"
    )
    
    # Approve it to make it active
    await bootstrapped_db.execute("UPDATE knowledge_scope_versions SET status = 'approved' WHERE id = ?", (version_id,))
    await bootstrapped_db.execute("UPDATE knowledge_scopes SET active_version_id = ? WHERE id = ?", (version_id, scope_id))
    
    # 2. Run drift detection immediately - should be 0 drift
    res = await detect_and_propose_drift(bootstrapped_db, mock_graph_for_drift)
    assert res["scopes_processed"] >= 1
    assert res["drifts_detected"] == 0
    
    # 3. Simulate drift (the mock graph returns more entities on the next call)
    mock_graph_for_drift.trigger_drift()
    res = await detect_and_propose_drift(bootstrapped_db, mock_graph_for_drift)
    assert res["scopes_processed"] >= 1
    assert res["drifts_detected"] >= 1
    
    # Verify candidate version was created and scope marked as needs review
    row = await bootstrapped_db.fetch_one("SELECT review_status FROM knowledge_scopes WHERE id = ?", (scope_id,))
    assert row["review_status"] == "candidate_pending"
    
    candidates = await bootstrapped_db.fetch_all("SELECT * FROM knowledge_scope_versions WHERE scope_id = ? AND status = 'candidate'", (scope_id,))
    assert len(candidates) == 1
    candidate = candidates[0]
    
    assert candidate["entity_drift_pct"] > 0
    assert candidate["edge_drift_pct"] > 0
