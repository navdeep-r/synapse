"""The only sanctioned way onto the ingestion topic.

Connectors do not touch the bus directly. Publishing through here means the
schema gate, the partition key and the lineage stamp cannot be skipped — a
connector that hand-rolled its own producer would be the exact failure the
registry exists to prevent.
"""

from __future__ import annotations

import logging
from collections import Counter

from kg_events.bus import EventBus, Message
from kg_events.codec import CANONICAL_EVENT_TOPIC, EventCodec, SerializationError
from kg_events.models import CanonicalEvent

logger = logging.getLogger("synapse.events.producer")


class EventProducer:
    def __init__(
        self,
        bus: EventBus,
        codec: EventCodec,
        *,
        topic: str = CANONICAL_EVENT_TOPIC,
        counters: Counter | None = None,
    ) -> None:
        self._bus = bus
        self._codec = codec
        self._topic = topic
        self.counters: Counter = counters if counters is not None else Counter()

    @property
    def topic(self) -> str:
        return self._topic

    async def publish(self, event: CanonicalEvent) -> Message:
        """Encode against the registered schema and publish on the entity-keyed partition.

        A `SerializationError` here is a producer-side bug — the connector built an
        event its own contract rejects — so it is raised rather than dead-lettered.
        Dead-lettering it would hide a broken connector behind a growing DLQ.
        """
        pass

        try:
            frame = self._codec.encode(event)
        except SerializationError:
            self.counters["produce_rejected"] += 1
            raise

        message = await self._bus.publish(self._topic, event.partition_key, frame)
        self.counters["produced"] += 1
        logger.debug(
            "published %s key=%s partition=%s offset=%s trace=%s",
            event.event_id,
            event.partition_key,
            message.partition,
            message.offset,
            event.lineage.trace_id,
        )
        return message

    async def publish_all(self, events: list[CanonicalEvent]) -> list[Message]:
        return [await self.publish(event) for event in events]
