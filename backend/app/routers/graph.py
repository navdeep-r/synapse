"""Graph reads for the Viewer screen.

Contract note: `ViewerScreen` drops the whole topology payload unless *both*
`nodes` and `links` are present (`if (data.nodes && data.links)`), so both keys
are always returned even when empty.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.services import (
    build_provenance_index,
    edge_tag_map,
    episodes_of,
    read_graph,
)
from app.state import AppState, get_state
from kg_acl.authorized_graph import AuthorizedGraphRepository
from kg_acl.principal import get_authorized_graph

router = APIRouter()


def _node_type(entity: dict) -> str:
    labels = entity.get("labels") or []
    if isinstance(labels, str):
        labels = [labels]
    for label in labels:
        if label and label != "Entity":
            return str(label)
    return "Entity"


from typing import Optional

@router.get("/graph/topology")
async def topology(
    state: AppState = Depends(get_state), 
    graph: AuthorizedGraphRepository = Depends(get_authorized_graph),
    scope_version_id: Optional[str] = None
) -> dict:
    entities, edges = await read_graph(state, graph)
    
    if scope_version_id and scope_version_id.strip() and scope_version_id.strip() != "all":
        version_ids = [v.strip() for v in scope_version_id.split(",") if v.strip()]
        if version_ids:
            placeholders = ",".join("?" for _ in version_ids)
            all_org_check = await state.db.fetch_one(
                f"SELECT 1 FROM knowledge_scope_versions WHERE id IN ({placeholders}) AND boundary_kind = 'all_org' LIMIT 1",
                version_ids
            )
            if not all_org_check:
                ent_rows = await state.db.fetch_all(
                    f"SELECT entity_uuid FROM scope_entities WHERE scope_version_id IN ({placeholders})",
                    version_ids
                )
                allowed_entities = {r["entity_uuid"] for r in ent_rows}
                
                edge_rows = await state.db.fetch_all(
                    f"SELECT edge_uuid FROM scope_edges WHERE scope_version_id IN ({placeholders})",
                    version_ids
                )
                allowed_edges = {r["edge_uuid"] for r in edge_rows}
                
                entities = [e for e in entities if e.get("uuid") in allowed_entities]
                edges = [e for e in edges if e.get("uuid") in allowed_edges]

    degree: dict[str, int] = {}
    for edge in edges:
        for key in ("source_uuid", "target_uuid"):
            uuid = edge.get(key)
            if uuid:
                degree[uuid] = degree.get(uuid, 0) + 1

    from kg_serializer.canonical import serialize_node
    
    nodes = []
    for entity in entities:
        if entity.get("uuid"):
            cnode = serialize_node(entity)
            node_dict = cnode.model_dump(mode="json")
            # For backward compatibility in frontend renderer
            node_dict["val"] = 1 + degree.get(entity["uuid"], 0)
            node_dict["type"] = cnode.type
            node_dict["name"] = cnode.name
            node_dict["group_id"] = entity.get("group_id")
            node_dict["source_id"] = cnode.source.source_id
            node_dict["source_type"] = cnode.source.source_type
            node_dict["need_attention"] = cnode.need_attention
            node_dict["summary"] = cnode.semantic.summary
            nodes.append(node_dict)

    node_ids = {node["id"] for node in nodes}
    links = [
        {
            "source": edge["source_uuid"],
            "target": edge["target_uuid"],
            "relation_type": edge.get("relation_type"),
            "group_id": edge.get("group_id"),
            "source_id": edge.get("source_id"),
            "source_type": edge.get("source_type"),
        }
        for edge in edges
        if edge.get("source_uuid") in node_ids and edge.get("target_uuid") in node_ids
        # Superseded facts are hidden from the default topology view.
        and not edge.get("invalid_at")
    ]

    return {"nodes": nodes, "links": links}


@router.get("/entities/search")
async def search_entities(q: str, state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)) -> dict:
    if not q or len(q) < 2:
        return {"results": []}
    
    groups = await state.group_ids()
    rows = await graph.search_entities(q, groups)
    
    from kg_serializer.canonical import serialize_node
    results = []
    for row in rows:
        if row.get("uuid"):
            cnode = serialize_node(row)
            node_dict = cnode.model_dump(mode="json")
            node_dict["type"] = cnode.type
            node_dict["name"] = cnode.name
            results.append(node_dict)
            
    return {"results": results}


@router.get("/entities/{canonical_id:path}")
async def entity_facts(canonical_id: str, state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)) -> dict:
    groups = await state.group_ids()
    
    # 1. Fetch Entity metadata
    entity_cypher = (
        "MATCH (n:Entity {uuid: $uuid}) "
        "RETURN n"
    )
    entity_rows = await graph.query_groups(entity_cypher, groups, uuid=canonical_id)
    if not entity_rows:
        return {"entity": None, "facts": [], "incoming": []}
    entity_data = dict(entity_rows[0]["n"])
    entity_data["need_attention"] = bool(entity_data.get("need_attention"))
    
    # 2. Fetch outgoing and incoming edges
    edges_cypher = (
        "MATCH (a:Entity)-[r]->(b:Entity) "
        "WHERE r.group_id = $group_id AND (a.uuid = $uuid OR b.uuid = $uuid) "
        "RETURN r.uuid AS uuid, a.uuid AS source_uuid, a.name AS source_name, "
        "b.uuid AS target_uuid, b.name AS target_name, r.name AS relation_type, "
        "r.fact AS fact, r.episodes AS episodes, r.valid_at AS valid_at, r.invalid_at AS invalid_at"
    )
    edges_rows = await graph.query_groups(edges_cypher, groups, uuid=canonical_id)
    
    # Filter out invalid (superseded) edges from the default view
    active_edges = [e for e in edges_rows if not e.get("invalid_at")]
    
    index = await build_provenance_index(state)
    snapshot_version = await _latest_snapshot_version(state)
    
    # Pre-fetch all chunk text for these active edges
    all_chunks = {cid for edge in active_edges for cid in index.chunks_for(episodes_of(edge))}
    chunk_text = await _chunk_text(state, all_chunks)
    
    def _map_edge(edge):
        episodes = episodes_of(edge)
        print("MAPPING EDGE:", edge, "EPISODES:", episodes)
        uuid = edge.get("uuid") or ""
        edge_chunks = index.chunks_for(episodes)
        return {
            "fact_id": uuid,
            "relation_type": edge.get("relation_type"),
            "source_canonical_id": edge.get("source_uuid"),
            "source_name": edge.get("source_name"),
            "target_canonical_id": edge.get("target_uuid"),
            "target_name": edge.get("target_name"),
            "chunk_ids": edge_chunks,
            "confidence": 1.0,
            "mutation_id": index.mutation_id(episodes),
            "source_chunk_text": next(
                (chunk_text[c] for c in edge_chunks if c in chunk_text), edge.get("fact") or ""
            ),
            "source_document_name": index.document_name(episodes),
            "access_tag": index.access_tag(episodes),
            "resolution_confidence": 1.0,
            "snapshot_version": snapshot_version,
            "fact": edge.get("fact"),
            "valid_at": _text(edge.get("valid_at")),
            "invalid_at": _text(edge.get("invalid_at")),
            "_tags": index.tags_for(episodes),
        }

    outgoing = [_map_edge(e) for e in active_edges if e.get("source_uuid") == canonical_id]
    incoming = [_map_edge(e) for e in active_edges if e.get("target_uuid") == canonical_id]
    
    from kg_serializer.canonical import serialize_node
    cnode = serialize_node(entity_data, index=index)
    node_dict = cnode.model_dump(mode="json")
    node_dict["type"] = cnode.type
    node_dict["name"] = cnode.name
    node_dict["summary"] = cnode.semantic.summary if cnode.semantic else None
    
    return {
        "entity": node_dict,
        "facts": outgoing,
        "incoming": incoming
    }


async def _chunk_text(state: AppState, chunk_ids: set[str]) -> dict[str, str]:
    """Text for just the chunks in play, rather than the whole corpus."""
    if not chunk_ids:
        return {}
    placeholders = ",".join("?" * len(chunk_ids))
    rows = await state.db.fetch_all(
        f"SELECT chunk_id, text FROM chunks WHERE chunk_id IN ({placeholders})",
        tuple(chunk_ids),
    )
    return {row["chunk_id"]: row["text"] for row in rows}


@router.get("/entities/{canonical_id}/history")
async def entity_history(canonical_id: str, state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)) -> dict:
    """Every fact ever asserted about an entity, including superseded ones."""
    entities, edges = await read_graph(state, graph)
    index = await build_provenance_index(state)
    names = {e["uuid"]: e.get("name") for e in entities if e.get("uuid")}

    history = []
    for edge in edges:
        if canonical_id not in (edge.get("source_uuid"), edge.get("target_uuid")):
            continue
        episodes = episodes_of(edge)
        history.append(
            {
                "fact_id": edge.get("uuid"),
                "relation_type": edge.get("relation_type"),
                "subject": names.get(edge.get("source_uuid")) or edge.get("source_name"),
                "object": names.get(edge.get("target_uuid")) or edge.get("target_name"),
                "fact": edge.get("fact"),
                "valid_at": _text(edge.get("valid_at")),
                "invalid_at": _text(edge.get("invalid_at")),
                "status": "superseded" if edge.get("invalid_at") else "active",
                "source_document_name": index.document_name(episodes),
                "chunk_ids": index.chunks_for(episodes),
            }
        )

    history.sort(key=lambda item: (item["valid_at"] or "", item["fact_id"] or ""))
    return {"entity_id": canonical_id, "history": history}


@router.get("/entities/{canonical_id}/merge-history")
async def merge_history(canonical_id: str, state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)) -> dict:
    """Steward merge decisions that shaped this node."""
    rows = await state.db.fetch_all(
        "SELECT * FROM curation_candidates WHERE (primary_uuid = ? OR secondary_uuid = ?) "
        "AND status != 'open' ORDER BY decided_at DESC",
        (canonical_id, canonical_id),
    )
    return {
        "entity_id": canonical_id,
        "merges": [
            {
                "id": row["id"],
                "decision": row["status"],
                "primary_name": row["primary_name"],
                "secondary_name": row["secondary_name"],
                "confidence": row["confidence"],
                "reason": row["reason"],
                "decided_at": row["decided_at"],
            }
            for row in rows
        ],
    }


async def _latest_snapshot_version(state: AppState) -> str:
    version = await state.db.fetch_value(
        "SELECT version FROM snapshots WHERE passed = 1 ORDER BY created_at DESC LIMIT 1"
    )
    return version or "unreleased"


def _text(value: object) -> str | None:
    return None if value is None else str(value)
