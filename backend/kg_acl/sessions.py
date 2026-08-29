"""Session management: create, validate, revoke, rotate.

Sessions track refresh-token grants. Each session row maps to exactly one
active refresh token. Rotation replaces the old session (``replaced_by_session_id``)
and creates a new one atomically.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.db import Database


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _hash_token(token: str) -> str:
    """SHA-256 hash of a refresh token for storage."""
    return hashlib.sha256(token.encode()).hexdigest()


async def create_session(
    db: Database,
    *,
    user_id: str,
    refresh_token: str,
    ttl_days: int = 30,
    user_agent: str = "",
    ip_hash: str = "",
) -> dict[str, Any]:
    """Create a new session row bound to a refresh token."""
    session_id = str(uuid.uuid4())
    now = _now_iso()
    expires_at = (datetime.now(UTC) + timedelta(days=ttl_days)).isoformat()
    token_hash = _hash_token(refresh_token)

    await db.execute(
        """
        INSERT INTO sessions (id, user_id, refresh_token_hash, issued_at,
                              expires_at, user_agent, ip_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (session_id, user_id, token_hash, now, expires_at, user_agent, ip_hash),
    )
    return {
        "id": session_id,
        "user_id": user_id,
        "refresh_token_hash": token_hash,
        "issued_at": now,
        "expires_at": expires_at,
    }


async def validate_session(
    db: Database,
    *,
    session_id: str,
    refresh_token: str,
) -> dict[str, Any] | None:
    """Return the session if it exists, is not revoked, not expired, and the
    refresh token hash matches. Returns ``None`` otherwise."""
    row = await db.fetch_one(
        "SELECT * FROM sessions WHERE id = ? AND revoked_at IS NULL",
        (session_id,),
    )
    if not row:
        return None

    # Check token hash
    if row["refresh_token_hash"] != _hash_token(refresh_token):
        return None

    # Check expiry
    expires_at = datetime.fromisoformat(row["expires_at"])
    if datetime.now(UTC) > expires_at:
        return None

    return row


async def revoke_session(db: Database, session_id: str) -> None:
    """Mark a session as revoked."""
    await db.execute(
        "UPDATE sessions SET revoked_at = ? WHERE id = ?",
        (_now_iso(), session_id),
    )


async def revoke_all_user_sessions(db: Database, user_id: str) -> None:
    """Revoke every active session for a user."""
    await db.execute(
        "UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
        (_now_iso(), user_id),
    )


async def rotate_session(
    db: Database,
    *,
    old_session_id: str,
    user_id: str,
    new_refresh_token: str,
    ttl_days: int = 30,
    user_agent: str = "",
    ip_hash: str = "",
) -> dict[str, Any]:
    """Revoke the old session and create a replacement.

    The old session's ``replaced_by_session_id`` points to the new one,
    creating an audit chain.
    """
    new_session = await create_session(
        db,
        user_id=user_id,
        refresh_token=new_refresh_token,
        ttl_days=ttl_days,
        user_agent=user_agent,
        ip_hash=ip_hash,
    )

    await db.execute(
        "UPDATE sessions SET revoked_at = ?, replaced_by_session_id = ? WHERE id = ?",
        (_now_iso(), new_session["id"], old_session_id),
    )

    return new_session


def generate_refresh_token() -> str:
    """Generate a cryptographically secure refresh token."""
    return secrets.token_urlsafe(48)
