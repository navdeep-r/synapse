from datetime import UTC, datetime

from app.db import Database


async def resolve_effective_scopes(
    db: Database,
    user_id: str,
    role_ids: list[str],
    is_supervisor: bool
) -> tuple[list[str], list[str]]:
    """
    Computes the effective knowledge scopes for a user based on their role grants,
    team grants, and direct assignments.
    
    Returns a tuple of (active_scope_ids, active_scope_version_ids).
    """
    if is_supervisor:
        # Supervisors get access to all approved scopes in the org
        rows = await db.fetch_all(
            "SELECT id, active_version_id FROM knowledge_scopes WHERE status = 'approved' AND active_version_id IS NOT NULL"
        )
        scope_ids = [row["id"] for row in rows]
        version_ids = [row["active_version_id"] for row in rows]
        return scope_ids, version_ids

    now = datetime.now(UTC).isoformat()
    
    # NEW: Get user's MAX clearance from their roles
    max_clearance = await db.fetch_one("""
        SELECT MAX(CASE r.clearance 
            WHEN 'restricted' THEN 3 WHEN 'confidential' THEN 2 
            WHEN 'internal' THEN 1 ELSE 0 END) as max_clearance
        FROM user_roles ur
        JOIN roles r ON ur.role_id = r.id
        WHERE ur.user_id = ? 
          AND ur.revoked_at IS NULL AND ur.valid_from <= ? 
          AND (ur.valid_to IS NULL OR ur.valid_to > ?)
    """, (user_id, now, now))
    
    user_max_clearance = max_clearance["max_clearance"] if max_clearance and max_clearance["max_clearance"] else 0
    
    # User's teams and their effective minimum clearance
    teams = await db.fetch_all(
        """
        WITH team_clearances AS (
            SELECT tm.team_id, 
                   MAX(CASE r.clearance 
                       WHEN 'restricted' THEN 3 WHEN 'confidential' THEN 2 
                       WHEN 'internal' THEN 1 ELSE 0 END) as max_clearance
            FROM team_members tm
            JOIN user_roles ur ON tm.user_id = ur.user_id
            JOIN roles r ON ur.role_id = r.id
            WHERE tm.removed_at IS NULL
              AND ur.revoked_at IS NULL AND ur.valid_from <= ? 
              AND (ur.valid_to IS NULL OR ur.valid_to > ?)
            GROUP BY tm.team_id
        )
        SELECT tc.team_id, tc.max_clearance 
        FROM team_clearances tc
        JOIN team_members tm ON tc.team_id = tm.team_id
        WHERE tm.user_id = ? AND tm.removed_at IS NULL
        """,
        (now, now, user_id)
    )
    
    # We will build the assignee conditions dynamically
    assignee_conditions = []
    params = [now, now]
    
    # User assignment
    assignee_conditions.append("(a.assignee_kind = 'user' AND a.assignee_id = ? AND CASE s.sensitivity WHEN 'restricted' THEN 3 WHEN 'confidential' THEN 2 WHEN 'internal' THEN 1 ELSE 0 END <= ?)")
    params.extend([user_id, user_max_clearance])
    
    # Role assignment
    if role_ids:
        placeholders = ",".join("?" for _ in role_ids)
        assignee_conditions.append(f"(a.assignee_kind = 'role' AND a.assignee_id IN ({placeholders}) AND CASE s.sensitivity WHEN 'restricted' THEN 3 WHEN 'confidential' THEN 2 WHEN 'internal' THEN 1 ELSE 0 END <= ?)")
        params.extend(role_ids)
        params.append(user_max_clearance)
        
    # Team assignment
    if teams:
        # For teams, we check if the team's minimum clearance is sufficient for the scope, AND the user's max clearance is sufficient
        for t in teams:
            team_id = t["team_id"]
            team_clearance = t["max_clearance"] or 0
            # Both team and user must have sufficient clearance -> actually we want max
            effective_clearance = max(user_max_clearance, team_clearance)
            assignee_conditions.append(f"(a.assignee_kind = 'team' AND a.assignee_id = ? AND CASE s.sensitivity WHEN 'restricted' THEN 3 WHEN 'confidential' THEN 2 WHEN 'internal' THEN 1 ELSE 0 END <= ?)")
            params.extend([team_id, effective_clearance])

    condition_str = " OR ".join(assignee_conditions)
    
    query = f"""
        SELECT DISTINCT a.scope_id, s.active_version_id 
        FROM scope_assignees a
        JOIN knowledge_scopes s ON s.id = a.scope_id
        WHERE a.revoked_at IS NULL
          AND a.valid_from <= ?
          AND (a.valid_to IS NULL OR a.valid_to >= ?)
          AND s.status = 'approved'
          AND s.active_version_id IS NOT NULL
          AND ( {condition_str} )
    """
    
    rows = await db.fetch_all(query, params)
    
    scope_ids = [row["scope_id"] for row in rows]
    version_ids = [row["active_version_id"] for row in rows]
    
    return scope_ids, version_ids
