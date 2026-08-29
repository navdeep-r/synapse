"""Python mirror of the canonical event Avro contract.

The `.avsc` file is the wire contract and the registry's source of truth; these
models are the ergonomic in-process view of it. `tests/test_event_contract.py`
asserts the two stay aligned, so adding a field to one without the other fails
the suite rather than drifting silently.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

NAMESPACE = "com.synapse.ingestion.v1"
SCHEMA_VERSION = 1


def utcnow() -> datetime:
    return datetime.now(UTC)


class AccessTag(str, Enum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"


class SourceSystem(str, Enum):
    CRM = "CRM"
    ERP = "ERP"
    HRMS = "HRMS"
    SQL = "SQL"
    CSV = "CSV"
    SLACK = "SLACK"
    NOTION = "NOTION"
    DOCS = "DOCS"
    PDF = "PDF"
    GITHUB = "GITHUB"
    UPLOAD = "UPLOAD"


class CdcMode(str, Enum):
    """How a change was detected at the source.

    Preference order is LOG_BASED > WEBHOOK > POLLING > FULL_SNAPSHOT. Polling
    cannot observe a delete, and cannot see two updates that happen inside one
    interval — it reports the net result and silently loses the intermediate
    state, which matters here because Synapse records history rather than
    current state.
    """

    LOG_BASED = "LOG_BASED"
    WEBHOOK = "WEBHOOK"
    POLLING = "POLLING"
    FULL_SNAPSHOT = "FULL_SNAPSHOT"
    MANUAL_UPLOAD = "MANUAL_UPLOAD"


class Operation(str, Enum):
    UPSERT = "UPSERT"
    DELETE = "DELETE"


class SourceDescriptor(BaseModel):
    system: SourceSystem = SourceSystem.UPLOAD
    connector_id: str
    record_id: str
    record_version: str | None = None
    cdc_mode: CdcMode = CdcMode.POLLING
    captured_at: datetime = Field(default_factory=utcnow)


class Lineage(BaseModel):
    trace_id: str
    parent_event_id: str | None = None
    stage_path: list[str] = Field(default_factory=list)

    def advanced(self, stage: str) -> Lineage:
        """Return a copy with `stage` appended — stages never mutate lineage in place."""
        return Lineage(
            trace_id=self.trace_id,
            parent_event_id=self.parent_event_id,
            stage_path=[*self.stage_path, stage],
        )


class DocumentPayload(BaseModel):
    kind: Literal["document"] = "document"
    name: str
    media_type: str = "text/plain"
    text: str
    byte_size: int = 0
    uri: str | None = None
    ocr_confidence: float | None = None


class RecordPayload(BaseModel):
    kind: Literal["record"] = "record"
    entity_type: str
    fields: dict[str, str | None] = Field(default_factory=dict)
    text_repr: str | None = None


Payload = DocumentPayload | RecordPayload


class CanonicalEvent(BaseModel):
    """The only shape permitted on the ingestion topic."""

    schema_version: int = SCHEMA_VERSION
    event_id: str
    idempotency_key: str
    partition_key: str
    entity_key: str
    sequence: int = 0

    group_id: str
    access_tag: AccessTag = AccessTag.INTERNAL
    pii_fields: list[str] = Field(default_factory=list)

    source: SourceDescriptor
    lineage: Lineage

    operation: Operation = Operation.UPSERT
    payload: Payload
    payload_hash: str

    occurred_at: datetime = Field(default_factory=utcnow)
    emitted_at: datetime = Field(default_factory=utcnow)

    # --- Avro interop ----------------------------------------------------

    def to_avro(self) -> dict[str, Any]:
        """Shape this for fastavro.

        The payload union is written in fastavro's explicit `(branch_name, value)`
        form. Structural inference would work today but becomes ambiguous the
        moment two branches share a field set, and that failure appears as a
        confusing serialisation error rather than as a schema problem.
        """
        payload = self.payload.model_dump(exclude={"kind"})
        branch = (
            f"{NAMESPACE}.DocumentPayload"
            if isinstance(self.payload, DocumentPayload)
            else f"{NAMESPACE}.RecordPayload"
        )
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "idempotency_key": self.idempotency_key,
            "partition_key": self.partition_key,
            "entity_key": self.entity_key,
            "sequence": self.sequence,
            "group_id": self.group_id,
            "access_tag": self.access_tag.value,
            "pii_fields": list(self.pii_fields),
            "source": {
                "system": self.source.system.value,
                "connector_id": self.source.connector_id,
                "record_id": self.source.record_id,
                "record_version": self.source.record_version,
                "cdc_mode": self.source.cdc_mode.value,
                "captured_at": self.source.captured_at,
            },
            "lineage": {
                "trace_id": self.lineage.trace_id,
                "parent_event_id": self.lineage.parent_event_id,
                "stage_path": list(self.lineage.stage_path),
            },
            "operation": self.operation.value,
            "payload": (branch, payload),
            "payload_hash": self.payload_hash,
            "occurred_at": self.occurred_at,
            "emitted_at": self.emitted_at,
        }

    @classmethod
    def from_avro(cls, record: dict[str, Any]) -> CanonicalEvent:
        raw = dict(record["payload"])
        payload: Payload = (
            RecordPayload(**raw) if "entity_type" in raw else DocumentPayload(**raw)
        )
        return cls(
            schema_version=record["schema_version"],
            event_id=record["event_id"],
            idempotency_key=record["idempotency_key"],
            partition_key=record["partition_key"],
            entity_key=record["entity_key"],
            sequence=record.get("sequence", 0),
            group_id=record["group_id"],
            access_tag=AccessTag(record["access_tag"]),
            pii_fields=list(record["pii_fields"]),
            source=SourceDescriptor(
                system=SourceSystem(record["source"]["system"]),
                connector_id=record["source"]["connector_id"],
                record_id=record["source"]["record_id"],
                record_version=record["source"]["record_version"],
                cdc_mode=CdcMode(record["source"]["cdc_mode"]),
                captured_at=record["source"]["captured_at"],
            ),
            lineage=Lineage(
                trace_id=record["lineage"]["trace_id"],
                parent_event_id=record["lineage"]["parent_event_id"],
                stage_path=list(record["lineage"]["stage_path"]),
            ),
            operation=Operation(record["operation"]),
            payload=payload,
            payload_hash=record["payload_hash"],
            occurred_at=record["occurred_at"],
            emitted_at=record["emitted_at"],
        )
