"""Avro serialisation in Confluent wire format.

Wire layout, identical locally and in production so bytes captured from a dev run
can be replayed against a real cluster:

    byte 0      magic 0x00
    bytes 1-4   schema id, big-endian uint32
    bytes 5..   Avro binary (schemaless — the schema lives in the registry)

The schema id is what makes evolution safe: a consumer decodes with the *writer's*
schema fetched by id, resolved against its own reader schema, rather than assuming
the producer was running the same version it is.

WHY AVRO AND NOT PROTOBUF
-------------------------
Both were viable. Avro wins here on three counts. Compatibility rules are a
first-class registry concept rather than a convention, which matters because
"fail loudly at the registry" is a hard requirement. It needs no codegen step, so
the contract stays installable with `uv pip install` and reviewable as one JSON
file. And Avro carries field defaults in the schema itself, which is precisely the
mechanism that makes adding a field safe.
"""

from __future__ import annotations

import io
import json
import struct
from pathlib import Path
from typing import Any

from fastavro import parse_schema, schemaless_reader, schemaless_writer

from kg_events.models import CanonicalEvent
from kg_events.registry import RegisteredSchema, SchemaRegistry

MAGIC_BYTE = 0
HEADER = struct.Struct(">bI")

SCHEMA_DIR = Path(__file__).parent / "schemas"
CANONICAL_EVENT_SCHEMA_FILE = SCHEMA_DIR / "canonical_event.v1.avsc"

#: Confluent subject naming. TopicNameStrategy: one schema per topic.
CANONICAL_EVENT_SUBJECT = "synapse.ingestion.canonical-events-value"
CANONICAL_EVENT_TOPIC = "synapse.ingestion.canonical-events"


def load_canonical_schema() -> dict[str, Any]:
    return json.loads(CANONICAL_EVENT_SCHEMA_FILE.read_text("utf-8"))


class SerializationError(RuntimeError):
    """Raised when an event cannot be encoded against the registered schema."""


class DeserializationError(RuntimeError):
    """Raised for a malformed frame or a payload that does not match its schema.

    Consumers treat this as a poison message and route to the DLQ — it can never
    succeed on retry, so redelivery would spin forever.
    """


class EventCodec:
    """Encode/decode canonical events against a registry."""

    def __init__(
        self,
        registry: SchemaRegistry,
        *,
        subject: str = CANONICAL_EVENT_SUBJECT,
        schema: dict[str, Any] | None = None,
    ) -> None:
        self._registry = registry
        self._subject = subject
        self._schema = schema or load_canonical_schema()
        self._registered: RegisteredSchema = registry.register(subject, self._schema)
        self._reader_schema = parse_schema(self._schema)
        self._writer_cache: dict[int, Any] = {self._registered.schema_id: self._reader_schema}

    @property
    def schema_id(self) -> int:
        return self._registered.schema_id

    def encode(self, event: CanonicalEvent) -> bytes:
        buffer = io.BytesIO()
        buffer.write(HEADER.pack(MAGIC_BYTE, self._registered.schema_id))
        try:
            schemaless_writer(buffer, self._reader_schema, event.to_avro())
        except Exception as exc:
            raise SerializationError(f"event {event.event_id} does not match schema: {exc}") from exc
        return buffer.getvalue()

    def decode(self, data: bytes) -> CanonicalEvent:
        if len(data) < HEADER.size:
            raise DeserializationError("frame shorter than the Confluent header")
        magic, schema_id = HEADER.unpack_from(data, 0)
        if magic != MAGIC_BYTE:
            raise DeserializationError(f"bad magic byte {magic!r}; not a Confluent frame")

        writer_schema = self._writer_cache.get(schema_id)
        if writer_schema is None:
            try:
                writer_schema = parse_schema(self._registry.by_id(schema_id).schema)
            except KeyError as exc:
                raise DeserializationError(f"schema id {schema_id} is not in the registry") from exc
            self._writer_cache[schema_id] = writer_schema

        try:
            record = schemaless_reader(
                io.BytesIO(data[HEADER.size :]), writer_schema, self._reader_schema
            )
        except Exception as exc:
            raise DeserializationError(f"could not decode event body: {exc}") from exc
        return CanonicalEvent.from_avro(record)
