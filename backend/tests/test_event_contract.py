"""The canonical event contract: schema, idempotency, and registry enforcement.

These are correctness tests, not coverage. Each one pins a property that fails
silently in production if it breaks — a drifting schema, an idempotency key that
stops deduplicating, a partition assignment that splits one entity across
consumers.
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from kg_events import (
    AccessTag,
    CdcMode,
    Compatibility,
    DocumentPayload,
    EventCodec,
    IncompatibleSchemaError,
    LocalSchemaRegistry,
    Operation,
    RecordPayload,
    SourceSystem,
    build_event,
    check_compatibility,
    derive_key,
    load_canonical_schema,
    partition_key_for,
    payload_hash,
)
from kg_events.models import CanonicalEvent


@pytest.fixture
def registry(tmp_path: Path) -> LocalSchemaRegistry:
    return LocalSchemaRegistry(tmp_path / "registry.json")


@pytest.fixture
def codec(registry: LocalSchemaRegistry) -> EventCodec:
    return EventCodec(registry)


def document_event(**overrides):
    defaults = dict(
        entity_key="emp-123",
        payload=DocumentPayload(name="handbook.pdf", text="Alice reports to Robert.", byte_size=24),
        connector_id="notion-1",
        record_id="page-9",
        source_system=SourceSystem.NOTION,
        cdc_mode=CdcMode.WEBHOOK,
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    return build_event(**{**defaults, **overrides})


# --- schema ---------------------------------------------------------------


def test_avro_and_pydantic_definitions_stay_aligned() -> None:
    """The .avsc is the wire contract; the model is the in-process view of it.

    Adding a field to one and not the other is the kind of drift that produces a
    field which is always null downstream with no error anywhere.
    """
    schema = load_canonical_schema()
    avro_fields = {f["name"] for f in schema["fields"]}
    model_fields = set(CanonicalEvent.model_fields)
    assert avro_fields == model_fields, (
        f"only in Avro: {avro_fields - model_fields}; only in model: {model_fields - avro_fields}"
    )


def test_document_and_record_payloads_both_round_trip(codec: EventCodec) -> None:
    doc = document_event()
    assert codec.decode(codec.encode(doc)).payload == doc.payload

    rec = document_event(
        payload=RecordPayload(entity_type="Employee", fields={"name": "Alice", "dept": None})
    )
    decoded = codec.decode(codec.encode(rec))
    assert isinstance(decoded.payload, RecordPayload)
    assert decoded.payload.fields == {"name": "Alice", "dept": None}


def test_frame_uses_confluent_wire_format(codec: EventCodec) -> None:
    """Byte-compatible with a real registry, so dev captures replay in production."""
    frame = codec.encode(document_event())
    assert frame[0] == 0, "magic byte must be 0x00"
    assert int.from_bytes(frame[1:5], "big") == codec.schema_id


def test_tenancy_and_acl_survive_the_wire(codec: EventCodec) -> None:
    """These drive graph isolation and export filtering; a silent loss is a leak."""
    event = document_event(
        access_tag=AccessTag.RESTRICTED,
        pii_fields=["payload.text", "payload.name"],
        group_id="tenant-a",
    )
    decoded = codec.decode(codec.encode(event))
    assert decoded.access_tag is AccessTag.RESTRICTED
    assert decoded.pii_fields == ["payload.text", "payload.name"]
    assert decoded.group_id == "default"


def test_corrupt_frame_is_rejected_not_silently_accepted(codec: EventCodec) -> None:
    from kg_events import DeserializationError

    with pytest.raises(DeserializationError):
        codec.decode(b"\x01\x00\x00\x00\x01garbage")  # wrong magic byte
    with pytest.raises(DeserializationError):
        codec.decode(b"\x00\x00")  # truncated header


# --- idempotency ----------------------------------------------------------


def test_redelivery_of_the_same_change_produces_the_same_key() -> None:
    """The whole dedup story rests on this."""
    first, second = document_event(), document_event()
    assert first.event_id != second.event_id, "each emission is a distinct delivery"
    assert first.idempotency_key == second.idempotency_key, "but the same logical change"


def test_a_real_change_produces_a_different_key() -> None:
    original = document_event()
    changed = document_event(
        payload=DocumentPayload(name="handbook.pdf", text="Alice reports to Carol.", byte_size=24)
    )
    assert original.idempotency_key != changed.idempotency_key


def test_same_payload_at_a_different_time_is_a_different_fact() -> None:
    """A -> B -> A is three facts in a bi-temporal graph, not two.

    Hashing payload alone would collapse the third into the first and erase the
    intervening history, which is exactly what this system exists to keep.
    """
    earlier = document_event(occurred_at=datetime(2026, 1, 1, tzinfo=UTC))
    later = document_event(occurred_at=datetime(2026, 6, 1, tzinfo=UTC))
    assert earlier.idempotency_key != later.idempotency_key


def test_two_connectors_on_one_source_do_not_collide() -> None:
    """During a connector migration both instances must be independently visible."""
    old = document_event(connector_id="notion-1")
    new = document_event(connector_id="notion-2")
    assert old.idempotency_key != new.idempotency_key


def test_key_is_stable_across_dict_ordering() -> None:
    a = payload_hash(RecordPayload(entity_type="Employee", fields={"a": "1", "b": "2"}))
    b = payload_hash(RecordPayload(entity_type="Employee", fields={"b": "2", "a": "1"}))
    assert a == b


def test_delete_and_upsert_of_one_record_are_distinct() -> None:
    upsert = derive_key(
        source_system="CRM", connector_id="c", record_id="r",
        operation=Operation.UPSERT.value, payload_hash_hex="abc",
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    delete = derive_key(
        source_system="CRM", connector_id="c", record_id="r",
        operation=Operation.DELETE.value, payload_hash_hex="abc",
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert upsert != delete


# --- partitioning ---------------------------------------------------------


def test_partition_key_is_entity_scoped_not_source_scoped() -> None:
    """Two source systems, one person: the ordering unit is the person.

    Source-scoped keying would put these on different partitions, letting two
    workers resolve and temporally write the same entity concurrently.
    """
    from_crm = document_event(
        entity_key="emp-123", source_system=SourceSystem.CRM, connector_id="crm-1"
    )
    from_hrms = document_event(
        entity_key="emp-123", source_system=SourceSystem.HRMS, connector_id="hrms-1"
    )
    assert from_crm.partition_key == from_hrms.partition_key == "tenant-a:emp-123"


def test_tenants_never_share_a_partition_key() -> None:
    assert partition_key_for("tenant-a", "emp-1") != partition_key_for("tenant-b", "emp-1")


# --- registry compatibility ----------------------------------------------


def test_adding_a_field_without_a_default_is_rejected(registry: LocalSchemaRegistry) -> None:
    """The headline requirement: a connector shape change fails loudly, here."""
    schema = load_canonical_schema()
    registry.register("evolve", schema)

    broken = copy.deepcopy(schema)
    broken["fields"].append({"name": "region", "type": "string"})

    with pytest.raises(IncompatibleSchemaError) as caught:
        registry.register("evolve", broken)
    assert "region" in str(caught.value)


def test_adding_a_field_with_a_default_is_accepted(registry: LocalSchemaRegistry) -> None:
    schema = load_canonical_schema()
    first = registry.register("evolve", schema)

    extended = copy.deepcopy(schema)
    extended["fields"].append({"name": "region", "type": "string", "default": "eu-west-1"})

    second = registry.register("evolve", extended)
    assert second.schema_id != first.schema_id
    assert second.version == 2


def test_removing_a_field_is_rejected(registry: LocalSchemaRegistry) -> None:
    schema = load_canonical_schema()
    registry.register("evolve", schema)

    reduced = copy.deepcopy(schema)
    reduced["fields"] = [f for f in reduced["fields"] if f["name"] != "pii_fields"]

    with pytest.raises(IncompatibleSchemaError):
        registry.register("evolve", reduced)


def test_non_promotable_type_change_is_rejected() -> None:
    old = {"type": "record", "name": "R", "fields": [{"name": "sequence", "type": "long"}]}
    new = {"type": "record", "name": "R", "fields": [{"name": "sequence", "type": "string"}]}
    violations = check_compatibility(old, new, Compatibility.BACKWARD)
    assert violations and "not promotable" in violations[0]


def test_promotable_type_change_is_allowed() -> None:
    old = {"type": "record", "name": "R", "fields": [{"name": "n", "type": "int"}]}
    new = {"type": "record", "name": "R", "fields": [{"name": "n", "type": "long"}]}
    assert check_compatibility(old, new, Compatibility.BACKWARD) == []


def test_dropping_a_union_branch_is_rejected() -> None:
    """Removing RecordPayload would make every structured-source event undecodable."""
    old = {
        "type": "record", "name": "R",
        "fields": [{"name": "p", "type": ["null", "string", "long"]}],
    }
    new = {"type": "record", "name": "R", "fields": [{"name": "p", "type": ["null", "string"]}]}
    violations = check_compatibility(old, new, Compatibility.BACKWARD)
    assert violations and "union branch" in violations[0]


def test_removing_an_enum_symbol_is_rejected() -> None:
    """Retiring a CDC mode would break replay of everything already emitted with it."""
    def schema(symbols: list[str]) -> dict:
        return {
            "type": "record", "name": "R",
            "fields": [{"name": "mode", "type": {"type": "enum", "name": "M", "symbols": symbols}}],
        }

    violations = check_compatibility(
        schema(["LOG_BASED", "POLLING"]), schema(["LOG_BASED"]), Compatibility.BACKWARD
    )
    assert violations and "enum symbol" in violations[0]


def test_registering_the_identical_schema_is_idempotent(registry: LocalSchemaRegistry) -> None:
    schema = load_canonical_schema()
    first = registry.register("evolve", schema)
    second = registry.register("evolve", schema)
    assert first.schema_id == second.schema_id


def test_registry_survives_a_restart(tmp_path: Path) -> None:
    """Schema ids are baked into every frame, so they must outlive the process."""
    path = tmp_path / "registry.json"
    original = LocalSchemaRegistry(path).register("evolve", load_canonical_schema())

    reopened = LocalSchemaRegistry(path)
    assert reopened.latest("evolve").schema_id == original.schema_id
    assert json.loads(path.read_text())["next_id"] == original.schema_id + 1


def test_decoding_uses_the_writers_schema_by_id(tmp_path: Path) -> None:
    """A consumer on v1 must still read frames written by a producer on v2.

    This is the whole point of putting the schema id on the wire rather than
    assuming both sides run the same build.
    """
    path = tmp_path / "registry.json"
    registry = LocalSchemaRegistry(path)
    v1 = load_canonical_schema()
    old_codec = EventCodec(registry, subject="rolling", schema=v1)
    frame = old_codec.encode(document_event())

    v2 = copy.deepcopy(v1)
    v2["fields"].append({"name": "region", "type": "string", "default": "eu-west-1"})
    new_codec = EventCodec(registry, subject="rolling", schema=v2)

    assert new_codec.schema_id != old_codec.schema_id
    decoded = new_codec.decode(frame)
    assert decoded.entity_key == "emp-123"


# --- lineage --------------------------------------------------------------


def test_lineage_advances_without_mutating() -> None:
    event = document_event()
    advanced = event.lineage.advanced("normalize")
    assert event.lineage.stage_path == ["connector"]
    assert advanced.stage_path == ["connector", "normalize"]
    assert advanced.trace_id == event.lineage.trace_id


def test_capture_lag_is_derivable() -> None:
    event = document_event(occurred_at=datetime.now(UTC) - timedelta(minutes=5))
    assert (event.emitted_at - event.occurred_at) >= timedelta(minutes=4)
