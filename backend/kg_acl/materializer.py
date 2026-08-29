import json
from datetime import datetime, timezone
from typing import Any
from kg_acl.schemas import BoundarySpec
from app.db import Database
from kg_graphiti.service import GraphService

async def resolve_boundary_spec(graph: GraphService, boundary_spec: BoundarySpec) -> tuple[set[str], set[str], list[dict[str, Any]]]:
    """Resolves a boundary spec into a set of entity and edge UUIDs, and the raw edges list."""
    entity_uuids: set[str] = set()
    
    if boundary_spec.kind == "entity_list":
        entity_uuids = set(boundary_spec.entity_uuids) - set(boundary_spec.excluded_entity_uuids)
    elif boundary_spec.kind == "all_org":
        cypher = "MATCH (n:Entity) RETURN n.uuid AS uuid"
        rows = await graph.query(cypher)
        entity_uuids = {row["uuid"] for row in rows if row.get("uuid")}
        
    edges: list[dict[str, Any]] = []
    if entity_uuids:
        uuids_list = list(entity_uuids)
        chunk_size = 1000
        for i in range(0, len(uuids_list), chunk_size):
            chunk = uuids_list[i:i + chunk_size]
            cypher = (
                "MATCH (a:Entity)-[r]->(b:Entity) "
                "WHERE a.uuid IN $uuids AND b.uuid IN $uuids "
                "RETURN r.uuid AS uuid, a.uuid AS source, b.uuid AS target"
            )
            rows = await graph.query(cypher, uuids=chunk)
            edges.extend(rows)

    edge_uuids = {row["uuid"] for row in edges if row.get("uuid")}
    return entity_uuids, edge_uuids, edges

async def materialize_scope(
    db: Database,
    graph: GraphService,
    scope_id: str,
    boundary_spec: BoundarySpec,
    proposed_by: str,
    entity_drift_pct: float = 0.0,
    edge_drift_pct: float = 0.0
) -> str:
    """
    Materializes a boundary spec into a candidate scope version.
    Returns the ID of the new knowledge_scope_versions row.
    """
    import uuid
    version_id = f"ver-{uuid.uuid4()}"
    now = datetime.now(timezone.utc)
    
    # 1. Validate and resolve entity UUIDs based on boundary kind
    entity_uuids, edge_uuids, edges = await resolve_boundary_spec(graph, boundary_spec)
    
    # 3. Insert into scope_entities and scope_edges
    await db.execute("BEGIN")
    try:
        row = await db.fetch_one(
            "SELECT MAX(version_number) AS max_v FROM knowledge_scope_versions WHERE scope_id = ?",
            (scope_id,)
        )
        version_number = (row["max_v"] or 0) + 1
        
        for euuid in entity_uuids:
            await db.execute(
                """
                INSERT INTO scope_entities (scope_version_id, entity_uuid, included_at)
                VALUES (?, ?, ?)
                """,
                (version_id, euuid, now)
            )
            
        for e in edges:
            euuid = e["uuid"]
            await db.execute(
                """
                INSERT INTO scope_edges (scope_version_id, edge_uuid, source_entity_uuid, target_entity_uuid, access_tag, included_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (version_id, euuid, e["source"], e["target"], 'internal', now)
            )
            
        spec_json = boundary_spec.model_dump_json()
        await db.execute(
            """
            INSERT INTO knowledge_scope_versions (
                id, scope_id, version_number, status, boundary_kind,
                boundary_spec_json, member_entity_count, member_edge_count, proposed_by,
                created_at, materialized_at, entity_drift_pct, edge_drift_pct
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id, scope_id, version_number, 'candidate', boundary_spec.kind,
                spec_json, len(entity_uuids), len(edge_uuids), proposed_by,
                now, now, entity_drift_pct, edge_drift_pct
            )
        )
        
        await db.execute(
            "UPDATE knowledge_scopes SET review_status = 'candidate_pending' WHERE id = ?",
            (scope_id,)
        )
        
        await db.execute("COMMIT")
    except Exception:
        await db.execute("ROLLBACK")
        raise
        
    return version_id
