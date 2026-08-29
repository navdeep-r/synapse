"""JWT token handling and ``require_principal`` FastAPI dependency.

``require_principal`` is the single injection point for authentication.
It replaces the old ``require_auth`` and resolves every request to a
``Principal`` (or rejects with 401).

Resolution order:
1. Bearer JWT access token → decode → load user + roles → Principal.
2. Bearer API key → hash-lookup in ``api_keys`` → resolve ``user_id`` → Principal.
3. Legacy ``SYNAPSE_AUTH_TOKEN`` shared secret (transition mode only).
4. No auth header + no ``SYNAPSE_AUTH_TOKEN`` + transition mode → bootstrap supervisor.
5. Otherwise → 401.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from fastapi import HTTPException, Request, Depends

from app.db import Database
from kg_acl.models import Principal, Sensitivity
from kg_acl.resolver import resolve_effective_scopes

logger = logging.getLogger("synapse.auth")

# JWT algorithm
_ALGORITHM = "HS256"


# ---------------------------------------------------------------------------
# Token issuance
# ---------------------------------------------------------------------------


def issue_access_token(
    *,
    user_id: str,
    session_id: str,
    token_version: int,
    secret: str,
    ttl_minutes: int = 15,
) -> str:
    """Issue a short-lived JWT access token."""
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "session_id": session_id,
        "token_version": token_version,
        "purpose": "access",
        "iat": now,
        "exp": now + timedelta(minutes=ttl_minutes),
    }
    return jwt.encode(payload, secret, algorithm=_ALGORITHM)


def issue_refresh_token(
    *,
    user_id: str,
    session_id: str,
    secret: str,
    ttl_days: int = 30,
) -> str:
    """Issue a long-lived JWT refresh token.

    The refresh token is also stored as a SHA-256 hash in the ``sessions``
    table, so even if it leaks, it cannot be used without the matching
    session row.
    """
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "session_id": session_id,
        "purpose": "refresh",
        "iat": now,
        "exp": now + timedelta(days=ttl_days),
    }
    return jwt.encode(payload, secret, algorithm=_ALGORITHM)


def decode_token(token: str, secret: str) -> dict[str, Any]:
    """Decode and verify a JWT, raising 401 on any failure."""
    try:
        return jwt.decode(token, secret, algorithms=[_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")


# ---------------------------------------------------------------------------
# Principal resolution helpers
# ---------------------------------------------------------------------------


async def _load_user(db: Database, user_id: str) -> dict[str, Any] | None:
    """Load a user row, returning None if not found or inactive."""
    row = await db.fetch_one(
        "SELECT * FROM users WHERE id = ? AND is_active = 1", (user_id,)
    )
    return row


async def _load_roles(db: Database, user_id: str) -> list[dict[str, Any]]:
    """Load active role grants for a user in an org."""
    now = datetime.now(UTC).isoformat()
    return await db.fetch_all(
        """
        SELECT r.id, r.name, r.clearance, r.is_system_role
        FROM user_roles ur
        JOIN roles r ON r.id = ur.role_id
        WHERE ur.user_id = ?
          AND ur.revoked_at IS NULL
          AND ur.valid_from <= ?
          AND (ur.valid_to IS NULL OR ur.valid_to >= ?)
        """,
        (user_id, now, now),
    )


async def _build_principal(
    db: Database,
    user: dict[str, Any],
    settings: Any,
    session_id: str | None = None,
) -> Principal:
    """Assemble a Principal from a user row + role grants."""
    roles = await _load_roles(db, user["id"])
    role_ids = [r["id"] for r in roles]
    is_supervisor = any(r["name"] == "org-supervisor" for r in roles)

    # Clearance = max across all roles; supervisor gets RESTRICTED
    if is_supervisor:
        clearance = Sensitivity.RESTRICTED
    elif roles:
        clearance = max(
            (Sensitivity.parse(r["clearance"]) for r in roles),
            default=Sensitivity.INTERNAL,
        )
    else:
        clearance = Sensitivity.INTERNAL

    # Phase 3: compute active scopes
    active_scope_ids, active_scope_version_ids = await resolve_effective_scopes(
        db, user["id"], role_ids, is_supervisor
    )

    if not active_scope_ids and settings.rbac_transition_mode:
        active_scope_ids = ["scope-bootstrap-all"]
        active_scope_version_ids = ["ver-bootstrap-1"]

    return Principal(
        user_id=user["id"],
        email=user["email"],
        display_name=user.get("display_name") or user["email"],
        role_ids=role_ids,
        clearance=clearance,
        is_org_supervisor=is_supervisor,
        active_scope_ids=active_scope_ids,
        active_scope_version_ids=active_scope_version_ids,
        session_id=session_id,
        token_version=user.get("token_version", 1),
    )


async def _resolve_api_key(db: Database, settings: Any, bearer: str) -> Principal | None:
    """Resolve an API key to a Principal, or return None."""
    key_hash = hashlib.sha256(bearer.encode()).hexdigest()
    row = await db.fetch_one(
        "SELECT * FROM api_keys WHERE key_hash = ?", (key_hash,)
    )
    if not row or not row.get("user_id"):
        return None

    user = await _load_user(db, row["user_id"])
    if not user:
        return None

    return await _build_principal(db, user, settings)


def _make_bootstrap_supervisor() -> Principal:
    """Synthetic supervisor principal for transition-mode fallback."""
    return Principal(
        user_id="bootstrap-transition",
        email="transition@synapse.local",
        display_name="Transition Mode User",
        role_ids=["transition-supervisor"],
        clearance=Sensitivity.RESTRICTED,
        is_org_supervisor=True,
        active_scope_ids=[],
        active_scope_version_ids=[],
        session_id=None,
        token_version=0,
    )


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


async def require_principal(request: Request) -> Principal:
    """FastAPI dependency that resolves every request to a ``Principal``.

    Replaces ``require_auth``. When ``rbac_transition_mode`` is ``True``,
    unauthenticated requests and legacy shared-secret tokens are accepted
    as the bootstrap supervisor.
    """
    from app.state import AppState

    state: AppState = request.app.state.synapse
    settings = state.settings
    db = state.db

    header = request.headers.get("authorization", "")
    bearer = header[7:].strip() if header.lower().startswith("bearer ") else ""

    # 1. Try JWT access token
    jwt_principal: Principal | None = None
    jwt_error: str | None = None

    if bearer:
        try:
            payload = decode_token(bearer, settings.jwt_secret)
            if payload.get("purpose") != "access":
                jwt_error = "Not an access token"
            else:
                user = await _load_user(db, payload["sub"])
                if not user:
                    jwt_error = "User not found or inactive"
                elif user.get("token_version", 1) != payload.get("token_version", 1):
                    jwt_error = "Token version mismatch"
                else:
                    jwt_principal = await _build_principal(db, user, settings, session_id=payload.get("session_id"))
        except HTTPException:
            # JWT decode failed — the bearer might be a legacy token or API key
            jwt_error = "JWT decode failed"
            logger.debug("JWT decode failed, trying other methods")
        except Exception:
            jwt_error = "JWT processing error"
            logger.debug("JWT processing error, trying other methods", exc_info=True)

    # If JWT succeeded, return immediately
    if jwt_principal is not None:
        return jwt_principal

    # If JWT decoded successfully but had a downstream error (user issues),
    # that's a hard fail — don't fall through to weaker auth methods
    if jwt_error and jwt_error not in ("JWT decode failed", "JWT processing error", "Not an access token"):
        raise HTTPException(status_code=401, detail=jwt_error)

    # 2. Try API key
    if bearer:
        principal = await _resolve_api_key(db, settings, bearer)
        if principal is not None:
            return principal

    # 3. Legacy shared-secret fallback (transition mode only)
    if settings.rbac_transition_mode and settings.auth_token and bearer == settings.auth_token:
        logger.debug("Legacy SYNAPSE_AUTH_TOKEN matched — transition-mode supervisor")
        return _make_bootstrap_supervisor()

    # 4. No auth at all — transition mode allows it (preserves pre-RBAC behavior)
    if settings.rbac_transition_mode and not settings.auth_token and not bearer:
        return _make_bootstrap_supervisor()

    # 5. Transition mode + auth_token set but bearer doesn't match
    if settings.rbac_transition_mode and settings.auth_token and not bearer:
        logger.error(f"require_principal failed: Transition mode + auth_token but no bearer")
        raise HTTPException(status_code=401, detail="Authentication required")

    logger.error(f"require_principal failed: Fallback to 401. Bearer present? {bool(bearer)}. jwt_error: {jwt_error}")
    raise HTTPException(status_code=401, detail="Authentication required")

async def get_authorized_graph(
    request: Request,
    principal: Principal = Depends(require_principal),
):
    from app.state import AppState
    from kg_acl.authorized_graph import AuthorizedGraphRepository
    state: AppState = request.app.state.synapse
    return AuthorizedGraphRepository(state.graph, state.db, principal)


async def require_supervisor(
    principal: Principal = Depends(require_principal),
) -> Principal:
    """Dependency that requires the principal to be an organization supervisor."""
    if not principal.is_org_supervisor:
        raise HTTPException(status_code=403, detail="Supervisor clearance required")
    return principal

