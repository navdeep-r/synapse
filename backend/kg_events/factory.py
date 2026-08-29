"""Constructing canonical events correctly.

Connectors should never assemble a `CanonicalEvent` field by field. The derived
fields — payload hash, idempotency key, partition key — have to be computed one
specific way or the guarantees built on them quietly stop holding: a hand-rolled
idempotency key that happens to include a timestamp disables dedup entirely, and
nothing fails, it just writes everything twice.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from kg_events.idempotency import derive_key, partition_key_for, payload_hash
from kg_events.models import (
    AccessTag,
    CanonicalEvent,
    CdcMode,
    Lineage,
    Operation,
    Payload,
    SourceDescriptor,
    SourceSystem,
)


def build_event(
    *,
    entity_key: str,
    payload: Payload,
    connector_id: str,
    record_id: str,
    source_system: SourceSystem = SourceSystem.UPLOAD,
    cdc_mode: CdcMode = CdcMode.POLLING,
    operation: Operation = Operation.UPSERT,
    occurred_at: datetime | None = None,
    group_id: str | None = None,
    access_tag: AccessTag = AccessTag.INTERNAL,
    pii_fields: list[str] | None = None,
    sequence: int = 0,
    record_version: str | None = None,
    trace_id: str | None = None,
    parent_event_id: str | None = None,
    stage_path: list[str] | None = None,
) -> CanonicalEvent:
    """Assemble a canonical event with all derived fields computed consistently."""
    occurred = occurred_at or datetime.now(UTC)
    digest = payload_hash(payload)

    return CanonicalEvent(
        event_id=str(uuid.uuid4()),
        idempotency_key=derive_key(
            source_system=source_system.value,
            connector_id=connector_id,
            record_id=record_id,
            operation=operation.value,
            payload_hash_hex=digest,
            occurred_at=occurred,
        ),
        partition_key=partition_key_for(entity_key=entity_key),
        entity_key=entity_key,
        sequence=sequence,
        group_id=group_id or "default",
        access_tag=access_tag,
        pii_fields=list(pii_fields or []),
        source=SourceDescriptor(
            system=source_system,
            connector_id=connector_id,
            record_id=record_id,
            record_version=record_version,
            cdc_mode=cdc_mode,
            captured_at=datetime.now(UTC),
        ),
        lineage=Lineage(
            trace_id=trace_id or str(uuid.uuid4()),
            parent_event_id=parent_event_id,
            stage_path=list(stage_path or ["connector"]),
        ),
        operation=operation,
        payload=payload,
        payload_hash=digest,
        occurred_at=occurred,
        emitted_at=datetime.now(UTC),
    )
