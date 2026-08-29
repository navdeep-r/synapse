from datetime import UTC
from typing import Any

from fastapi import HTTPException

from app.db import Database
from kg_acl.models import Principal
from kg_graphiti.service import GraphService


class AuthorizedGraphRepository:
    """Wraps GraphService to enforce RBAC using the Principal's active_scope_version_ids.
    
    Implements a fail-closed policy: if a user is not a supervisor, they can only see
    entities and edges that exist in the materialized boundaries of their active scopes.
    Raw Cypher queries are denied for non-supervisors.
    """

    def __init__(self, graph: GraphService, db: Database, principal: Principal):
        self.graph = graph
        self.db = db
        self.principal = principal

    @property
    def is_ready(self) -> bool:
        return self.graph.is_ready

    async def ensure_ready(self) -> None:
        await self.graph.ensure_ready()

    @property
    def counters(self) -> dict:
        return getattr(self.graph, "counters", {})

    async def _get_user_max_clearance(self) -> int:
        from datetime import datetime
        now = datetime.now(UTC).isoformat()
        max_clearance = await self.db.fetch_one("""
            SELECT MAX(CASE r.clearance 
                WHEN 'restricted' THEN 3 WHEN 'confidential' THEN 2 
                WHEN 'internal' THEN 1 ELSE 0 END) as max_clearance
            FROM user_roles ur
            JOIN roles r ON ur.role_id = r.id
            WHERE ur.user_id = ? 
              AND ur.revoked_at IS NULL AND ur.valid_from <= ? 
              AND (ur.valid_to IS NULL OR ur.valid_to > ?)
        """, (self.principal.user_id, now, now))
        return max_clearance["max_clearance"] if max_clearance and max_clearance["max_clearance"] else 0

    async def _get_allowed_entity_uuids(self) -> set[str] | None:
        if self.principal.is_org_supervisor:
            return None # Unused, fast path skips filtering
            
        if not self.principal.active_scope_version_ids:
            return set()
            
        placeholders = ",".join("?" for _ in self.principal.active_scope_version_ids)
        all_org_check = await self.db.fetch_one(
            f"SELECT 1 FROM knowledge_scope_versions WHERE id IN ({placeholders}) AND boundary_kind = 'all_org' LIMIT 1",
            self.principal.active_scope_version_ids
        )
        if all_org_check:
            return None

        user_max_clearance = await self._get_user_max_clearance()
            
        sql = f"""
            SELECT se.entity_uuid 
            FROM scope_entities se
            JOIN knowledge_scope_versions ksv ON se.scope_version_id = ksv.id
            JOIN knowledge_scopes ks ON ksv.scope_id = ks.id
            WHERE se.scope_version_id IN ({placeholders})
              AND CASE ks.sensitivity 
                  WHEN 'restricted' THEN 3 WHEN 'confidential' THEN 2 
                  WHEN 'internal' THEN 1 ELSE 0 END <= ?
        """
        params = [*self.principal.active_scope_version_ids, user_max_clearance]
        rows = await self.db.fetch_all(sql, params)
        return {r["entity_uuid"] for r in rows}

    async def _get_allowed_edge_uuids(self) -> set[str] | None:
        if self.principal.is_org_supervisor:
            return None
            
        if not self.principal.active_scope_version_ids:
            return set()
            
        placeholders = ",".join("?" for _ in self.principal.active_scope_version_ids)
        all_org_check = await self.db.fetch_one(
            f"SELECT 1 FROM knowledge_scope_versions WHERE id IN ({placeholders}) AND boundary_kind = 'all_org' LIMIT 1",
            self.principal.active_scope_version_ids
        )
        if all_org_check:
            return None

        user_max_clearance = await self._get_user_max_clearance()
            
        sql = f"""
            SELECT se.edge_uuid 
            FROM scope_edges se
            JOIN knowledge_scope_versions ksv ON se.scope_version_id = ksv.id
            JOIN knowledge_scopes ks ON ksv.scope_id = ks.id
            WHERE se.scope_version_id IN ({placeholders})
              AND CASE ks.sensitivity 
                  WHEN 'restricted' THEN 3 WHEN 'confidential' THEN 2 
                  WHEN 'internal' THEN 1 ELSE 0 END <= ?
        """
        params = [*self.principal.active_scope_version_ids, user_max_clearance]
        rows = await self.db.fetch_all(sql, params)
        return {r["edge_uuid"] for r in rows}

    # --- Wrappers for Reads ---

    async def entities(self, group_ids: list[str]) -> list[dict[str, Any]]:
        entities = await self.graph.entities(group_ids)
        if not self.principal.is_org_supervisor:
            allowed = await self._get_allowed_entity_uuids()
            if allowed is not None:
                entities = [e for e in entities if e["uuid"] in allowed]
        
        return entities

    async def edges(self, group_ids: list[str], *, include_invalid: bool = True) -> list[dict[str, Any]]:
        edges = await self.graph.edges(group_ids, include_invalid=include_invalid)
        if self.principal.is_org_supervisor:
            return edges
            
        allowed = await self._get_allowed_edge_uuids()
        if allowed is None:
            return edges
        return [e for e in edges if e["uuid"] in allowed]

    async def search(self, query: str, group_ids: list[str] | None = None, limit: int = 10):
        edges = await self.graph.search(query, group_ids=group_ids, limit=limit)
        if self.principal.is_org_supervisor:
            return edges
            
        allowed = await self._get_allowed_edge_uuids()
        if allowed is None:
            return edges
        return [
            e for e in edges 
            if (e.get("uuid") if isinstance(e, dict) else getattr(e, "uuid", None)) in allowed
        ]

    async def search_entities(self, query: str, group_ids: list[str]) -> list[dict[str, Any]]:
        """Search entities by name. Safe for non-supervisors."""
        cypher = (
            "MATCH (n:Entity) "
            "WHERE n.group_id = $group_id AND toLower(n.name) CONTAINS toLower($q) "
            "RETURN n.uuid AS uuid, n.name AS name, n.labels AS labels "
            "LIMIT 50"
        )
        rows = await self.graph.query_groups(cypher, group_ids=group_ids, q=query)
        if self.principal.is_org_supervisor:
            return rows
        allowed = await self._get_allowed_entity_uuids()
        if allowed is None:
            return rows
        return [r for r in rows if r.get("uuid") in allowed]

    async def entity_count(self, group_ids: list[str]) -> int:
        if self.principal.is_org_supervisor:
            return await self.graph.entity_count(group_ids)
        
        # Non-supervisor: fetch all allowed, then count those that actually exist in the groups.
        return len(await self.entities(group_ids))
        
    async def episode_count(self, group_ids: list[str]) -> int:
        if self.principal.is_org_supervisor:
            return await self.graph.episode_count(group_ids)
        
        # We don't track episode_uuid boundaries in RBAC currently (only entities and edges).
        # We fail closed or just deny this for non-supervisors if it's unsafe.
        raise HTTPException(
            status_code=403, 
            detail="Aggregations are not supported for non-supervisors"
        )
        
    async def episode_uuids(self, group_ids: list[str]) -> set[str]:
        if self.principal.is_org_supervisor:
            return await self.graph.episode_uuids(group_ids)
            
        # Same as above, no episode-level boundary enforcement yet.
        raise HTTPException(
            status_code=403, 
            detail="Direct episode queries are not supported for non-supervisors"
        )

    async def query(self, cypher: str, group_id: str | None = None, **params: Any) -> list[dict[str, Any]]:
        if not self.principal.is_org_supervisor:
            raise HTTPException(
                status_code=403, 
                detail="Raw Cypher queries are forbidden for non-supervisors"
            )
        return await self.graph.query(cypher, group_id=group_id, **params)

    async def query_groups(self, cypher: str, group_ids: list[str], **params: Any) -> list[dict[str, Any]]:
        if not self.principal.is_org_supervisor:
            raise HTTPException(
                status_code=403, 
                detail="Raw Cypher queries are forbidden for non-supervisors"
            )
        return await self.graph.query_groups(cypher, group_ids=group_ids, **params)

    # --- Pass-through for Writes (Assuming writes require supervisor role, which is enforced at the router level) ---
    
    async def add_episode(self, *args, **kwargs):
        if not self.principal.is_org_supervisor:
            raise HTTPException(status_code=403, detail="Writes are forbidden for non-supervisors")
        return await self.graph.add_episode(*args, **kwargs)
