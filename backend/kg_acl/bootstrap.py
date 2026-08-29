"""First-run bootstrap: creates default admin user, supervisor role, and default scope.

Idempotent — safe to call on every startup. Skipped if the ``users``
table already has rows.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime

import bcrypt

from app.config import Settings
from app.db import Database

logger = logging.getLogger("synapse.bootstrap")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _hash_password(password: str) -> str:
    """Hash a password with bcrypt."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against a bcrypt hash."""
    return bcrypt.checkpw(password.encode(), password_hash.encode())


async def run_bootstrap(db: Database, settings: Settings) -> None:
    """Create the default admin user and supervisor role.

    Only runs if no users exist yet.
    """
    existing = await db.fetch_one("SELECT id FROM users LIMIT 1")
    if existing:
        logger.debug("Bootstrap skipped — users table already populated")
        return

    logger.info("=== RBAC Bootstrap: first-run setup ===")
    now = _now_iso()

    # 1. Create org-supervisor role
    supervisor_role_id = str(uuid.uuid4())
    await db.execute(
        """
        INSERT INTO roles (id, name, description, clearance, is_system_role, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            supervisor_role_id,
            "org-supervisor",
            "Full access to all approved knowledge scopes. "
            "Can review proposals, manage assignments, and configure RBAC.",
            "restricted",
            1,
            now,
            now,
        ),
    )
    logger.info("  Created role: org-supervisor (%s)", supervisor_role_id)

    # 2. Create default-member role (for non-supervisor users)
    member_role_id = str(uuid.uuid4())
    await db.execute(
        """
        INSERT INTO roles (id, name, description, clearance, is_system_role, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            member_role_id,
            "default-member",
            "Default role for members. Access limited to assigned knowledge scopes.",
            "internal",
            1,
            now,
            now,
        ),
    )
    logger.info("  Created role: default-member (%s)", member_role_id)

    # 3. Create bootstrap admin user (if credentials provided)
    email = settings.bootstrap_admin_email or "admin@synapse.local"
    password = settings.bootstrap_admin_password or "password"

    user_id = str(uuid.uuid4())
    password_hash = _hash_password(password)
    await db.execute(
        """
        INSERT INTO users (id, email, display_name, password_hash, auth_provider,
                           status, email_verified, is_active, token_version, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            email,
            settings.bootstrap_admin_display_name,
            password_hash,
            "local",
            "active",
            1,
            1,
            1,
            now,
            now,
        ),
    )
    logger.info("  Created admin user: %s (%s)", email, user_id)

    # 4. Membership
    await db.execute(
        """
        INSERT INTO organization_members (user_id, membership_status, joined_at, invited_by, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (user_id, "active", now, "system", now),
    )

    # 5. Grant org-supervisor role
    grant_id = str(uuid.uuid4())
    await db.execute(
        """
        INSERT INTO user_roles (id, user_id, role_id, granted_at, granted_by, valid_from)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (grant_id, user_id, supervisor_role_id, now, "system", now),
    )
    logger.info("  Granted org-supervisor role to %s", email)

    # 6. Optionally create API key linked to admin user
    api_key_value = settings.bootstrap_admin_api_key
    if api_key_value:
        key_hash = hashlib.sha256(api_key_value.encode()).hexdigest()
        masked = api_key_value[:8] + "..." + api_key_value[-4:] if len(api_key_value) > 12 else "***"
        await db.execute(
            """
            INSERT INTO api_keys (id, name, masked_key, key_hash, environment, user_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                "Bootstrap Admin API Key",
                masked,
                key_hash,
                "production",
                user_id,
                now,
            ),
        )
        logger.info("  Created API key for bootstrap admin")

    # 7. Create bootstrap scope
    scope_id = "scope-bootstrap-all"
    version_id = "ver-bootstrap-1"
    
    await db.execute(
        """
        INSERT INTO knowledge_scopes (
            id, name, description, status, sensitivity, 
            active_version_id, review_status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            scope_id, "Bootstrap Legacy Scope", 
            "Auto-generated scope covering the entire graph.",
            "approved", "internal", version_id, "current", now, now
        )
    )
    
    await db.execute(
        """
        INSERT INTO knowledge_scope_versions (
            id, scope_id, version_number, status, boundary_kind,
            boundary_spec_json, created_at, materialized_at, approved_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            version_id, scope_id, 1, "approved", "all_org",
            '{"kind": "all_org"}', now, now, now
        )
    )
    logger.info("  Created scope: scope-bootstrap-all")
    
    # 8. Removed: We no longer assign scope-bootstrap-all to default-member role to prevent RBAC leaks.

    # 9. Initialize authorization_versions
    await db.execute(
        """
        INSERT INTO authorization_versions (id, version, updated_at)
        VALUES (?, ?, ?)
        """,
        ("default", 1, now),
    )
    logger.info("  Initialized authorization_versions (version=1)")

    logger.info("=== RBAC Bootstrap complete ===")
