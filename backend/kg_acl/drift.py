import json
from datetime import datetime, timezone
from typing import Any

from app.db import Database
from kg_graphiti.service import GraphService
from kg_acl.schemas import BoundarySpec
from kg_acl.materializer import materialize_scope, resolve_boundary_spec

async def detect_and_propose_drift(db: Database, graph: GraphService) -> dict[str, Any]:
    """
    Checks all active scopes for drift (differences between current graph state 
    and the materialized scope version). If drift is found > 0%, materializes 
    a new candidate version.
    """
    scopes_processed = 0
    drifts_detected = 0
    
    rows = await db.fetch_all(
        "SELECT id, active_version_id FROM knowledge_scopes WHERE status = 'approved' AND active_version_id IS NOT NULL"
    )
    
    for row in rows:
        scope_id = row["id"]
        active_version_id = row["active_version_id"]
        scopes_processed += 1
        
        # 1. Fetch active version boundary spec and entities/edges
        v_row = await db.fetch_one(
            "SELECT boundary_kind, boundary_spec_json FROM knowledge_scope_versions WHERE id = ?",
            (active_version_id,)
        )
        if not v_row:
            continue
            
        spec_data = json.loads(v_row["boundary_spec_json"])
        spec_data["kind"] = v_row["boundary_kind"]
        
        # We need to construct a BoundarySpec (we'll just use the dict if we can't easily import all subclasses, 
        # but wait, we can just use Pydantic or the base schema parsing).
        # Actually, let's just do it manually for the two supported kinds to avoid complex imports here
        from kg_acl.schemas import EntityListBoundary, AllOrgBoundary
        kind = spec_data["kind"]
        if kind == "entity_list":
            boundary = EntityListBoundary(**spec_data)
        elif kind == "all_org":
            boundary = AllOrgBoundary(**spec_data)
        else:
            continue # not implemented
            
        # 2. Re-evaluate the boundary spec against the current graph
        new_entity_uuids, new_edge_uuids, _ = await resolve_boundary_spec(graph, boundary)
        
        # 3. Get old entities and edges
        old_ent_rows = await db.fetch_all("SELECT entity_uuid FROM scope_entities WHERE scope_version_id = ?", (active_version_id,))
        old_entity_uuids = {r["entity_uuid"] for r in old_ent_rows}
        
        old_edge_rows = await db.fetch_all("SELECT edge_uuid FROM scope_edges WHERE scope_version_id = ?", (active_version_id,))
        old_edge_uuids = {r["edge_uuid"] for r in old_edge_rows}
        
        # 4. Calculate drift
        ent_sym_diff = old_entity_uuids.symmetric_difference(new_entity_uuids)
        edge_sym_diff = old_edge_uuids.symmetric_difference(new_edge_uuids)
        
        ent_drift_pct = len(ent_sym_diff) / max(len(old_entity_uuids), 1) * 100.0
        edge_drift_pct = len(edge_sym_diff) / max(len(old_edge_uuids), 1) * 100.0
        
        if ent_drift_pct > 0 or edge_drift_pct > 0:
            drifts_detected += 1
            # 5. Drift detected, materialize a new candidate
            await materialize_scope(
                db=db,
                graph=graph,
                scope_id=scope_id,
                boundary_spec=boundary,
                proposed_by="system-drift-detector",
                entity_drift_pct=ent_drift_pct,
                edge_drift_pct=edge_drift_pct
            )
            
    return {
        "scopes_processed": scopes_processed,
        "drifts_detected": drifts_detected
    }
