import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException

from app.state import AppState, get_state
from kg_acl.models import Principal
from kg_acl.principal import require_principal
from kg_acl.schemas import RoleCreate, RoleResponse

router = APIRouter(prefix="/roles", tags=["roles"])

@router.get("", response_model=list[RoleResponse])
async def list_roles(
    principal: Principal = Depends(require_principal),
    state: AppState = Depends(get_state)
):
    db = state.db
    rows = await db.fetch_all(
        "SELECT * FROM roles",
        ()
    )
    return [dict(row) for row in rows]

@router.post("", response_model=RoleResponse)
async def create_role(
    role_data: RoleCreate,
    principal: Principal = Depends(require_principal),
    state: AppState = Depends(get_state)
):
    if not principal.is_org_supervisor:
        raise HTTPException(status_code=403, detail="Supervisor required")

    valid_clearances = ["public", "internal", "confidential", "restricted"]
    if role_data.clearance not in valid_clearances:
        raise HTTPException(status_code=400, detail="Invalid clearance level")

    from kg_acl.models import Sensitivity
    creator_clearance = principal.clearance
    requested_clearance = Sensitivity.parse(role_data.clearance)
    
    if requested_clearance > creator_clearance:
        raise HTTPException(status_code=403, detail=f"Cannot create role with clearance {role_data.clearance} > your clearance {creator_clearance.name.lower()}")

    db = state.db
    role_id = f"role-{uuid.uuid4()}"
    now = datetime.now(UTC)
    
    await db.execute("BEGIN")
    try:
        await db.execute(
            """
            INSERT INTO roles (
                id, name, description, clearance, is_system_role, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                role_id, role_data.name, role_data.description,
                role_data.clearance, 0, now, now
            )
        )
        # Bump auth version
        await db.execute(
            "UPDATE authorization_versions SET version = version + 1, updated_at = ?",
            (now,)
        )
        await db.execute("COMMIT")
    except Exception as e:
        await db.execute("ROLLBACK")
        raise HTTPException(status_code=400, detail=str(e))
        
    row = await db.fetch_one("SELECT * FROM roles WHERE id = ?", (role_id,))
    return dict(row)

@router.get("/{role_id}", response_model=RoleResponse)
async def get_role(
    role_id: str,
    principal: Principal = Depends(require_principal),
    state: AppState = Depends(get_state)
):
    db = state.db
    row = await db.fetch_one(
        "SELECT * FROM roles WHERE id = ?",
        (role_id,)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Role not found")
    return dict(row)



from pydantic import BaseModel


class RoleUpdate(BaseModel):
    description: str | None = None
    clearance: str | None = None

@router.patch("/{role_id}", response_model=RoleResponse)
async def update_role(
    role_id: str,
    role_data: RoleUpdate,
    principal: Principal = Depends(require_principal),
    state: AppState = Depends(get_state)
):
    if not principal.is_org_supervisor:
        raise HTTPException(status_code=403, detail="Supervisor required")

    db = state.db
    row = await db.fetch_one("SELECT * FROM roles WHERE id = ?", (role_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Role not found")

    valid_clearances = ["public", "internal", "confidential", "restricted"]
    
    if role_data.clearance is not None:
        if role_data.clearance not in valid_clearances:
            raise HTTPException(status_code=400, detail="Invalid clearance level")
        
        from kg_acl.models import Sensitivity
        creator_clearance = principal.clearance
        requested_clearance = Sensitivity.parse(role_data.clearance)
        
        if requested_clearance > creator_clearance:
            raise HTTPException(status_code=403, detail=f"Cannot update role to clearance {role_data.clearance} > your clearance {creator_clearance.name.lower()}")
        
        if row["is_system_role"]:
            old_idx = valid_clearances.index(row["clearance"])
            new_idx = valid_clearances.index(role_data.clearance)
            if new_idx < old_idx:
                raise HTTPException(status_code=400, detail="Cannot reduce clearance of a system role")

    now = datetime.now(UTC)
    
    updates = []
    params = []
    if role_data.description is not None:
        updates.append("description = ?")
        params.append(role_data.description)
    if role_data.clearance is not None:
        updates.append("clearance = ?")
        params.append(role_data.clearance)
        
    if updates:
        updates.append("updated_at = ?")
        params.append(now)
        params.append(role_id)
        
        await db.execute("BEGIN")
        try:
            await db.execute(f"UPDATE roles SET {', '.join(updates)} WHERE id = ?", params)
            if role_data.clearance is not None:
                await db.execute(
                    "UPDATE authorization_versions SET version = version + 1, updated_at = ?",
                    (now,)
                )
            await db.execute("COMMIT")
        except Exception as e:
            await db.execute("ROLLBACK")
            raise HTTPException(status_code=400, detail=str(e))
            
    updated_row = await db.fetch_one("SELECT * FROM roles WHERE id = ?", (role_id,))
    return dict(updated_row)

@router.delete("/{role_id}")
async def delete_role(
    role_id: str,
    principal: Principal = Depends(require_principal),
    state: AppState = Depends(get_state)
):
    if not principal.is_org_supervisor:
        raise HTTPException(status_code=403, detail="Supervisor required")

    db = state.db
    row = await db.fetch_one("SELECT * FROM roles WHERE id = ?", (role_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Role not found")
        
    if row["is_system_role"]:
        raise HTTPException(status_code=400, detail="Cannot delete a system role")
        
    now = datetime.now(UTC)
    
    await db.execute("BEGIN")
    try:
        await db.execute("DELETE FROM user_roles WHERE role_id = ?", (role_id,))
        await db.execute("DELETE FROM roles WHERE id = ?", (role_id,))
        await db.execute(
            "UPDATE authorization_versions SET version = version + 1, updated_at = ?",
            (now,)
        )
        await db.execute("COMMIT")
    except Exception as e:
        await db.execute("ROLLBACK")
        raise HTTPException(status_code=400, detail=str(e))
        
    return {"status": "deleted"}
