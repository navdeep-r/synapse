"""Supervisor user management endpoints."""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.state import AppState, get_state
from kg_acl.models import Principal
from kg_acl.principal import require_principal

logger = logging.getLogger("synapse.users")

router = APIRouter(prefix="/users", tags=["users"])


def _require_supervisor(principal: Principal = Depends(require_principal)) -> Principal:
    if not principal.is_org_supervisor:
        raise HTTPException(status_code=403, detail="Organization supervisor required")
    return principal


class InviteUserRequest(BaseModel):
    email: str
    display_name: str | None = None
    role_ids: list[str] = []
    team_ids: list[str] = []
    passcode: str | None = None


class UpdateUserRequest(BaseModel):
    status: str | None = None
    is_active: int | None = None
    role_ids: list[str] | None = None


@router.get("")
async def list_users(
    state: AppState = Depends(get_state),
    principal: Principal = Depends(_require_supervisor),
) -> list[dict[str, Any]]:
    db = state.db
    # List users in the org
    users = await db.fetch_all(
        """
        SELECT u.id, u.email, u.display_name, u.status, u.email_verified, u.is_active, u.last_login_at, u.created_at
        FROM users u
        JOIN organization_members om ON u.id = om.user_id
        
        ORDER BY u.email ASC
        """,
        ()
    )
    return users


@router.get("/{user_id}")
async def get_user(
    user_id: str,
    state: AppState = Depends(get_state),
    principal: Principal = Depends(_require_supervisor),
) -> dict[str, Any]:
    db = state.db
    user = await db.fetch_one(
        """
        SELECT u.id, u.email, u.display_name, u.status, u.email_verified, u.is_active, u.last_login_at, u.created_at, u.invited_by
        FROM users u
        JOIN organization_members om ON u.id = om.user_id
        WHERE u.id = ?
        """,
        (user_id,)
    )
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    roles = await db.fetch_all(
        """
        SELECT r.id, r.name, ur.valid_from, ur.valid_to, ur.revoked_at
        FROM roles r
        JOIN user_roles ur ON r.id = ur.role_id
        WHERE ur.user_id = ?
        """,
        (user_id,)
    )
    
    sessions = await db.fetch_all(
        "SELECT id, issued_at, expires_at, revoked_at, user_agent, ip_hash FROM sessions WHERE user_id = ? ORDER BY issued_at DESC",
        (user_id,)
    )
    
    return {
        **user,
        "roles": roles,
        "sessions": sessions,
    }


@router.post("/invite")
async def invite_user(
    body: InviteUserRequest,
    state: AppState = Depends(get_state),
    principal: Principal = Depends(_require_supervisor),
) -> dict[str, Any]:
    import secrets
    import string
    db = state.db
    now = datetime.now(UTC).isoformat()
    
    existing = await db.fetch_one("SELECT id FROM users WHERE email = ?", (body.email,))
    if existing:
        raise HTTPException(status_code=400, detail="User with this email already exists")
        
    user_id = str(uuid.uuid4())
    token = str(uuid.uuid4())
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    expires = (datetime.now(UTC) + timedelta(days=7)).isoformat()
    
    passcode = (body.passcode or ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))).upper()
    import bcrypt
    passcode_hash = bcrypt.hashpw(passcode.encode(), bcrypt.gensalt()).decode()
    
    await db.execute(
        """
        INSERT INTO users (id, email, display_name, status, email_verified, invited_by, invited_at, invitation_token_hash, invitation_token_expires, password_hash, is_active, created_at, updated_at)
        VALUES (?, ?, ?, 'pending_invitation', 0, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (user_id, body.email, body.display_name, principal.user_id, now, token_hash, expires, passcode_hash, now, now)
    )
    
    await db.execute(
        "INSERT INTO organization_members (user_id, membership_status, joined_at, invited_by, updated_at) VALUES (?, 'active', ?, ?, ?)",
        (user_id, now, principal.user_id, now)
    )
    
    if body.role_ids:
        from kg_acl.models import Sensitivity
        placeholders = ",".join("?" * len(body.role_ids))
        roles = await db.fetch_all(
            f"SELECT id, clearance FROM roles WHERE id IN ({placeholders}) ",
            (*body.role_ids,)
        )
        for role in roles:
            if Sensitivity.parse(role["clearance"]) > principal.clearance:
                raise HTTPException(status_code=403, detail=f"Cannot assign role {role['id']} with clearance {role['clearance']} > your clearance")

    for role_id in body.role_ids:
        await db.execute(
            "INSERT INTO user_roles (id, user_id, role_id, granted_at, granted_by, valid_from) VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), user_id, role_id, now, principal.user_id, now)
        )
        
    for team_id in body.team_ids:
        await db.execute(
            "INSERT INTO team_members (id, team_id, user_id, joined_at) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), team_id, user_id, now)
        )
        
    logger.info("Invited %s. Magic link: /accept-invitation?token=%s&passcode=%s", body.email, token, passcode)
    return {"status": "invited", "id": user_id, "passcode": passcode}


@router.patch("/{user_id}")
async def update_user(
    user_id: str,
    body: UpdateUserRequest,
    state: AppState = Depends(get_state),
    principal: Principal = Depends(_require_supervisor),
) -> dict[str, Any]:
    db = state.db
    now = datetime.now(UTC).isoformat()
    
    membership = await db.fetch_one("SELECT 1 FROM organization_members WHERE user_id = ? ", (user_id,))
    if not membership:
        raise HTTPException(status_code=404, detail="User not found")
        
    updates = []
    params = []
    
    if body.status is not None:
        updates.append("status = ?")
        params.append(body.status)
    if body.is_active is not None:
        updates.append("is_active = ?")
        params.append(body.is_active)
        
    if updates:
        updates.append("updated_at = ?")
        params.append(now)
        params.append(user_id)
        
        query = f"UPDATE users SET {', '.join(updates)} WHERE id = ?"
        await db.execute(query, params)

        if body.is_active == 0 or body.status == 'suspended':
            await db.execute(
                "UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                (now, user_id)
            )
            await db.execute(
                "UPDATE users SET token_version = token_version + 1 WHERE id = ?",
                (user_id,)
            )
        
    if body.role_ids is not None:
        if body.role_ids:
            from kg_acl.models import Sensitivity
            placeholders = ",".join("?" * len(body.role_ids))
            roles = await db.fetch_all(
                f"SELECT id, clearance FROM roles WHERE id IN ({placeholders}) ",
                (*body.role_ids,)
            )
            for role in roles:
                if Sensitivity.parse(role["clearance"]) > principal.clearance:
                    raise HTTPException(status_code=403, detail=f"Cannot assign role {role['id']} with clearance {role['clearance']} > your clearance")

        # Revoke existing
        await db.execute(
            "UPDATE user_roles SET revoked_at = ? WHERE user_id = ?  AND revoked_at IS NULL",
            (now, user_id)
        )
        for role_id in body.role_ids:
            await db.execute(
                "INSERT INTO user_roles (id, user_id, role_id, granted_at, granted_by, valid_from) VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), user_id, role_id, now, principal.user_id, now)
            )
            
    return {"status": "updated"}


@router.post("/{user_id}/revoke-sessions")
async def revoke_user_sessions(
    user_id: str,
    state: AppState = Depends(get_state),
    principal: Principal = Depends(_require_supervisor),
) -> dict[str, Any]:
    db = state.db
    membership = await db.fetch_one("SELECT 1 FROM organization_members WHERE user_id = ? ", (user_id,))
    if not membership:
        raise HTTPException(status_code=404, detail="User not found")
        
    now = datetime.now(UTC).isoformat()
    await db.execute(
        "UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
        (now, user_id)
    )
    await db.execute(
        "UPDATE users SET token_version = token_version + 1, updated_at = ? WHERE id = ?",
        (now, user_id)
    )
    return {"status": "sessions_revoked"}
