import json
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request

from app.state import AppState, get_state
from kg_acl.authorized_graph import AuthorizedGraphRepository
from kg_acl.materializer import materialize_scope
from kg_acl.models import Principal
from kg_acl.principal import get_authorized_graph, require_principal
from kg_acl.schemas import (
    AllOrgBoundary,
    DiscoverySnapshotBoundary,
    EntityListBoundary,
    MaterializeRequest,
    OntologySubtreeBoundary,
    ProjectRootBoundary,
    RepositorySetBoundary,
    ScopeCreate,
    ScopePatch,
    ScopeResponse,
    ScopeVersionResponse,
    SourceSetBoundary,
    VersionDiffResponse,
)


# Placeholder for get_graph logic if needed, usually passed in Request state
def get_graph(request: Request):
    return request.app.state.synapse.graph

router = APIRouter(prefix="/scopes", tags=["scopes"])

from pydantic import BaseModel

from kg_acl.discovery import generate_scope_proposals, generate_scope_proposals_with_prompt


class DiscoverRequest(BaseModel):
    prompt: str
    max_communities: int = 10
    min_entities: int = 3
    min_density: float = 0.05

@router.post("/proposals/discover")
async def discover_proposals_with_prompt(
    body: DiscoverRequest,
    principal: Principal = Depends(require_principal),
    state: AppState = Depends(get_state)
):
    """LLM-guided, prompt-driven community discovery."""
    if not principal.is_org_supervisor:
        raise HTTPException(status_code=403, detail="Supervisor required")
    
    import traceback
    try:
        proposals = await generate_scope_proposals_with_prompt(
            state,
            
            prompt=body.prompt,
            max_communities=body.max_communities,
            min_entities=body.min_entities,
            min_density=body.min_density,
        )
        return {"status": "success", "count": len(proposals), "proposals": proposals}
    except Exception as e:
        with open("error.log", "a") as f:
            f.write(f"\n--- discover_proposals_with_prompt ---\n{traceback.format_exc()}")
        raise HTTPException(500, f"Error during AI discovery: {e}")

@router.post("/proposals/generate")
async def generate_proposals(
    principal: Principal = Depends(require_principal),
    state: AppState = Depends(get_state)
):
    if not principal.is_org_supervisor:
        raise HTTPException(status_code=403, detail="Supervisor required")
    
    import traceback
    try:
        proposals = await generate_scope_proposals(state)
        return {"status": "success", "count": len(proposals), "proposals": proposals}
    except Exception:
        with open("error.log", "w") as f:
            f.write(traceback.format_exc())
        raise HTTPException(500, "Error generating proposals")



@router.get("/proposals")
async def list_proposals(
    principal: Principal = Depends(require_principal),
    state: AppState = Depends(get_state)
):
    db = state.db
    rows = await db.fetch_all(
        "SELECT * FROM scope_proposals WHERE status = 'open' ORDER BY created_at DESC",
        ()
    )
    
    proposals = []
    for row in rows:
        d = dict(row)
        for field in ["boundary_spec_json", "sample_entities_json", "source_distribution_json"]:
            if d.get(field):
                d[field] = json.loads(d[field])
        proposals.append(d)
    return proposals

@router.post("/proposals/{proposal_id}/preview")
async def preview_proposal(
    proposal_id: str,
    req: Request,
    assignee_kind: str | None = None,
    assignee_id: str | None = None,
    principal: Principal = Depends(require_principal),
    state: AppState = Depends(get_state)
):
    """Return graph nodes/edges for proposal boundary intersected with user's access."""
    db = state.db
    graph = get_graph(req)
    
    # 1. Get proposal
    proposal = await db.fetch_one(
        "SELECT * FROM scope_proposals WHERE id=?", 
        (proposal_id,)
    )
    if not proposal:
        raise HTTPException(404, "Proposal not found")
    
    spec_data = json.loads(proposal["boundary_spec_json"])
    
    # 2. Resolve proposal's entity UUIDs (read-only, no materialization)
    from kg_acl.materializer import resolve_boundary_spec
    from kg_acl.schemas import EntityListBoundary
    
    if "entity_list" in spec_data and "kind" not in spec_data:
        spec_data = {
            "kind": "entity_list", 
            "entity_uuids": spec_data.get("entity_list", []),
            "excluded_entity_uuids": spec_data.get("excluded_entity_uuids", []),
            "inclusion_mode": spec_data.get("inclusion_mode", "explicit")
        }
    
    kind = spec_data.get("kind", "entity_list")
    
    from kg_acl.schemas import (
        AllOrgBoundary,
        DiscoverySnapshotBoundary,
        OntologySubtreeBoundary,
        ProjectRootBoundary,
        RepositorySetBoundary,
        SourceSetBoundary,
    )

    try:
        if kind == "entity_list":
            boundary = EntityListBoundary(**spec_data)
        elif kind == "source_set":
            boundary = SourceSetBoundary(**spec_data)
        elif kind == "repository_set":
            boundary = RepositorySetBoundary(**spec_data)
        elif kind == "project_root":
            boundary = ProjectRootBoundary(**spec_data)
        elif kind == "ontology_subtree":
            boundary = OntologySubtreeBoundary(**spec_data)
        elif kind == "discovery_snapshot":
            boundary = DiscoverySnapshotBoundary(**spec_data)
        elif kind == "all_org":
            boundary = AllOrgBoundary(**spec_data)
        else:
            raise ValueError(f"Unknown boundary kind: {kind}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    proposal_entity_uuids, _, _ = await resolve_boundary_spec(graph, boundary)
    
    if not proposal_entity_uuids:
        return {"nodes": [], "edges": []}
    
    # 3. Get user's effective scope entity UUIDs (from resolver cache)
    from kg_acl.resolver import resolve_effective_scopes
    
    active_scope_ids, active_scope_version_ids = await resolve_effective_scopes(
        db,  principal.user_id, principal.role_ids, principal.is_org_supervisor
    )
    
    # Get all entity UUIDs user can access
    if principal.is_org_supervisor and not assignee_kind:
        user_accessible_uuids = None  # None = all
    else:
        now = datetime.now(UTC).isoformat()
        
        target_user_id = principal.user_id
        is_supervisor = principal.is_org_supervisor
        target_role_ids = principal.role_ids
        
        if assignee_kind == "user" and assignee_id:
            target_user_id = assignee_id
            # We should technically fetch this user's role_ids and is_supervisor, but for preview 
            # we mainly need the max_clearance which we compute below.
            
        if assignee_kind == "team" and assignee_id:
            # Check team members' clearance
            max_clearance = await db.fetch_one("""
                SELECT MAX(CASE r.clearance 
                    WHEN 'restricted' THEN 3 WHEN 'confidential' THEN 2 
                    WHEN 'internal' THEN 1 ELSE 0 END) as max_clearance
                FROM team_members tm
                JOIN user_roles ur ON tm.user_id = ur.user_id
                JOIN roles r ON ur.role_id = r.id
                WHERE tm.team_id = ? AND tm.removed_at IS NULL
                  AND ur.revoked_at IS NULL AND ur.valid_from <= ? AND (ur.valid_to IS NULL OR ur.valid_to > ?)
            """, (assignee_id,  now, now))
        elif assignee_kind == "role" and assignee_id:
            max_clearance = await db.fetch_one("""
                SELECT CASE clearance 
                    WHEN 'restricted' THEN 3 WHEN 'confidential' THEN 2 
                    WHEN 'internal' THEN 1 ELSE 0 END as max_clearance
                FROM roles WHERE id = ?
            """, (assignee_id,))
        else:
            max_clearance = await db.fetch_one("""
                SELECT MAX(CASE r.clearance 
                    WHEN 'restricted' THEN 3 WHEN 'confidential' THEN 2 
                    WHEN 'internal' THEN 1 ELSE 0 END) as max_clearance
                FROM user_roles ur
                JOIN roles r ON ur.role_id = r.id
                WHERE ur.user_id = ? 
                  AND ur.revoked_at IS NULL AND ur.valid_from <= ? 
                  AND (ur.valid_to IS NULL OR ur.valid_to > ?)
            """, (target_user_id,  now, now))
            
            # Also fetch maximum team clearance for this user
            team_max_clearance = await db.fetch_one("""
                SELECT MAX(CASE r.clearance 
                    WHEN 'restricted' THEN 3 WHEN 'confidential' THEN 2 
                    WHEN 'internal' THEN 1 ELSE 0 END) as max_team_clearance
                FROM team_members tm
                JOIN user_roles ur ON tm.user_id = ur.user_id
                JOIN roles r ON ur.role_id = r.id
                WHERE tm.team_id IN (
                    SELECT team_id FROM team_members WHERE user_id = ? AND removed_at IS NULL
                )
                AND tm.removed_at IS NULL
                AND ur.revoked_at IS NULL AND ur.valid_from <= ? AND (ur.valid_to IS NULL OR ur.valid_to > ?)
            """, (target_user_id, now, now))
            
        user_max_clearance = max_clearance["max_clearance"] if max_clearance and max_clearance["max_clearance"] else 0
        
        if (not assignee_kind or assignee_kind == "user") and 'team_max_clearance' in locals():
            tmc = team_max_clearance["max_team_clearance"] if team_max_clearance and team_max_clearance["max_team_clearance"] else 0
            user_max_clearance = max(user_max_clearance, tmc)
        
        # If simulating assignee, we still intersect with the current principal's effective scopes
        # because the backend must ensure the supervisor doesn't leak data they don't have.
        # But if the principal is a supervisor, we simulate the assignee's scopes!
        if assignee_kind:
            # For simplicity, if simulating an assignee, we just use the assignee's clearance to filter the proposal
            # and assume the new scope grants them access to everything in the proposal up to their clearance.
            # But the proposal itself doesn't have per-node sensitivity. 
            pass

        if not active_scope_version_ids and not principal.is_org_supervisor:
            return {"nodes": [], "edges": []}  # No access
            
        # If supervisor simulating an assignee, we don't intersect with active scope versions 
        # (they get the whole proposal) BUT we need to apply clearance? Wait, proposals don't have per-node sensitivity.
        # The prompt says: "Preview checks user's role clearance but not team members' clearance".
        
        # We will use user_max_clearance for the query below if active_scope_version_ids is present
        user_accessible_uuids = None
        if active_scope_version_ids:
            placeholders = ",".join("?" * len(active_scope_version_ids))
            rows = await db.fetch_all(
                f"""
                SELECT se.entity_uuid 
                FROM scope_entities se
                JOIN knowledge_scope_versions ksv ON se.scope_version_id = ksv.id
                JOIN knowledge_scopes ks ON ksv.scope_id = ks.id
                WHERE se.scope_version_id IN ({placeholders})
                  AND CASE ks.sensitivity 
                      WHEN 'restricted' THEN 3 WHEN 'confidential' THEN 2 
                      WHEN 'internal' THEN 1 ELSE 0 END <= ?
                """,
                (*active_scope_version_ids, user_max_clearance)
            )
            user_accessible_uuids = {row["entity_uuid"] for row in rows}
        
        if principal.is_org_supervisor and assignee_kind:
            # If supervisor is simulating assignee, they see the full proposal, BUT we must ensure the 
            # proposal's suggested_sensitivity is <= assignee's clearance!
            proposal_sensitivity = proposal.get("suggested_sensitivity", "internal")
            sens_val = {"restricted": 3, "confidential": 2, "internal": 1}.get(proposal_sensitivity, 0)
            if sens_val > user_max_clearance:
                return {"nodes": [], "edges": []} # Assignee cannot see this proposal
            user_accessible_uuids = None # Assignee will get full access to the proposal upon approval
    
    # 4. INTERSECT: proposal entities ∩ user accessible entities
    if user_accessible_uuids is not None:
        final_entity_uuids = proposal_entity_uuids & user_accessible_uuids
    else:
        final_entity_uuids = proposal_entity_uuids  # Supervisor sees all
    
    if not final_entity_uuids:
        return {"nodes": [], "edges": []}
    
    # 5. Query ONLY the intersected entities + their internal edges
    final_uuids = list(final_entity_uuids)
    nodes = []
    edges = []
    
    if final_uuids:
        chunk_size = 500
        for i in range(0, len(final_uuids), chunk_size):
            chunk = final_uuids[i:i + chunk_size]
            
            # Get node details
            node_rows = await graph.query(
                "MATCH (n:Entity) WHERE n.uuid IN $uuids RETURN n.uuid AS id, n.name AS name, labels(n) AS labels",
                uuids=chunk
            )
            for r in node_rows:
                labels = [l for l in r.get("labels", []) if l != "Entity"]
                nodes.append({
                    "id": r["id"],
                    "name": r.get("name") or "Unknown",
                    "type": labels[0] if labels else "Entity",
                    "val": 1
                })
            
            # Get internal edges (both endpoints in final_uuids)
            edge_rows = await graph.query(
                """
                MATCH (a:Entity)-[r]->(b:Entity)
                WHERE a.uuid IN $uuids AND b.uuid IN $uuids
                  AND r.invalid_at IS NULL AND r.expired_at IS NULL
                RETURN r.uuid AS uuid, r.name AS relation_type, r.fact AS fact,
                       a.uuid AS source, a.name AS source_name,
                       b.uuid AS target, b.name AS target_name,
                       r.valid_at, r.invalid_at, r.expired_at
                """,
                uuids=final_uuids
            )
            for r in edge_rows:
                # If relationship label is not explicitly 'RELATES_TO', relation_type might be None.
                # In graphiti edges are RELATES_TO and type is in property name. But fallback to generic relation if None.
                edges.append({
                    "uuid": r["uuid"],
                    "source": r["source"],
                    "target": r["target"],
                    "relation_type": r.get("relation_type") or "RELATES_TO",
                    "fact": r.get("fact"),
                    "source_name": r.get("source_name"),
                    "target_name": r.get("target_name"),
                    "valid_at": r.get("valid_at"),
                    "invalid_at": r.get("invalid_at"),
                    "expired_at": r.get("expired_at")
                })
    
    # 6. Compute degree for sizing
    degree_map = {n["id"]: 0 for n in nodes}
    for e in edges:
        if e["source"] in degree_map: degree_map[e["source"]] += 1
        if e["target"] in degree_map: degree_map[e["target"]] += 1
    
    for n in nodes:
        n["val"] = max(1, degree_map[n["id"]] * 0.5)
    
    return {"nodes": nodes, "edges": edges}

@router.post("/proposals/{proposal_id}/approve")
async def approve_proposal(
    proposal_id: str,
    principal: Principal = Depends(require_principal),
    state: AppState = Depends(get_state)
):
    if not principal.is_org_supervisor:
        raise HTTPException(status_code=403, detail="Supervisor required")
        
    db = state.db
    row = await db.fetch_one("SELECT * FROM scope_proposals WHERE id = ?", (proposal_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Proposal not found")
        
    now = datetime.now(UTC)
    
    # 1. Check if we already created a scope for this proposal
    existing = await db.fetch_one("SELECT id FROM knowledge_scopes WHERE discovery_origin = ?", (proposal_id,))
    if existing:
        scope_id = existing["id"]
        boundary_spec = json.loads(row["boundary_spec_json"])
        return {
            "status": "approved",
            "scope_id": scope_id,
            "boundary_spec": boundary_spec
        }

    # 2. Update proposal status
    await db.execute(
        "UPDATE scope_proposals SET status = 'approved', decided_at = ?, decided_by = ? WHERE id = ?",
        (now, principal.user_id, proposal_id)
    )
    
    # 3. Create actual scope & materialization request (as candidate version)
    scope_id = f"scope-{uuid.uuid4()}"
    
    # Ensure unique name to prevent IntegrityError
    unique_name = f"{row['suggested_name']} ({scope_id[-6:]})"
    
    await db.execute(
        """
        INSERT INTO knowledge_scopes (
            id, name, description, owner_user_id, status,
            sensitivity, review_status, discovery_origin, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 'proposed', ?, 'current', ?, ?, ?)
        """,
        (
            scope_id,  unique_name, row["description"],
            principal.user_id, row["suggested_sensitivity"], proposal_id, now, now
        )
    )
    
    boundary_spec = json.loads(row["boundary_spec_json"])
    
    # 4. Automatically materialize it to create a version
    from kg_acl.materializer import materialize_scope
    
    spec_data = boundary_spec.copy()
    kind = spec_data.pop("kind")
    from kg_acl.schemas import (
        SourceSetBoundary,
        RepositorySetBoundary,
        ProjectRootBoundary,
        OntologySubtreeBoundary,
        DiscoverySnapshotBoundary,
        AllOrgBoundary,
        EntityListBoundary,
    )
    boundary = None
    if kind == "entity_list":
        boundary = EntityListBoundary(**spec_data)
    elif kind == "source_set":
        boundary = SourceSetBoundary(**spec_data)
    elif kind == "repository_set":
        boundary = RepositorySetBoundary(**spec_data)
    elif kind == "project_root":
        boundary = ProjectRootBoundary(**spec_data)
    elif kind == "ontology_subtree":
        boundary = OntologySubtreeBoundary(**spec_data)
    elif kind == "discovery_snapshot":
        boundary = DiscoverySnapshotBoundary(**spec_data)
    elif kind == "all_org":
        boundary = AllOrgBoundary(**spec_data)
        
    if boundary:
        version_id = await materialize_scope(
            db=db,
            graph=state.graph,
            scope_id=scope_id,
            boundary_spec=boundary,
            proposed_by=principal.user_id
        )
        await db.execute(
            "UPDATE knowledge_scope_versions SET status = 'approved', approved_by = ?, approved_at = ? WHERE id = ?",
            (principal.user_id, now, version_id)
        )
        await db.execute(
            "UPDATE knowledge_scopes SET active_version_id = ?, status = 'approved', updated_at = ? WHERE id = ?",
            (version_id, now, scope_id)
        )
    
    return {
        "status": "approved",
        "scope_id": scope_id,
        "boundary_spec": boundary_spec
    }

@router.get("", response_model=list[ScopeResponse])
async def list_scopes(
    principal: Principal = Depends(require_principal),
    state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)
):
    db = state.db
    rows = await db.fetch_all(
        "SELECT * FROM knowledge_scopes ",
        ()
    )
        
    return [dict(row) for row in rows]

@router.post("", response_model=ScopeResponse)
async def create_scope(
    scope_data: ScopeCreate,
    principal: Principal = Depends(require_principal),
    state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)
):
    db = state.db
    scope_id = f"scope-{uuid.uuid4()}"
    now = datetime.now(UTC)
    
    await db.execute("BEGIN")
    try:
        await db.execute(
            """
            INSERT INTO knowledge_scopes (
                id, name, description, owner_user_id, status,
                sensitivity, review_status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'proposed', ?, 'current', ?, ?)
            """,
            (
                scope_id,  scope_data.name, scope_data.description,
                principal.user_id, scope_data.sensitivity, now, now
            )
        )
        await db.execute("COMMIT")
    except Exception as e:
        await db.execute("ROLLBACK")
        raise HTTPException(status_code=400, detail=str(e))
        
    row = await db.fetch_one("SELECT * FROM knowledge_scopes WHERE id = ?", (scope_id,))
        
    return dict(row)

@router.get("/{scope_id}", response_model=ScopeResponse)
async def get_scope(
    scope_id: str,
    principal: Principal = Depends(require_principal),
    state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)
):
    db = state.db
    row = await db.fetch_one("SELECT * FROM knowledge_scopes WHERE id = ?", (scope_id,))
        
    if not row:
        raise HTTPException(status_code=404, detail="Scope not found")
        
    return dict(row)

@router.patch("/{scope_id}", response_model=ScopeResponse)
async def update_scope(
    scope_id: str,
    patch: ScopePatch,
    principal: Principal = Depends(require_principal),
    state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)
):
    db = state.db
    await db.execute("BEGIN")
    try:
        row = await db.fetch_one("SELECT * FROM knowledge_scopes WHERE id = ?", (scope_id,))
        if not row:
            await db.execute("ROLLBACK")
            raise HTTPException(status_code=404, detail="Scope not found")
            
        updates = []
        params = []
        if patch.name is not None:
            updates.append("name = ?")
            params.append(patch.name)
        if patch.description is not None:
            updates.append("description = ?")
            params.append(patch.description)
        if patch.sensitivity is not None:
            updates.append("sensitivity = ?")
            params.append(patch.sensitivity)
        if patch.owner_user_id is not None:
            updates.append("owner_user_id = ?")
            params.append(patch.owner_user_id)
            
        if updates:
            updates.append("updated_at = ?")
            params.append(datetime.now(UTC))
            
            query = f"UPDATE knowledge_scopes SET {', '.join(updates)} WHERE id = ?"
            params.append(scope_id)
            await db.execute(query, params)
            await db.execute("COMMIT")
        else:
            await db.execute("ROLLBACK")
    except HTTPException:
        raise
    except Exception as e:
        await db.execute("ROLLBACK")
        raise HTTPException(status_code=500, detail=str(e))
        
    row = await db.fetch_one("SELECT * FROM knowledge_scopes WHERE id = ?", (scope_id,))
        
    return dict(row)

@router.post("/{scope_id}/deprecate")
async def deprecate_scope(
    scope_id: str,
    principal: Principal = Depends(require_principal),
    state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)
):
    db = state.db
    now = datetime.now(UTC)
    await db.execute("BEGIN")
    try:
        row = await db.fetch_one("SELECT id FROM knowledge_scopes WHERE id = ?", (scope_id,))
        if not row:
            await db.execute("ROLLBACK")
            raise HTTPException(status_code=404, detail="Scope not found")
            
        await db.execute(
            "UPDATE knowledge_scopes SET status = 'deprecated', deprecated_at = ?, updated_at = ? WHERE id = ?",
            (now, now, scope_id)
        )
        await db.execute("COMMIT")
    except Exception:
        await db.execute("ROLLBACK")
        raise
    return {"status": "deprecated"}

@router.post("/{scope_id}/materialize")
async def materialize(
    scope_id: str,
    request: MaterializeRequest,
    req: Request,
    principal: Principal = Depends(require_principal),
    state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)
):
    db = state.db
    graph = get_graph(req)
    # Parse boundary spec
    kind = request.boundary_kind
    spec_data = request.boundary_spec
    spec_data["kind"] = kind
    
    try:
        if kind == "entity_list":
            boundary = EntityListBoundary(**spec_data)
        elif kind == "source_set":
            boundary = SourceSetBoundary(**spec_data)
        elif kind == "repository_set":
            boundary = RepositorySetBoundary(**spec_data)
        elif kind == "project_root":
            boundary = ProjectRootBoundary(**spec_data)
        elif kind == "ontology_subtree":
            boundary = OntologySubtreeBoundary(**spec_data)
        elif kind == "discovery_snapshot":
            boundary = DiscoverySnapshotBoundary(**spec_data)
        elif kind == "all_org":
            boundary = AllOrgBoundary(**spec_data)
        else:
            raise ValueError(f"Unknown boundary kind: {kind}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    row = await db.fetch_one("SELECT id FROM knowledge_scopes WHERE id = ?", (scope_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Scope not found")
            
    version_id = await materialize_scope(
        db=db,
        graph=graph,
        scope_id=scope_id,
        boundary_spec=boundary,
        proposed_by=principal.user_id,
        
    )
    
    return {"version_id": version_id}

@router.get("/{scope_id}/versions", response_model=list[ScopeVersionResponse])
async def list_versions(
    scope_id: str,
    principal: Principal = Depends(require_principal),
    state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)
):
    db = state.db
    rows = await db.fetch_all(
        "SELECT * FROM knowledge_scope_versions WHERE scope_id = ? ORDER BY version_number DESC",
        (scope_id,)
    )
        
    versions = []
    for row in rows:
        d = dict(row)
        if isinstance(d.get("boundary_spec_json"), str):
            d["boundary_spec_json"] = json.loads(d["boundary_spec_json"])
        versions.append(d)
    return versions

@router.get("/{scope_id}/versions/{version_id}", response_model=ScopeVersionResponse)
async def get_version(
    scope_id: str,
    version_id: str,
    principal: Principal = Depends(require_principal),
    state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)
):
    db = state.db
    row = await db.fetch_one(
        "SELECT * FROM knowledge_scope_versions WHERE id = ? AND scope_id = ?",
        (version_id, scope_id)
    )
        
    if not row:
        raise HTTPException(status_code=404, detail="Version not found")
        
    d = dict(row)
    if isinstance(d.get("boundary_spec_json"), str):
        d["boundary_spec_json"] = json.loads(d["boundary_spec_json"])
    return d

@router.post("/{scope_id}/versions/{version_id}/approve")
async def approve_version(
    scope_id: str,
    version_id: str,
    principal: Principal = Depends(require_principal),
    state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)
):
    db = state.db
    if not principal.is_org_supervisor:
        raise HTTPException(status_code=403, detail="Supervisor required")
        
    now = datetime.now(UTC)
    
    await db.execute("BEGIN")
    try:
        row = await db.fetch_one(
            "SELECT id FROM knowledge_scope_versions WHERE id = ? AND scope_id = ?",
            (version_id, scope_id)
        )
        if not row:
            await db.execute("ROLLBACK")
            raise HTTPException(status_code=404, detail="Version not found")
            
        await db.execute(
            """
            UPDATE knowledge_scope_versions
            SET status = 'approved', approved_at = ?, approved_by = ?
            WHERE id = ?
            """,
            (now, principal.user_id, version_id)
        )
        
        await db.execute(
            """
            UPDATE knowledge_scopes
            SET active_version_id = ?, status = 'approved', review_status = 'current', updated_at = ?
            WHERE id = ?
            """,
            (version_id, now, scope_id)
        )
        
        await db.execute(
            "UPDATE authorization_versions SET version = version + 1, updated_at = ? ",
            (now,)
        )
        
        await db.execute("COMMIT")
    except HTTPException:
        raise
    except Exception:
        await db.execute("ROLLBACK")
        raise
        
    return {"status": "approved"}

@router.post("/{scope_id}/versions/{version_id}/reject")
async def reject_version(
    scope_id: str,
    version_id: str,
    principal: Principal = Depends(require_principal),
    state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)
):
    db = state.db
    if not principal.is_org_supervisor:
        raise HTTPException(status_code=403, detail="Supervisor required")
        
    now = datetime.now(UTC)
    
    await db.execute("BEGIN")
    try:
        row = await db.fetch_one(
            "SELECT id FROM knowledge_scope_versions WHERE id = ? AND scope_id = ?",
            (version_id, scope_id)
        )
        if not row:
            await db.execute("ROLLBACK")
            raise HTTPException(status_code=404, detail="Version not found")
            
        await db.execute(
            """
            UPDATE knowledge_scope_versions
            SET status = 'rejected', rejected_at = ?
            WHERE id = ?
            """,
            (now, version_id)
        )
        
        await db.execute(
            "UPDATE knowledge_scopes SET review_status = 'current', updated_at = ? WHERE id = ?",
            (now, scope_id)
        )
        
        await db.execute("COMMIT")
    except HTTPException:
        raise
    except Exception:
        await db.execute("ROLLBACK")
        raise
        
    return {"status": "rejected"}

@router.get("/{scope_id}/versions/{version_id}/diff", response_model=VersionDiffResponse)
async def get_version_diff(
    scope_id: str,
    version_id: str,
    principal: Principal = Depends(require_principal),
    state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)
):
    db = state.db
    # Stub for now
    return {
        "entity_added": [],
        "entity_removed": [],
        "edge_added": [],
        "edge_removed": [],
        "entity_drift_pct": 0.0,
        "edge_drift_pct": 0.0,
        "change_summary": "Diff not fully implemented"
    }
