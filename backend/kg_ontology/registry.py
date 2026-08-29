"""Ontology registry backed by SQLite.

Relation-type constraints (`max_active_outgoing`, `temporal`, `overlap_allowed`)
are the schema the graph is validated against; changing one is a migration with
an impact report, not an edit.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from app.db import Database
from kg_graphiti.ontology import ENTITY_TYPE_DESCRIPTIONS, RELATION_DEFAULTS
from kg_ontology.impact import assess_impact


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


class OntologyRegistry:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def seed(self) -> None:
        """Install the default ontology once; user edits are never overwritten."""
        for entity in ENTITY_TYPE_DESCRIPTIONS:
            await self._db.execute(
                "INSERT INTO ontology_entity_types(id, name, description) VALUES(?, ?, ?) "
                "ON CONFLICT(id) DO NOTHING",
                (entity["id"], entity["name"], entity["description"]),
            )
        for relation in RELATION_DEFAULTS:
            await self._db.execute(
                "INSERT INTO ontology_relation_types(relation_type, version, source_types_json, "
                "target_types_json, max_active_outgoing, temporal, overlap_allowed) "
                "VALUES(?, ?, ?, ?, ?, ?, ?) ON CONFLICT(relation_type) DO NOTHING",
                (
                    relation["relation_type"],
                    relation["version"],
                    json.dumps(relation["source_types"]),
                    json.dumps(relation["target_types"]),
                    relation["max_active_outgoing"],
                    int(bool(relation["temporal"])),
                    int(bool(relation["overlap_allowed"])),
                ),
            )

    # --- Entity types ------------------------------------------------------

    async def entity_types(self) -> list[dict]:
        rows = await self._db.fetch_all("SELECT * FROM ontology_entity_types ORDER BY name")
        return [{"id": r["id"], "name": r["name"], "description": r["description"]} for r in rows]

    async def update_entity_description(self, type_id: str, description: str) -> dict | None:
        await self._db.execute(
            "UPDATE ontology_entity_types SET description = ? WHERE id = ?",
            (description, type_id),
        )
        row = await self._db.fetch_one(
            "SELECT * FROM ontology_entity_types WHERE id = ?", (type_id,)
        )
        if not row:
            return None
        return {"id": row["id"], "name": row["name"], "description": row["description"]}

    # --- Relation types ----------------------------------------------------

    async def relation_types(self) -> list[dict]:
        rows = await self._db.fetch_all(
            "SELECT * FROM ontology_relation_types ORDER BY relation_type"
        )
        return [_relation_payload(row) for row in rows]

    async def relation_type(self, relation_type: str) -> dict | None:
        row = await self._db.fetch_one(
            "SELECT * FROM ontology_relation_types WHERE relation_type = ?", (relation_type,)
        )
        return _relation_payload(row) if row else None

    # --- Change proposals / migrations -------------------------------------

    async def propose_change(
        self,
        *,
        relation_type: str,
        field: str,
        old_value: object,
        new_value: object,
        justification: str,
        affected_edges: list[dict] | None = None,
    ) -> dict:
        impact, report = assess_impact(field, old_value, new_value, affected_edges or [])
        change_id = f"mig-{uuid.uuid4().hex[:10]}"
        await self._db.execute(
            "INSERT INTO ontology_changes(id, relation_type, field_to_change, old_value_json, "
            "new_value_json, justification, proposed_by, status, impact, impact_report, "
            "affected_edges_json, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                change_id,
                relation_type,
                field,
                json.dumps(old_value),
                json.dumps(new_value),
                justification,
                "console",
                "pending",
                impact,
                report,
                json.dumps(affected_edges or []),
                _iso(datetime.now(UTC)),
            ),
        )
        row = await self._db.fetch_one("SELECT * FROM ontology_changes WHERE id = ?", (change_id,))
        return _migration_payload(row) if row else {}

    async def migrations(self) -> list[dict]:
        rows = await self._db.fetch_all(
            "SELECT * FROM ontology_changes ORDER BY created_at DESC, rowid DESC"
        )
        return [_migration_payload(row) for row in rows]

    async def resolve_migration(self, change_id: str, approve: bool) -> dict | None:
        row = await self._db.fetch_one("SELECT * FROM ontology_changes WHERE id = ?", (change_id,))
        if not row:
            return None

        status = "approved" if approve else "rejected"
        await self._db.execute(
            "UPDATE ontology_changes SET status = ?, resolved_at = ? WHERE id = ?",
            (status, _iso(datetime.now(UTC)), change_id),
        )

        if approve:
            await self._apply(row)

        updated = await self._db.fetch_one(
            "SELECT * FROM ontology_changes WHERE id = ?", (change_id,)
        )
        return _migration_payload(updated) if updated else None

    async def _apply(self, change: dict) -> None:
        """Approving a migration writes the constraint and bumps the version."""
        field = change["field_to_change"]
        value = json.loads(change["new_value_json"] or "null")
        column = {
            "max_active_outgoing": "max_active_outgoing",
            "temporal": "temporal",
            "overlap_allowed": "overlap_allowed",
        }.get(field)
        if column is None:
            return

        coerced = int(value) if column == "max_active_outgoing" else int(bool(value))
        await self._db.execute(
            f"UPDATE ontology_relation_types SET {column} = ?, version = version + 1 "
            "WHERE relation_type = ?",
            (coerced, change["relation_type"]),
        )


def _relation_payload(row: dict) -> dict:
    return {
        "relation_type": row["relation_type"],
        "version": int(row["version"]),
        "source_types": json.loads(row["source_types_json"] or "[]"),
        "target_types": json.loads(row["target_types_json"] or "[]"),
        "max_active_outgoing": int(row["max_active_outgoing"]),
        "temporal": bool(row["temporal"]),
        "overlap_allowed": bool(row["overlap_allowed"]),
    }


def _migration_payload(row: dict) -> dict:
    return {
        "id": row["id"],
        "relation_type": row["relation_type"],
        "field_to_change": row["field_to_change"],
        "old_value": json.loads(row["old_value_json"] or "null"),
        "new_value": json.loads(row["new_value_json"] or "null"),
        "justification": row["justification"],
        "proposed_by": row["proposed_by"],
        "status": row["status"],
        "impact": row["impact"],
        "impact_report": row["impact_report"],
        "affected_edges": json.loads(row["affected_edges_json"] or "[]"),
        "created_at": row["created_at"],
        "resolved_at": row["resolved_at"],
    }
