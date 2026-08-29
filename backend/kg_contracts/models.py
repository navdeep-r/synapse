"""Pydantic v2 models exchanged between Synapse pipeline stages."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


class SourceKind(str, Enum):
    UPLOAD = "upload"
    SLACK = "slack"
    GITHUB = "github"
    NOTION = "notion"
    SQL = "sql"
    CRM = "crm"
    HR = "hr"
    EMAIL = "email"


class EpisodeStatus(str, Enum):
    """Lifecycle of an episode receipt.

    R3: `PENDING` is written *before* Graphiti is called and flipped to
    `COMMITTED` after it returns. A crash in between leaves a detectable
    `PENDING` row instead of a silent gap between the graph and SQLite.
    """

    PENDING = "pending"
    COMMITTED = "committed"
    FAILED = "failed"


class PrefilterOutcome(str, Enum):
    KEPT = "kept"
    DUPLICATE = "duplicate"
    BOILERPLATE = "boilerplate"
    TOO_SHORT = "too_short"


class Provenance(BaseModel):
    """The anchor carried unchanged from chunk to episode to graph fact."""

    chunk_id: str
    document_id: str
    source: str
    source_document_name: str
    source_type: str
    offset: int = 0
    timestamp: datetime = Field(default_factory=utcnow)
    access_tag: str = "internal"


class CanonicalDocument(BaseModel):
    document_id: str
    source_id: str
    source_kind: SourceKind = SourceKind.UPLOAD
    name: str
    media_type: str
    text: str
    content_hash: str
    ingested_at: datetime = Field(default_factory=utcnow)
    byte_size: int = 0
    access_tag: str = "internal"
    tenant: str = "tenant-a"
    metadata: dict = Field(default_factory=dict)


class Chunk(BaseModel):
    chunk_id: str
    document_id: str
    ordinal: int
    text: str
    offset: int
    char_length: int
    content_hash: str
    provenance: Provenance


class PrefilterDecision(BaseModel):
    """R7: every drop is auditable — the text and the rule that fired are kept."""

    chunk_id: str
    outcome: PrefilterOutcome
    rule: str | None = None
    dropped_text: str | None = None


class EpisodePayload(BaseModel):
    """One `add_episode` call. May batch several small chunks (v2 doc 3.4)."""

    episode_id: str
    name: str
    body: str
    source_description: str
    reference_time: datetime
    document_id: str
    source_id: str
    source_type: str
    group_id: str = "synapse"
    chunk_ids: list[str] = Field(default_factory=list)
    is_json: bool = False


class EpisodeReceipt(BaseModel):
    episode_id: str
    document_id: str
    status: EpisodeStatus
    chunk_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    committed_at: datetime | None = None
    graph_uuid: str | None = None
    nodes_created: int = 0
    edges_created: int = 0
    llm_calls: int = 0
    latency_ms: int = 0
    error: str | None = None
