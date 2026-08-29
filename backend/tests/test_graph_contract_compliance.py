import pytest

def test_all_nodes_have_complete_contract(client):
    response = client.get("/api/v1/graph/topology")
    assert response.status_code == 200
    
    nodes = response.json()["nodes"]
    assert len(nodes) > 0, "No nodes returned from graph"
    
    for node in nodes:
        # Classification
        assert node["classification"]["domain"] is not None
        assert node["classification"]["category"] is not None
        assert node["classification"]["type"] is not None
        
        # Identity
        assert node["identity"]["entity_type"] != "Entity"
        assert node["identity"]["subtype"] is not None
        
        # Source metadata
        sm = node["source_metadata"]
        populated = [k for k, v in sm.items() if v is not None]
        assert len(populated) == 1, f"Expected exactly one source_metadata, got {populated} on node {node['identity']}"
        assert len(sm[populated[0]]) >= 3
        
        # Provenance
        assert len(node["provenance"]["source_ids"]) > 0
        
        # Source
        assert node["source"]["external_id"] is not None
        assert node["source"]["source_type"] != "unknown"
