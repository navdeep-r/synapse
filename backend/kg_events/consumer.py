"""Idempotent consumer with dead-lettering.

The failure taxonomy matters more than the loop. Three kinds of failure, three
different correct responses, and conflating them is how pipelines either lose data
or wedge:

- **Poison** (`DeserializationError`) — the frame cannot be decoded. Retrying is
  pointless because the bytes will never change. Dead-letter it and commit the
  offset, or it blocks the partition forever.
- **Handler failure** — the event was understood but processing failed. This may
  be transient (a graph timeout), so the offset is committed only after the DLQ
  row exists; the receipt is marked FAILED so it can be reclaimed and retried
  rather than counted as done.
- **Duplicate/stale** — not a failure at all. Commit the offset and move on, but
  count it, because a sudden rise in duplicates means something upstream is
  redelivering more than it should.

Offsets are committed *after* the handler, which makes delivery at-least-once.
That is the deliberate choice: at-most-once would drop data on a crash, and the
dedup layer exists precisely to make the resulting redeliveries harmless.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Awaitable, Callable

from kg_events.bus import EventBus
from kg_events.codec import CANONICAL_EVENT_TOPIC, DeserializationError, EventCodec
from kg_events.dedup import ClaimStatus, EventReceiptStore
from kg_events.dlq import DeadLetterQueue
from kg_events.models import CanonicalEvent

logger = logging.getLogger("synapse.events.consumer")

Handler = Callable[[CanonicalEvent], Awaitable[None]]


class EventConsumer:
    def __init__(
        self,
        bus: EventBus,
        codec: EventCodec,
        receipts: EventReceiptStore,
        dlq: DeadLetterQueue,
        *,
        group: str,
        stage: str = "consume",
        topic: str = CANONICAL_EVENT_TOPIC,
        counters: Counter | None = None,
    ) -> None:
        self._bus = bus
        self._codec = codec
        self._receipts = receipts
        self._dlq = dlq
        self._group = group
        self._stage = stage
        self._topic = topic
        self.counters: Counter = counters if counters is not None else Counter()

    async def poll_once(self, handler: Handler, *, max_messages: int = 10) -> int:
        """Consume one batch. Returns how many messages were handled successfully."""
        batch = await self._bus.poll(self._topic, self._group, max_messages)
        handled = 0

        for message in batch:
            try:
                event = self._codec.decode(message.value)
            except DeserializationError as exc:
                # Poison: never retriable, and blocks the partition if left.
                self.counters["poison"] += 1
                logger.warning(
                    "poison message on %s/%s offset %s: %s",
                    self._topic,
                    message.partition,
                    message.offset,
                    exc,
                )
                await self._dlq.put(stage=self._stage, payload=message.value, error=exc)
                await self._bus.commit(self._topic, self._group, message)
                continue

            claim = await self._receipts.claim(event, stage=self._stage)
            if not claim.should_process:
                self.counters[f"skipped_{claim.status.value}"] += 1
                if claim.status is ClaimStatus.IN_FLIGHT:
                    # Someone else owns it; leave the offset so it is retried.
                    continue
                await self._bus.commit(self._topic, self._group, message)
                continue

            try:
                await handler(event)
            except Exception as exc:
                self.counters["handler_failed"] += 1
                logger.exception("handler failed for event %s", event.event_id)
                await self._receipts.fail(event, str(exc))
                await self._dlq.put(
                    stage=self._stage,
                    payload=message.value,
                    error=exc,
                    event=event,
                    attempts=claim.attempts,
                )
                await self._bus.commit(self._topic, self._group, message)
                continue

            await self._receipts.commit(event)
            await self._bus.commit(self._topic, self._group, message)
            self.counters["consumed"] += 1
            handled += 1

        return handled

    async def drain(self, handler: Handler, *, max_batches: int = 100) -> int:
        """Consume until the topic is caught up. Used by tests and batch replays."""
        total = 0
        for _ in range(max_batches):
            batch = await self.poll_once(handler)
            if batch == 0 and not await self._pending():
                break
            total += batch
        return total

    async def _pending(self) -> bool:
        lag = getattr(self._bus, "lag", None)
        if lag is None:
            return False
        return await lag(self._topic, self._group) > 0
