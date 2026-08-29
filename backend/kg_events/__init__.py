"""Enterprise ingestion pipeline: the canonical event contract and its transport.

Everything before the ontology lives here. Synapse's knowledge core consumes
`CanonicalEvent` and knows nothing about connectors, Kafka, or CDC.
"""

from kg_events.bus import DEFAULT_PARTITIONS, EventBus, InProcessBus, Message
from kg_events.codec import (
    CANONICAL_EVENT_SUBJECT,
    CANONICAL_EVENT_TOPIC,
    DeserializationError,
    EventCodec,
    SerializationError,
    load_canonical_schema,
)
from kg_events.consumer import EventConsumer
from kg_events.dedup import Claim, ClaimStatus, EventReceiptStore, EventStatus
from kg_events.dlq import DeadLetterQueue
from kg_events.factory import build_event
from kg_events.idempotency import derive_key, partition_key_for, payload_hash
from kg_events.models import (
    AccessTag,
    CanonicalEvent,
    CdcMode,
    DocumentPayload,
    Lineage,
    Operation,
    RecordPayload,
    SourceDescriptor,
    SourceSystem,
)
from kg_events.producer import EventProducer
from kg_events.registry import (
    Compatibility,
    ConfluentSchemaRegistry,
    IncompatibleSchemaError,
    LocalSchemaRegistry,
    check_compatibility,
)

__all__ = [
    "CANONICAL_EVENT_SUBJECT",
    "CANONICAL_EVENT_TOPIC",
    "DEFAULT_PARTITIONS",
    "AccessTag",
    "CanonicalEvent",
    "CdcMode",
    "Claim",
    "ClaimStatus",
    "Compatibility",
    "ConfluentSchemaRegistry",
    "DeadLetterQueue",
    "DeserializationError",
    "DocumentPayload",
    "EventBus",
    "EventCodec",
    "EventConsumer",
    "EventProducer",
    "EventReceiptStore",
    "EventStatus",
    "InProcessBus",
    "IncompatibleSchemaError",
    "Lineage",
    "LocalSchemaRegistry",
    "Message",
    "Operation",
    "RecordPayload",
    "SerializationError",
    "SourceDescriptor",
    "SourceSystem",
    "build_event",
    "check_compatibility",
    "derive_key",
    "load_canonical_schema",
    "partition_key_for",
    "payload_hash",
]
