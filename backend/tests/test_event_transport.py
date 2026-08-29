"""Producer/consumer behaviour: ordering, idempotency, and failure routing.

Every test here corresponds to a way an at-least-once pipeline silently corrupts a
knowledge graph: applying the same change twice, applying two changes to one
entity out of order, or wedging a partition behind one bad message.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio

from app.db import Database
from kg_events import (
    CanonicalEvent,
    ClaimStatus,
    DeadLetterQueue,
    DocumentPayload,
    EventCodec,
    EventConsumer,
    EventProducer,
    EventReceiptStore,
    InProcessBus,
    LocalSchemaRegistry,
    RecordPayload,
    SourceSystem,
    build_event,
)
from kg_events.codec import CANONICAL_EVENT_TOPIC

TOPIC = CANONICAL_EVENT_TOPIC


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "events.db")
    await database.connect()
    yield database
    await database.close()


@pytest.fixture
def codec(tmp_path: Path) -> EventCodec:
    return EventCodec(LocalSchemaRegistry(tmp_path / "registry.json"))


@pytest.fixture
def bus() -> InProcessBus:
    return InProcessBus(partitions=4)


@pytest.fixture
def producer(bus: InProcessBus, codec: EventCodec) -> EventProducer:
    return EventProducer(bus, codec)


@pytest.fixture
def receipts(db: Database) -> EventReceiptStore:
    return EventReceiptStore(db)


@pytest.fixture
def dlq(db: Database) -> DeadLetterQueue:
    return DeadLetterQueue(db, topic=TOPIC)


@pytest.fixture
def consumer(
    bus: InProcessBus, codec: EventCodec, receipts: EventReceiptStore, dlq: DeadLetterQueue
) -> EventConsumer:
    return EventConsumer(bus, codec, receipts, dlq, group="synapse-core")


def event(entity: str = "emp-1", *, text: str = "Alice reports to Robert.", **kw) -> CanonicalEvent:
    defaults = dict(
        entity_key=entity,
        payload=DocumentPayload(name="doc.txt", text=text),
        connector_id="crm-1",
        record_id=entity,
        source_system=SourceSystem.CRM,
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    return build_event(**{**defaults, **kw})


class Collector:
    def __init__(self) -> None:
        self.seen: list[CanonicalEvent] = []

    async def __call__(self, incoming: CanonicalEvent) -> None:
        self.seen.append(incoming)


# --- ordering -------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_entity_lands_on_one_partition(producer: EventProducer, bus: InProcessBus) -> None:
    """Deltas from different source systems must still be ordered against each other."""
    from_crm = await producer.publish(event("emp-7", source_system=SourceSystem.CRM))
    from_hrms = await producer.publish(
        event("emp-7", source_system=SourceSystem.HRMS, connector_id="hrms-1", text="Alice moved.")
    )
    assert from_crm.partition == from_hrms.partition


@pytest.mark.asyncio
async def test_events_for_one_entity_arrive_in_publish_order(
    producer: EventProducer, consumer: EventConsumer
) -> None:
    """Out-of-order application is how the superseded fact ends up the open one."""
    for n in range(5):
        await producer.publish(
            event("emp-9", text=f"update {n}", occurred_at=datetime(2026, 1, n + 1, tzinfo=UTC))
        )

    collector = Collector()
    await consumer.drain(collector)

    texts = [e.payload.text for e in collector.seen]
    assert texts == [f"update {n}" for n in range(5)]


@pytest.mark.asyncio
async def test_different_entities_spread_across_partitions(
    producer: EventProducer, bus: InProcessBus
) -> None:
    """Ordering is per entity; throughput comes from parallelism across entities."""
    partitions = {
        (await producer.publish(event(f"emp-{n}", record_id=f"emp-{n}"))).partition
        for n in range(30)
    }
    assert len(partitions) > 1


# --- idempotency ----------------------------------------------------------


@pytest.mark.asyncio
async def test_redelivery_is_applied_exactly_once(
    producer: EventProducer, consumer: EventConsumer, bus: InProcessBus, codec: EventCodec
) -> None:
    """At-least-once delivery must not become at-least-once *application*."""
    original = event("emp-2")
    frame = codec.encode(original)

    # The same logical change, delivered three times: a broker retry, then a
    # connector replaying its window with a fresh event_id.
    await bus.publish(TOPIC, original.partition_key, frame)
    await bus.publish(TOPIC, original.partition_key, frame)
    replay = event("emp-2")
    assert replay.idempotency_key == original.idempotency_key
    await producer.publish(replay)

    collector = Collector()
    await consumer.drain(collector)

    assert len(collector.seen) == 1, "the change was applied more than once"
    assert consumer.counters["skipped_duplicate"] == 2


@pytest.mark.asyncio
async def test_a_genuine_second_change_is_applied(
    producer: EventProducer, consumer: EventConsumer
) -> None:
    """Dedup must not be so eager that it swallows real updates."""
    await producer.publish(event("emp-3", text="Alice reports to Robert."))
    await producer.publish(event("emp-3", text="Alice reports to Carol."))

    collector = Collector()
    await consumer.drain(collector)
    assert len(collector.seen) == 2


@pytest.mark.asyncio
async def test_an_out_of_order_delta_is_dropped_by_the_watermark(
    producer: EventProducer, consumer: EventConsumer
) -> None:
    """Partition order holds within a run; the watermark survives a restart.

    A connector that restarts and replays from an older checkpoint would
    otherwise overwrite newer state with older state.
    """
    await producer.publish(event("emp-4", sequence=10, text="current"))
    await producer.publish(event("emp-4", sequence=3, text="stale replay"))

    collector = Collector()
    await consumer.drain(collector)

    assert [e.payload.text for e in collector.seen] == ["current"]
    assert consumer.counters["skipped_stale"] == 1


@pytest.mark.asyncio
async def test_watermark_only_advances_on_commit(
    receipts: EventReceiptStore, consumer: EventConsumer, producer: EventProducer
) -> None:
    """A crashed event must stay retriable, not look superseded by itself."""
    crashed = event("emp-5", sequence=7)
    claim = await receipts.claim(crashed)
    assert claim.status is ClaimStatus.NEW

    await receipts.fail(crashed, "graph timeout")

    # Same entity, newer delta: it must not be blocked by the failed one.
    later = event("emp-5", sequence=8, text="later")
    assert (await receipts.claim(later)).should_process


@pytest.mark.asyncio
async def test_a_failed_event_can_be_reclaimed_after_the_stale_window(
    db: Database, producer: EventProducer
) -> None:
    """Crash recovery: the worker died holding the claim, so someone must take it."""
    impatient = EventReceiptStore(db, stale_after_seconds=0)
    stuck = event("emp-6")

    assert (await impatient.claim(stuck)).status is ClaimStatus.NEW
    reclaimed = await impatient.claim(stuck)
    assert reclaimed.status is ClaimStatus.RETRY
    assert reclaimed.attempts == 2


@pytest.mark.asyncio
async def test_a_fresh_claim_is_not_stolen(db: Database) -> None:
    patient = EventReceiptStore(db, stale_after_seconds=3600)
    contended = event("emp-8")

    assert (await patient.claim(contended)).status is ClaimStatus.NEW
    assert (await patient.claim(contended)).status is ClaimStatus.IN_FLIGHT


# --- failure routing ------------------------------------------------------


@pytest.mark.asyncio
async def test_a_poison_message_is_dead_lettered_not_retried_forever(
    bus: InProcessBus, consumer: EventConsumer, dlq: DeadLetterQueue, producer: EventProducer
) -> None:
    """Entity-keyed partitioning makes a wedged partition especially damaging.

    Every entity hashing to that partition would stop updating, silently, while
    the rest of the pipeline looked healthy.
    """
    await bus.publish(TOPIC, "tenant-a:emp-1", b"\x00\x00\x00\x01not-avro")
    good = event("emp-1", text="this must still arrive")
    await producer.publish(good)

    collector = Collector()
    await consumer.drain(collector)

    assert [e.payload.text for e in collector.seen] == ["this must still arrive"]
    assert consumer.counters["poison"] == 1
    assert len(await dlq.pending()) == 1


@pytest.mark.asyncio
async def test_a_handler_failure_is_dead_lettered_with_its_lineage(
    producer: EventProducer, consumer: EventConsumer, dlq: DeadLetterQueue
) -> None:
    """A dead letter must be traceable back to the source event without decoding it."""
    failing = event("emp-10")
    await producer.publish(failing)

    async def explode(_: CanonicalEvent) -> None:
        raise RuntimeError("graph unavailable")

    await consumer.drain(explode)

    rows = await dlq.pending()
    assert len(rows) == 1
    assert rows[0]["trace_id"] == failing.lineage.trace_id
    assert rows[0]["error_type"] == "RuntimeError"
    assert consumer.counters["handler_failed"] == 1


@pytest.mark.asyncio
async def test_a_dead_letter_retains_the_frame_for_replay(
    producer: EventProducer, consumer: EventConsumer, dlq: DeadLetterQueue, codec: EventCodec
) -> None:
    """The source system should not have to re-emit for us to recover."""
    await producer.publish(event("emp-11", text="recoverable"))

    async def explode(_: CanonicalEvent) -> None:
        raise RuntimeError("transient")

    await consumer.drain(explode)

    rows = await dlq.pending()
    replayed = codec.decode(await dlq.frame(rows[0]["id"]))
    assert replayed.payload.text == "recoverable"


@pytest.mark.asyncio
async def test_dlq_counts_are_reported_per_stage(
    producer: EventProducer, consumer: EventConsumer, dlq: DeadLetterQueue
) -> None:
    """Per-stage counts are the alerting signal; a total would hide which stage broke."""
    await producer.publish(event("emp-12"))

    async def explode(_: CanonicalEvent) -> None:
        raise RuntimeError("nope")

    await consumer.drain(explode)
    assert await dlq.counts_by_stage() == {"consume": 1}


# --- tenancy --------------------------------------------------------------





@pytest.mark.asyncio
async def test_tenant_and_acl_reach_the_handler(
    producer: EventProducer, consumer: EventConsumer
) -> None:
    """These must arrive as explicit fields, not be re-derived from the payload."""
    await producer.publish(
        event("emp-14", group_id="tenant-a", pii_fields=["payload.text"])
    )
    collector = Collector()
    await consumer.drain(collector)

    delivered = collector.seen[0]
    assert delivered.group_id == "tenant-a"
    assert delivered.pii_fields == ["payload.text"]


# --- observability --------------------------------------------------------


@pytest.mark.asyncio
async def test_consumer_lag_is_observable_for_autoscaling(
    producer: EventProducer, consumer: EventConsumer, bus: InProcessBus
) -> None:
    """Lag, not CPU: an OCR burst shows up here long before it shows up as load."""
    for n in range(8):
        await producer.publish(event(f"emp-lag-{n}", record_id=f"emp-lag-{n}"))

    assert await bus.lag(TOPIC, "synapse-core") == 8
    await consumer.drain(Collector())
    assert await bus.lag(TOPIC, "synapse-core") == 0


@pytest.mark.asyncio
async def test_receipts_are_queryable_by_trace_id(
    producer: EventProducer, consumer: EventConsumer, receipts: EventReceiptStore
) -> None:
    """End-to-end lineage: one source event, findable at every stage it reached."""
    tracked = event("emp-15")
    await producer.publish(tracked)
    await consumer.drain(Collector())

    rows = await receipts.by_trace(tracked.lineage.trace_id)
    assert len(rows) == 1
    assert rows[0]["status"] == "committed"
    assert rows[0]["entity_key"] == "emp-15"


@pytest.mark.asyncio
async def test_structured_records_flow_through_the_same_path(
    producer: EventProducer, consumer: EventConsumer
) -> None:
    """Structured and unstructured sources converge on one canonical shape."""
    await producer.publish(
        event(
            "acct-1",
            payload=RecordPayload(
                entity_type="Organization",
                fields={"name": "Acme Corp", "tier": "enterprise"},
                text_repr="Acme Corp is an enterprise-tier account.",
            ),
        )
    )
    collector = Collector()
    await consumer.drain(collector)

    payload = collector.seen[0].payload
    assert isinstance(payload, RecordPayload)
    assert payload.fields["name"] == "Acme Corp"
