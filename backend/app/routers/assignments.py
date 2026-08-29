import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException

from app.state import AppState, get_state
from kg_acl.models import Principal
from kg_acl.principal import require_principal
from kg_acl.schemas import ScopeAssignmentCreate, ScopeAssignmentResponse

router = APIRouter(prefix="/scopes", tags=["assignments"])

@router.get("/{scope_id}/assignments", response_model=list[ScopeAssignmentResponse])
async def list_assignments(
    scope_id: str,
    principal: Principal = Depends(require_principal),
    state: AppState = Depends(get_state)
):
    db = state.db
    # Only org supervisors can view assignments currently
    if not principal.is_org_supervisor:
        raise HTTPException(status_code=403, detail="Supervisor required")

    rows = await db.fetch_all(
        "SELECT * FROM scope_assignees WHERE scope_id = ?",
        (scope_id,)
    )
    return [dict(row) for row in rows]

@router.post("/{scope_id}/assignments", response_model=ScopeAssignmentResponse)
async def create_assignment(
    scope_id: str,
    assignment_data: ScopeAssignmentCreate,
    principal: Principal = Depends(require_principal),
    state: AppState = Depends(get_state)
):
    if not principal.is_org_supervisor:
        raise HTTPException(status_code=403, detail="Supervisor required")

    db = state.db
    
    # Validate scope exists
    scope = await db.fetch_one("SELECT id, sensitivity FROM knowledge_scopes WHERE id = ?", (scope_id,))
    if not scope:
        raise HTTPException(status_code=404, detail="Scope not found")
        
    from kg_acl.models import Sensitivity
    scope_sensitivity = Sensitivity.parse(scope["sensitivity"])

    if assignment_data.assignee_kind == "role":
        role = await db.fetch_one("SELECT clearance FROM roles WHERE id = ?", (assignment_data.assignee_id,))
        if not role or Sensitivity.parse(role["clearance"]) < scope_sensitivity:
            role_clearance = role["clearance"] if role else "unknown"
            raise HTTPException(status_code=403, detail=f"Role clearance '{role_clearance}' < scope sensitivity '{scope['sensitivity']}'")
    
    elif assignment_data.assignee_kind == "team":
        # Get max clearance of team members' roles
        max_clearance = await db.fetch_one("""
            SELECT MAX(CASE r.clearance 
                WHEN 'restricted' THEN 3 WHEN 'confidential' THEN 2 
                WHEN 'internal' THEN 1 ELSE 0 END) as max_clearance
            FROM team_members tm
            JOIN user_roles ur ON tm.user_id = ur.user_id
            JOIN roles r ON ur.role_id = r.id
            WHERE tm.team_id = ? AND tm.removed_at IS NULL
              AND ur.revoked_at IS NULL AND ur.valid_from <= ? AND (ur.valid_to IS NULL OR ur.valid_to > ?)
        """, (assignment_data.assignee_id, datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat()))
        
        if not max_clearance or (max_clearance["max_clearance"] or 0) < scope_sensitivity:
            raise HTTPException(status_code=403, detail="Team max clearance < scope sensitivity")

    assignment_id = f"assign-{uuid.uuid4()}"
    now = datetime.now(UTC)
    
    await db.execute("BEGIN")
    try:
        await db.execute(
            """
            INSERT INTO scope_assignees (
                id, scope_id, assignee_kind, assignee_id, granted_by,
                valid_from, created_at, reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                assignment_id, scope_id, assignment_data.assignee_kind,
                assignment_data.assignee_id, principal.user_id, now, now, assignment_data.reason
            )
        )
        # Bump auth version to invalidate cache
        await db.execute(
            "UPDATE authorization_versions SET version = version + 1, updated_at = ?",
            (now.isoformat(),)
        )
        await db.execute("COMMIT")
    except Exception as e:
        await db.execute("ROLLBACK")
        raise HTTPException(status_code=400, detail=str(e))
        
    row = await db.fetch_one("SELECT * FROM scope_assignees WHERE id = ?", (assignment_id,))
    return dict(row)

@router.delete("/{scope_id}/assignments/{assignment_id}")
async def revoke_assignment(
    scope_id: str,
    assignment_id: str,
    principal: Principal = Depends(require_principal),
    state: AppState = Depends(get_state)
):
    if not principal.is_org_supervisor:
        raise HTTPException(status_code=403, detail="Supervisor required")

    db = state.db
    now = datetime.now(UTC)
    
    await db.execute("BEGIN")
    try:
        row = await db.fetch_one(
            "SELECT id FROM scope_assignees WHERE id = ? AND scope_id = ?",
            (assignment_id, scope_id)
        )
        if not row:
            await db.execute("ROLLBACK")
            raise HTTPException(status_code=404, detail="Assignment not found")
            
        await db.execute(
            "UPDATE scope_assignees SET revoked_at = ? WHERE id = ?",
            (now, assignment_id)
        )
        
        await db.execute(
            "UPDATE authorization_versions SET version = version + 1, updated_at = ?",
            (now,)
        )
        await db.execute("COMMIT")
    except HTTPException:
        raise
    except Exception as e:
        await db.execute("ROLLBACK")
        raise HTTPException(status_code=400, detail=str(e))
        
    return {"status": "revoked"}

from kg_acl.schemas import BulkAssignmentRequest


@router.post("/{scope_id}/assignments/bulk", response_model=list[ScopeAssignmentResponse])
async def create_assignments_bulk(
    scope_id: str,
    request: BulkAssignmentRequest,
    principal: Principal = Depends(require_principal),
    state: AppState = Depends(get_state)
):
    if not principal.is_org_supervisor:
        raise HTTPException(status_code=403, detail="Supervisor required")

    db = state.db
    scope = await db.fetch_one("SELECT id, sensitivity FROM knowledge_scopes WHERE id = ?", (scope_id,))
    if not scope:
        raise HTTPException(status_code=404, detail="Scope not found")
        
    now = datetime.now(UTC)
    now_iso = now.isoformat()
    
    from kg_acl.models import Sensitivity
    scope_sensitivity = Sensitivity.parse(scope["sensitivity"])

    for assignment_data in request.assignments:
        if assignment_data.assignee_kind == "role":
            role = await db.fetch_one("SELECT clearance FROM roles WHERE id = ?", (assignment_data.assignee_id,))
            if not role or Sensitivity.parse(role["clearance"]) < scope_sensitivity:
                role_clearance = role["clearance"] if role else "unknown"
                raise HTTPException(status_code=403, detail=f"Role clearance '{role_clearance}' < scope sensitivity '{scope['sensitivity']}'")
        
        elif assignment_data.assignee_kind == "team":
            # Get max clearance of team members' roles
            max_clearance = await db.fetch_one("""
                SELECT MAX(CASE r.clearance 
                    WHEN 'restricted' THEN 3 WHEN 'confidential' THEN 2 
                    WHEN 'internal' THEN 1 ELSE 0 END) as max_clearance
                FROM team_members tm
                JOIN user_roles ur ON tm.user_id = ur.user_id
                JOIN roles r ON ur.role_id = r.id
                WHERE tm.team_id = ? AND tm.removed_at IS NULL
                  AND ur.revoked_at IS NULL AND ur.valid_from <= ? AND (ur.valid_to IS NULL OR ur.valid_to > ?)
            """, (assignment_data.assignee_id, now_iso, now_iso))
            
            if not max_clearance or (max_clearance["max_clearance"] or 0) < scope_sensitivity:
                raise HTTPException(status_code=403, detail="Team max clearance < scope sensitivity")

    created_ids = []
    
    await db.execute("BEGIN")
    try:
        for assignment_data in request.assignments:
            assignment_id = f"assign-{uuid.uuid4()}"
            await db.execute(
                """
                INSERT INTO scope_assignees (
                    id, scope_id, assignee_kind, assignee_id, granted_by,
                    valid_from, valid_to, created_at, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    assignment_id, scope_id, assignment_data.assignee_kind,
                    assignment_data.assignee_id, principal.user_id, 
                    assignment_data.valid_from or now, assignment_data.valid_to,
                    now, assignment_data.reason
                )
            )
            created_ids.append(assignment_id)
            
        if created_ids:
            await db.execute(
                "UPDATE authorization_versions SET version = version + 1, updated_at = ?",
                (now,)
            )
        await db.execute("COMMIT")
    except Exception as e:
        await db.execute("ROLLBACK")
        raise HTTPException(status_code=400, detail=str(e))
        
    if not created_ids:
        return []
        
    placeholders = ",".join("?" for _ in created_ids)
    rows = await db.fetch_all(f"SELECT * FROM scope_assignees WHERE id IN ({placeholders})", created_ids)
    return [dict(row) for row in rows]
