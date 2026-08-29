"""Idempotent consumption, as an extension of Synapse's intent-then-commit pattern.

`ReceiptStore` already answers "did this graph write land?" by writing a PENDING
row before calling Graphiti. This answers the question one level further out:
"have we already applied this logical change?" — which is what at-least-once
delivery forces you to ask.

The claim protocol, in order of the checks:

1. **STALE** — the entity's watermark is at or beyond this event's sequence, so a
   newer delta already won. Drop it. Partition ordering handles the common case,
   but a connector restart or a consumer-group rebalance can legitimately replay
   an older offset after a newer one was applied.
2. **DUPLICATE** — a committed receipt already exists for this key. Skip, and say
   so; this is the redelivery path and it should be visible in metrics, not
   silent.
3. **IN_FLIGHT** — a fresh PENDING row exists, so another worker holds the claim.
   Defer rather than racing it.
4. **RETRY** — a PENDING row exists but has gone stale, which means the worker
   that claimed it died. Take the claim over.
5. **NEW** — no receipt. Proceed.

Only NEW and RETRY authorise processing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum

from app.db import Database
from kg_events.models import CanonicalEvent


class ClaimStatus(str, Enum):
    NEW = "new"
    RETRY = "retry"
    DUPLICATE = "duplicate"
    IN_FLIGHT = "in_flight"
    STALE = "stale"


class EventStatus(str, Enum):
    PENDING = "pending"
    COMMITTED = "committed"
    FAILED = "failed"


@dataclass
class Claim:
    status: ClaimStatus
    attempts: int = 0
    reason: str | None = None

    @property
    def should_process(self) -> bool:
        return self.status in (ClaimStatus.NEW, ClaimStatus.RETRY)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


class EventReceiptStore:
    """Dedup and crash recovery for canonical events."""

    def __init__(self, db: Database, *, stale_after_seconds: int = 300) -> None:
        self._db = db
        self._stale_after = timedelta(seconds=stale_after_seconds)

    async def claim(self, event: CanonicalEvent, *, stage: str = "consume") -> Claim:
        now = datetime.now(UTC)

        if event.sequence > 0:
            watermark = await self._db.fetch_one(
                "SELECT last_sequence FROM entity_watermarks WHERE entity_key = ?",
                (event.entity_key,),
            )
            if watermark and int(watermark["last_sequence"]) >= event.sequence:
                return Claim(
                    ClaimStatus.STALE,
                    reason=(
                        f"sequence {event.sequence} <= watermark "
                        f"{watermark['last_sequence']} for {event.entity_key}"
                    ),
                )

        existing = await self._db.fetch_one(
            "SELECT status, attempts, claimed_at FROM event_receipts WHERE idempotency_key = ?",
            (event.idempotency_key,),
        )

        if existing is not None:
            status = EventStatus(existing["status"])
            attempts = int(existing["attempts"] or 0)

            if status is EventStatus.COMMITTED:
                return Claim(ClaimStatus.DUPLICATE, attempts, "already committed")

            claimed_at = datetime.fromisoformat(existing["claimed_at"])
            if status is EventStatus.PENDING and now - claimed_at < self._stale_after:
                return Claim(ClaimStatus.IN_FLIGHT, attempts, "another worker holds the claim")

            await self._db.execute(
                "UPDATE event_receipts SET status = ?, attempts = ?, claimed_at = ?, stage = ? "
                "WHERE idempotency_key = ?",
                (
                    EventStatus.PENDING.value,
                    attempts + 1,
                    _iso(now),
                    stage,
                    event.idempotency_key,
                ),
            )
            return Claim(ClaimStatus.RETRY, attempts + 1, f"reclaimed after {status.value}")

        await self._db.execute(
            "INSERT INTO event_receipts(idempotency_key, event_id, group_id, "
            "entity_key, partition_key, sequence, trace_id, status, attempts, stage, claimed_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.idempotency_key,
                event.event_id,
                event.group_id,
                event.entity_key,
                event.partition_key,
                event.sequence,
                event.lineage.trace_id,
                EventStatus.PENDING.value,
                1,
                stage,
                _iso(now),
            ),
        )
        return Claim(ClaimStatus.NEW, 1)

    async def commit(self, event: CanonicalEvent) -> None:
        """Mark applied and advance the entity watermark.

        The watermark moves only on commit. Advancing it at claim time would make
        a crashed event look superseded, and the retry would then be dropped as
        stale — losing the change with no error anywhere.
        """
        now = _iso(datetime.now(UTC))
        await self._db.execute(
            "UPDATE event_receipts SET status = ?, committed_at = ?, error = NULL "
            "WHERE idempotency_key = ?",
            (EventStatus.COMMITTED.value, now, event.idempotency_key),
        )
        if event.sequence > 0:
            await self._db.execute(
                "INSERT INTO entity_watermarks(entity_key, last_sequence, updated_at) "
                "VALUES(?, ?, ?) ON CONFLICT(entity_key) DO UPDATE SET "
                "last_sequence = MAX(last_sequence, excluded.last_sequence), "
                "updated_at = excluded.updated_at",
                (event.entity_key, event.sequence, now),
            )

    async def fail(self, event: CanonicalEvent, error: str) -> None:
        await self._db.execute(
            "UPDATE event_receipts SET status = ?, error = ? WHERE idempotency_key = ?",
            (EventStatus.FAILED.value, error[:500], event.idempotency_key),
        )

    async def counts(self) -> dict[str, int]:
        rows = await self._db.fetch_all(
            "SELECT status, COUNT(*) AS n FROM event_receipts GROUP BY status"
        )
        return {row["status"]: int(row["n"]) for row in rows}

    async def stuck(self) -> list[dict]:
        """PENDING rows past the stale threshold — the reconciliation signal."""
        cutoff = _iso(datetime.now(UTC) - self._stale_after)
        return await self._db.fetch_all(
            "SELECT * FROM event_receipts WHERE status = ? AND claimed_at < ? "
            "ORDER BY claimed_at",
            (EventStatus.PENDING.value, cutoff),
        )

    async def by_trace(self, trace_id: str) -> list[dict]:
        """End-to-end lineage lookup for one source event."""
        return await self._db.fetch_all(
            "SELECT * FROM event_receipts WHERE trace_id = ? ORDER BY claimed_at", (trace_id,)
        )
