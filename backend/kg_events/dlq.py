"""Dead-letter queue.

Every stage gets one, and the rule is the same everywhere: a message either makes
progress, is retried, or lands here — it never blocks the partition and it is
never dropped. A stalled partition is the worst failure mode in this design,
because entity-keyed partitioning means one poison message would halt every
update for every entity that hashes to that partition, silently, while the rest
of the pipeline looks healthy.

The full original frame is stored, not just the error, so a fixed connector can
replay the message rather than needing the source system to re-emit it. The
lineage `trace_id` is denormalised onto the row so a dead letter can be joined
back to the source event without decoding the payload.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime

from app.db import Database
from kg_events.models import CanonicalEvent


class DeadLetterQueue:
    def __init__(self, db: Database, *, topic: str) -> None:
        self._db = db
        self._topic = topic

    async def put(
        self,
        *,
        stage: str,
        payload: bytes,
        error: Exception | str,
        event: CanonicalEvent | None = None,
        attempts: int = 0,
    ) -> None:
        error_type = type(error).__name__ if isinstance(error, Exception) else "Error"
        await self._db.execute(
            "INSERT INTO dead_letters(topic, stage, idempotency_key, event_id, "
            "trace_id, partition_key, error_type, error, payload_b64, attempts, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self._topic,
                stage,
                event.idempotency_key if event else None,
                event.event_id if event else None,
                event.lineage.trace_id if event else None,
                event.partition_key if event else None,
                error_type,
                str(error)[:1000],
                base64.b64encode(payload).decode("ascii"),
                attempts,
                datetime.now(UTC).isoformat(),
            ),
        )

    async def pending(self, stage: str | None = None) -> list[dict]:
        if stage:
            return await self._db.fetch_all(
                "SELECT * FROM dead_letters WHERE resolved_at IS NULL AND stage = ? "
                "ORDER BY created_at DESC",
                (stage,),
            )
        return await self._db.fetch_all(
            "SELECT * FROM dead_letters WHERE resolved_at IS NULL ORDER BY created_at DESC"
        )

    async def counts_by_stage(self) -> dict[str, int]:
        """Feeds alerting. A non-zero count on any stage is a page, not a dashboard."""
        rows = await self._db.fetch_all(
            "SELECT stage, COUNT(*) AS n FROM dead_letters WHERE resolved_at IS NULL GROUP BY stage"
        )
        return {row["stage"]: int(row["n"]) for row in rows}

    async def frame(self, dead_letter_id: int) -> bytes | None:
        row = await self._db.fetch_one(
            "SELECT payload_b64 FROM dead_letters WHERE id = ?", (dead_letter_id,)
        )
        return base64.b64decode(row["payload_b64"]) if row else None

    async def resolve(self, dead_letter_id: int) -> None:
        await self._db.execute(
            "UPDATE dead_letters SET resolved_at = ? WHERE id = ?",
            (datetime.now(UTC).isoformat(), dead_letter_id),
        )
