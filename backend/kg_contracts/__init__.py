"""Shared data contracts (types only) for the Synapse v2 pipeline.

No business logic, no I/O. These models are the single source of truth for
payloads exchanged between pipeline modules.
"""

from kg_contracts.models import (
    CanonicalDocument,
    Chunk,
    EpisodePayload,
    EpisodeReceipt,
    EpisodeStatus,
    PrefilterDecision,
    PrefilterOutcome,
    Provenance,
    SourceKind,
)

__all__ = [
    "CanonicalDocument",
    "Chunk",
    "EpisodePayload",
    "EpisodeReceipt",
    "EpisodeStatus",
    "PrefilterDecision",
    "PrefilterOutcome",
    "Provenance",
    "SourceKind",
]
