"""Episode receipts using intent-then-commit (R3).

`graphiti.add_episode()` and the SQLite write are not one transaction. Writing
the receipt *before* calling Graphiti means a crash in between leaves a
detectable `pending` row instead of a silent gap between the two stores.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.db import Database
from kg_contracts import EpisodePayload, EpisodeReceipt, EpisodeStatus


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


class ReceiptStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def record_intent(self, payload: EpisodePayload) -> None:
        """Persist the intention to write an episode, before Graphiti is called."""
        await self._db.execute(
            "INSERT INTO episode_receipts(episode_id, document_id, group_id, status, "
            "chunk_ids_json, created_at) VALUES(?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(episode_id) DO UPDATE SET status = excluded.status",
            (
                payload.episode_id,
                payload.document_id,
                payload.group_id or "synapse",
                EpisodeStatus.PENDING.value,
                json.dumps(payload.chunk_ids),
                _iso(datetime.now(UTC)),
            ),
        )

    async def commit(
        self,
        episode_id: str,
        *,
        graph_uuid: str | None,
        nodes_created: int,
        edges_created: int,
        llm_calls: int,
        latency_ms: int,
    ) -> None:
        await self._db.execute(
            "UPDATE episode_receipts SET status = ?, committed_at = ?, graph_uuid = ?, "
            "nodes_created = ?, edges_created = ?, llm_calls = ?, latency_ms = ?, error = NULL "
            "WHERE episode_id = ?",
            (
                EpisodeStatus.COMMITTED.value,
                _iso(datetime.now(UTC)),
                graph_uuid,
                nodes_created,
                edges_created,
                llm_calls,
                latency_ms,
                episode_id,
            ),
        )

    async def fail(self, episode_id: str, error: str) -> None:
        await self._db.execute(
            "UPDATE episode_receipts SET status = ?, error = ? WHERE episode_id = ?",
            (EpisodeStatus.FAILED.value, error[:500], episode_id),
        )

    async def all(self) -> list[EpisodeReceipt]:
        rows = await self._db.fetch_all("SELECT * FROM episode_receipts ORDER BY created_at")
        return [_to_receipt(row) for row in rows]

    async def by_status(self, status: EpisodeStatus) -> list[EpisodeReceipt]:
        rows = await self._db.fetch_all(
            "SELECT * FROM episode_receipts WHERE status = ? ORDER BY created_at", (status.value,)
        )
        return [_to_receipt(row) for row in rows]

    async def group_ids(self) -> list[str]:
        return ["synapse"]

    async def counts(self) -> dict[str, int]:
        rows = await self._db.fetch_all(
            "SELECT status, COUNT(*) AS n FROM episode_receipts GROUP BY status"
        )
        return {row["status"]: int(row["n"]) for row in rows}

    async def totals(self) -> dict[str, int]:
        row = await self._db.fetch_one(
            "SELECT COALESCE(SUM(llm_calls), 0) AS llm_calls, "
            "COALESCE(SUM(nodes_created), 0) AS nodes, "
            "COALESCE(SUM(edges_created), 0) AS edges, "
            "COALESCE(AVG(latency_ms), 0) AS avg_latency FROM episode_receipts "
            "WHERE status = 'committed'"
        )
        return {
            "llm_calls": int(row["llm_calls"]) if row else 0,
            "nodes": int(row["nodes"]) if row else 0,
            "edges": int(row["edges"]) if row else 0,
            "avg_latency_ms": int(row["avg_latency"]) if row else 0,
        }


def _to_receipt(row: dict) -> EpisodeReceipt:
    return EpisodeReceipt(
        episode_id=row["episode_id"],
        document_id=row["document_id"],
        status=EpisodeStatus(row["status"]),
        chunk_ids=json.loads(row["chunk_ids_json"] or "[]"),
        created_at=datetime.fromisoformat(row["created_at"]),
        committed_at=(
            datetime.fromisoformat(row["committed_at"]) if row.get("committed_at") else None
        ),
        graph_uuid=row.get("graph_uuid"),
        nodes_created=int(row.get("nodes_created") or 0),
        edges_created=int(row.get("edges_created") or 0),
        llm_calls=int(row.get("llm_calls") or 0),
        latency_ms=int(row.get("latency_ms") or 0),
        error=row.get("error"),
    )
