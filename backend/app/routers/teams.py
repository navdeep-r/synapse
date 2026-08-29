from fastapi import APIRouter, Depends, HTTPException
from typing import List
import uuid
from datetime import datetime, timezone

from app.state import AppState, get_state
from kg_acl.models import Principal
from kg_acl.principal import require_principal
from kg_acl.schemas import TeamCreate, TeamResponse, TeamMemberAdd

router = APIRouter(prefix="/teams", tags=["teams"])

@router.get("", response_model=List[TeamResponse])
async def list_teams(
    principal: Principal = Depends(require_principal),
    state: AppState = Depends(get_state)
):
    db = state.db
    rows = await db.fetch_all(
        "SELECT * FROM teams",
        ()
    )
    return [dict(row) for row in rows]

@router.post("", response_model=TeamResponse)
async def create_team(
    team_data: TeamCreate,
    principal: Principal = Depends(require_principal),
    state: AppState = Depends(get_state)
):
    if not principal.is_org_supervisor:
        raise HTTPException(status_code=403, detail="Supervisor required")

    db = state.db
    team_id = f"team-{uuid.uuid4()}"
    now = datetime.now(timezone.utc)
    
    await db.execute(
        """
        INSERT INTO teams (
            id, name, description, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (team_id, team_data.name, team_data.description, now, now)
    )
        
    row = await db.fetch_one("SELECT * FROM teams WHERE id = ?", (team_id,))
    return dict(row)

@router.get("/{team_id}", response_model=dict)
async def get_team(
    team_id: str,
    principal: Principal = Depends(require_principal),
    state: AppState = Depends(get_state)
):
    db = state.db
    row = await db.fetch_one(
        "SELECT * FROM teams WHERE id = ?",
        (team_id,)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Team not found")
        
    members = await db.fetch_all(
        """
        SELECT u.id, u.email, u.display_name, tm.joined_at 
        FROM team_members tm
        JOIN users u ON tm.user_id = u.id
        WHERE tm.team_id = ? AND tm.removed_at IS NULL
        """,
        (team_id,)
    )
        
    team_dict = dict(row)
    team_dict["members"] = [dict(m) for m in members]
    return team_dict

@router.patch("/{team_id}", response_model=TeamResponse)
async def update_team(
    team_id: str,
    team_data: TeamCreate,
    principal: Principal = Depends(require_principal),
    state: AppState = Depends(get_state)
):
    if not principal.is_org_supervisor:
        raise HTTPException(status_code=403, detail="Supervisor required")

    db = state.db
    row = await db.fetch_one("SELECT * FROM teams WHERE id = ?", (team_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Team not found")

    now = datetime.now(timezone.utc)
    await db.execute(
        "UPDATE teams SET name = ?, description = ?, updated_at = ? WHERE id = ?",
        (team_data.name, team_data.description, now, team_id)
    )
    
    updated_row = await db.fetch_one("SELECT * FROM teams WHERE id = ?", (team_id,))
    return dict(updated_row)

@router.delete("/{team_id}")
async def delete_team(
    team_id: str,
    principal: Principal = Depends(require_principal),
    state: AppState = Depends(get_state)
):
    if not principal.is_org_supervisor:
        raise HTTPException(status_code=403, detail="Supervisor required")

    db = state.db
    row = await db.fetch_one("SELECT * FROM teams WHERE id = ?", (team_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Team not found")
        
    now = datetime.now(timezone.utc)
    
    await db.execute("BEGIN")
    try:
        await db.execute("UPDATE team_members SET removed_at = ? WHERE team_id = ? AND removed_at IS NULL", (now, team_id))
        await db.execute("DELETE FROM teams WHERE id = ?", (team_id,))
        # Bump auth version since deleting a team revokes its permissions
        await db.execute(
            "UPDATE authorization_versions SET version = version + 1, updated_at = ?",
            (now,)
        )
        await db.execute("COMMIT")
    except Exception as e:
        await db.execute("ROLLBACK")
        raise HTTPException(status_code=400, detail=str(e))
        
    return {"status": "deleted"}

@router.post("/{team_id}/members")
async def add_team_member(
    team_id: str,
    member_data: TeamMemberAdd,
    principal: Principal = Depends(require_principal),
    state: AppState = Depends(get_state)
):
    if not principal.is_org_supervisor:
        raise HTTPException(status_code=403, detail="Supervisor required")

    db = state.db
    team = await db.fetch_one("SELECT id FROM teams WHERE id = ?", (team_id,))
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
        
    user = await db.fetch_one(
        "SELECT id FROM users WHERE id = ?",
        (member_data.user_id,)
    )
    if not user:
        raise HTTPException(status_code=404, detail="User not found in organization")
        
    now = datetime.now(timezone.utc)
    
    existing = await db.fetch_one(
        "SELECT id FROM team_members WHERE team_id = ? AND user_id = ? AND removed_at IS NULL",
        (team_id, member_data.user_id)
    )
    if existing:
        return {"status": "already_member"}
        
    await db.execute("BEGIN")
    try:
        await db.execute(
            "INSERT INTO team_members (id, team_id, user_id, joined_at) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), team_id, member_data.user_id, now)
        )
        await db.execute(
            "UPDATE authorization_versions SET version = version + 1, updated_at = ?",
            (now,)
        )
        await db.execute("COMMIT")
    except Exception as e:
        await db.execute("ROLLBACK")
        raise HTTPException(status_code=400, detail=str(e))
        
    return {"status": "member_added"}

@router.delete("/{team_id}/members/{user_id}")
async def remove_team_member(
    team_id: str,
    user_id: str,
    principal: Principal = Depends(require_principal),
    state: AppState = Depends(get_state)
):
    if not principal.is_org_supervisor:
        raise HTTPException(status_code=403, detail="Supervisor required")

    db = state.db
    now = datetime.now(timezone.utc)
    
    await db.execute("BEGIN")
    try:
        row = await db.fetch_one(
            "SELECT id FROM team_members WHERE team_id = ? AND user_id = ? AND removed_at IS NULL",
            (team_id, user_id)
        )
        if not row:
            await db.execute("ROLLBACK")
            raise HTTPException(status_code=404, detail="Active member not found")
            
        await db.execute(
            "UPDATE team_members SET removed_at = ? WHERE id = ?",
            (now, row["id"])
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
        
    return {"status": "member_removed"}
