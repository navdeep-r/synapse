"""Authentication endpoints: login, refresh, logout, me.

These endpoints are mounted **without** the ``require_principal`` guard
because login and refresh must work for unauthenticated users.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta
import uuid
import bcrypt

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.state import AppState, get_state
from kg_acl.bootstrap import verify_password
from kg_acl.models import Principal
from kg_acl.principal import (
    decode_token,
    issue_access_token,
    issue_refresh_token,
    require_principal,
)
from kg_acl.sessions import (
    create_session,
    generate_refresh_token,
    revoke_session,
    rotate_session,
    validate_session,
)

logger = logging.getLogger("synapse.auth")

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Request / response shapes
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


class CompleteSignupRequest(BaseModel):
    email: str
    passcode: str
    password: str

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

_REFRESH_COOKIE = "synapse_refresh_token"


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    state: AppState = Depends(get_state),
) -> TokenResponse:
    """Authenticate with email + password → access token + refresh cookie."""
    db = state.db
    settings = state.settings
    now_iso = datetime.now(UTC).isoformat()

    # 1. Find user by email
    user = await db.fetch_one(
        "SELECT * FROM users WHERE email = ? AND is_active = 1", (body.email,)
    )
    if not user or not user.get("password_hash"):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if user.get("status") != "active" or not user.get("email_verified"):
        raise HTTPException(status_code=401, detail="Account is not active or email not verified")

    # 2. Verify password
    if not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # 3. Resolve org membership (No org_id in current schema)
    membership = await db.fetch_one(
        "SELECT user_id FROM organization_members WHERE user_id = ? AND membership_status = 'active'",
        (user["id"],),
    )
    if not membership:
        raise HTTPException(status_code=403, detail="No active organization membership")

    org_id = "default"  # Using default since there's no org_id in schema, or pass default to tokens

    # Check for active role grants
    active_grants = await db.fetch_all(
        """
        SELECT 1 FROM user_roles 
        WHERE user_id = ?
          AND valid_from <= ? 
          AND (valid_to IS NULL OR valid_to > ?)
          AND revoked_at IS NULL
        """,
        (user["id"], now_iso, now_iso)
    )

    if not active_grants:
        if not settings.rbac_transition_mode:
            raise HTTPException(status_code=403, detail="No active role grants")
        
        # Check bootstrap scope in transition mode
        bootstrap = await db.fetch_one("SELECT 1 FROM knowledge_scopes WHERE id = 'scope-bootstrap-all'")
        if not bootstrap:
            raise HTTPException(status_code=403, detail="No active role grants and bootstrap scope missing")

    # 4. Create session + tokens
    refresh_raw = generate_refresh_token()
    session = await create_session(
        db,
        user_id=user["id"],
        refresh_token=refresh_raw,
        ttl_days=settings.refresh_token_ttl_days,
        user_agent=request.headers.get("user-agent", ""),
        ip_hash=hashlib.sha256(
            (request.client.host if request.client else "unknown").encode()
        ).hexdigest()[:16],
    )

    access = issue_access_token(
        user_id=user["id"],
        session_id=session["id"],
        token_version=user.get("token_version", 1),
        secret=settings.jwt_secret,
        ttl_minutes=settings.access_token_ttl_minutes,
    )

    # 5. Set refresh token as httpOnly cookie
    response.set_cookie(
        key=_REFRESH_COOKIE,
        value=refresh_raw,
        httponly=True,
        secure=False,  # localhost development; production should use True
        samesite="lax",
        max_age=settings.refresh_token_ttl_days * 86400,
        path="/api/v1/auth",
    )

    await db.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now_iso, user["id"]))

    logger.info("User %s logged in (session %s)", body.email, session["id"])

    return TokenResponse(
        access_token=access,
        expires_in=settings.access_token_ttl_minutes * 60,
    )


@router.post("/refresh")
async def refresh(
    request: Request,
    response: Response,
    state: AppState = Depends(get_state),
) -> TokenResponse:
    """Rotate the refresh token and issue a new access token."""
    db = state.db
    settings = state.settings

    refresh_raw = request.cookies.get(_REFRESH_COOKIE)
    if not refresh_raw:
        raise HTTPException(status_code=401, detail="No refresh token")

    try:
        payload = decode_token(refresh_raw, settings.jwt_secret)
    except HTTPException:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    if payload.get("purpose") != "refresh":
        raise HTTPException(status_code=401, detail="Not a refresh token")

    session_id = payload.get("session_id")
    if not session_id:
        raise HTTPException(status_code=401, detail="Missing session_id in token")

    session = await validate_session(db, session_id=session_id, refresh_token=refresh_raw)
    if not session:
        raise HTTPException(status_code=401, detail="Session invalid or expired")

    user = await db.fetch_one(
        "SELECT * FROM users WHERE id = ? AND is_active = 1", (session["user_id"],)
    )
    if not user:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    new_refresh_raw = generate_refresh_token()
    new_session = await rotate_session(
        db,
        old_session_id=session_id,
        user_id=user["id"],
        new_refresh_token=new_refresh_raw,
        ttl_days=settings.refresh_token_ttl_days,
        user_agent=request.headers.get("user-agent", ""),
    )

    access = issue_access_token(
        user_id=user["id"],
        session_id=new_session["id"],
        token_version=user.get("token_version", 1),
        secret=settings.jwt_secret,
        ttl_minutes=settings.access_token_ttl_minutes,
    )

    response.set_cookie(
        key=_REFRESH_COOKIE,
        value=new_refresh_raw,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=settings.refresh_token_ttl_days * 86400,
        path="/api/v1/auth",
    )

    return TokenResponse(
        access_token=access,
        expires_in=settings.access_token_ttl_minutes * 60,
    )


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    state: AppState = Depends(get_state),
) -> dict:
    """Revoke the current session and clear the refresh cookie."""
    db = state.db
    settings = state.settings

    refresh_raw = request.cookies.get(_REFRESH_COOKIE)
    if refresh_raw:
        try:
            payload = decode_token(refresh_raw, settings.jwt_secret)
            session_id = payload.get("session_id")
            if session_id:
                await revoke_session(db, session_id)
        except Exception:
            pass  # Best-effort revocation

    response.delete_cookie(key=_REFRESH_COOKIE, path="/api/v1/auth")

    return {"status": "logged_out"}


@router.get("/me")
async def me(principal: Principal = Depends(require_principal)) -> dict:
    """Return the authenticated principal's information."""
    return principal.to_dict()


@router.get("/sessions")
async def get_sessions(
    request: Request,
    state: AppState = Depends(get_state),
    principal: Principal = Depends(require_principal),
) -> list[dict]:
    db = state.db
    sessions = await db.fetch_all(
        "SELECT id, issued_at, expires_at, revoked_at, user_agent, ip_hash FROM sessions WHERE user_id = ? ORDER BY issued_at DESC",
        (principal.user_id,)
    )
    return sessions


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    state: AppState = Depends(get_state),
    principal: Principal = Depends(require_principal),
) -> dict:
    db = state.db
    session = await db.fetch_one("SELECT * FROM sessions WHERE id = ? AND user_id = ?", (session_id, principal.user_id))
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    await revoke_session(db, session_id)
    return {"status": "revoked"}


@router.delete("/sessions")
async def delete_all_sessions(
    request: Request,
    state: AppState = Depends(get_state),
    principal: Principal = Depends(require_principal),
) -> dict:
    db = state.db
    auth_header = request.headers.get("authorization", "")
    current_session_id = None
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        try:
            payload = decode_token(token, state.settings.jwt_secret)
            current_session_id = payload.get("session_id")
        except:
            pass
            
    now = datetime.now(UTC).isoformat()
    if current_session_id:
        await db.execute(
            "UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND id != ? AND revoked_at IS NULL",
            (now, principal.user_id, current_session_id)
        )
    else:
        await db.execute(
            "UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
            (now, principal.user_id)
        )
    return {"status": "all_other_sessions_revoked"}


@router.post("/forgot-password")
async def forgot_password(
    body: ForgotPasswordRequest,
    state: AppState = Depends(get_state),
) -> dict:
    db = state.db
    user = await db.fetch_one("SELECT * FROM users WHERE email = ? AND status = 'active' AND email_verified = 1", (body.email,))
    if user:
        token = str(uuid.uuid4())
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        expires = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        await db.execute(
            "UPDATE users SET password_reset_token_hash = ?, password_reset_token_expires = ? WHERE id = ?",
            (token_hash, expires, user["id"])
        )
        logger.info("Password reset requested for %s. Magic link: /reset-password?token=%s", body.email, token)
    return {"status": "email_sent"}


@router.post("/reset-password")
async def reset_password(
    body: ResetPasswordRequest,
    state: AppState = Depends(get_state),
) -> dict:
    db = state.db
    token_hash = hashlib.sha256(body.token.encode()).hexdigest()
    now = datetime.now(UTC).isoformat()
    
    user = await db.fetch_one(
        "SELECT * FROM users WHERE password_reset_token_hash = ? AND password_reset_token_expires > ?",
        (token_hash, now)
    )
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
        
    new_hash = bcrypt.hashpw(body.new_password.encode(), bcrypt.gensalt(12)).decode()
    
    await db.execute(
        """
        UPDATE users SET 
            password_hash = ?, 
            token_version = token_version + 1,
            password_reset_token_hash = NULL,
            password_reset_token_expires = NULL,
            updated_at = ?
        WHERE id = ?
        """,
        (new_hash, now, user["id"])
    )
    return {"status": "password_reset"}


@router.get("/accept-invitation")
async def accept_invitation(
    token: str,
    state: AppState = Depends(get_state),
) -> dict:
    db = state.db
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    now = datetime.now(UTC).isoformat()
    
    user = await db.fetch_one(
        "SELECT email FROM users WHERE invitation_token_hash = ? AND invitation_token_expires > ?",
        (token_hash, now)
    )
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired invitation token")
    return {"email": user["email"]}


@router.post("/complete-signup")
async def complete_signup(
    body: CompleteSignupRequest,
    state: AppState = Depends(get_state),
) -> dict:
    db = state.db
    now = datetime.now(UTC).isoformat()
    
    user = await db.fetch_one(
        "SELECT * FROM users WHERE email = ? AND status = 'pending_invitation'",
        (body.email,)
    )
    if not user:
        raise HTTPException(status_code=400, detail="User not found or already activated")
        
    if not user.get("password_hash"):
        raise HTTPException(status_code=400, detail="Invalid passcode state")
        
    if not verify_password(body.passcode.upper(), user["password_hash"]):
        raise HTTPException(status_code=400, detail="Invalid passcode")
        
    new_hash = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt(12)).decode()
    
    await db.execute(
        """
        UPDATE users SET 
            password_hash = ?, 
            status = 'active',
            email_verified = 1,
            accepted_at = ?,
            invitation_token_hash = NULL,
            invitation_token_expires = NULL,
            updated_at = ?
        WHERE id = ?
        """,
        (new_hash, now, now, user["id"])
    )
    return {"status": "signup_complete"}
