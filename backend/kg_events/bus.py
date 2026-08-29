"""The event transport, and the ordering guarantee it has to provide.

`InProcessBus` is a partitioned append-only log with consumer-group offsets. It is
not Kafka and does not pretend to be — no durability across restarts, no network,
no rebalancing. What it *does* reproduce faithfully is the one property the rest of
the pipeline's correctness is built on:

    messages sharing a key are delivered to one consumer in publish order.

That is deliberate. If the in-process bus delivered out of order, or fanned one
key across partitions, then every test of entity resolution and temporal writes
would be passing under guarantees production does not have — or worse, failing
under guarantees production does have. Swapping this for Kafka later should change
throughput and durability, not observable ordering.

WHAT A KAFKA IMPLEMENTATION MUST PRESERVE
-----------------------------------------
The `EventBus` protocol is the seam. An implementation must keep:
  - same key -> same partition, for the lifetime of the topic
  - per-partition FIFO delivery
  - offsets committed *after* the handler succeeds (at-least-once, never
    at-most-once — the dedup layer exists precisely to absorb the redeliveries
    this produces)
Partition *count* is the one thing that may differ, and changing it on a live
topic reshuffles key-to-partition assignment, which breaks ordering across the
change. Treat partition count as immutable once data exists.
"""

from __future__ import annotations

import asyncio
import zlib
from collections import defaultdict
from dataclasses import dataclass
from typing import Protocol

DEFAULT_PARTITIONS = 6


@dataclass(frozen=True)
class Message:
    topic: str
    partition: int
    offset: int
    key: str
    value: bytes


class EventBus(Protocol):
    async def publish(self, topic: str, key: str, value: bytes) -> Message: ...
    async def poll(self, topic: str, group: str, max_messages: int = 10) -> list[Message]: ...
    async def commit(self, topic: str, group: str, message: Message) -> None: ...
    def partition_for(self, topic: str, key: str) -> int: ...


class InProcessBus:
    """Partitioned in-memory log. Development and tests only."""

    def __init__(self, *, partitions: int = DEFAULT_PARTITIONS) -> None:
        if partitions < 1:
            raise ValueError("partitions must be >= 1")
        self._partitions = partitions
        self._log: dict[str, list[list[Message]]] = {}
        self._offsets: dict[tuple[str, str, int], int] = defaultdict(int)
        self._lock = asyncio.Lock()

    @property
    def partitions(self) -> int:
        return self._partitions

    def partition_for(self, topic: str, key: str) -> int:
        """Stable key hashing.

        crc32 rather than the builtin `hash()`: Python randomises string hashing
        per process, so `hash()` would send the same entity to a different
        partition after a restart and quietly break ordering across it.
        """
        return zlib.crc32(key.encode("utf-8")) % self._partitions

    def _topic_log(self, topic: str) -> list[list[Message]]:
        if topic not in self._log:
            self._log[topic] = [[] for _ in range(self._partitions)]
        return self._log[topic]

    async def publish(self, topic: str, key: str, value: bytes) -> Message:
        async with self._lock:
            partitions = self._topic_log(topic)
            index = self.partition_for(topic, key)
            partition = partitions[index]
            message = Message(topic, index, len(partition), key, value)
            partition.append(message)
            return message

    async def poll(self, topic: str, group: str, max_messages: int = 10) -> list[Message]:
        """Next uncommitted messages, in per-partition order.

        Partitions are visited round-robin so one busy entity cannot starve the
        others, but within a partition the order is strictly the publish order.
        """
        async with self._lock:
            partitions = self._topic_log(topic)
            batch: list[Message] = []
            cursors = [self._offsets[(topic, group, i)] for i in range(self._partitions)]

            progressed = True
            while progressed and len(batch) < max_messages:
                progressed = False
                for index in range(self._partitions):
                    if len(batch) >= max_messages:
                        break
                    partition = partitions[index]
                    if cursors[index] < len(partition):
                        batch.append(partition[cursors[index]])
                        cursors[index] += 1
                        progressed = True
            return batch

    async def commit(self, topic: str, group: str, message: Message) -> None:
        async with self._lock:
            key = (topic, group, message.partition)
            # max(): a redelivered message must never rewind a committed offset.
            self._offsets[key] = max(self._offsets[key], message.offset + 1)

    async def lag(self, topic: str, group: str) -> int:
        """Total uncommitted messages.

        This is the autoscaling signal. CPU is the wrong trigger for this pipeline:
        an OCR burst arrives as a queue-depth spike long before it shows up as
        sustained CPU, and by the time CPU reacts the backlog is already deep.
        """
        async with self._lock:
            partitions = self._topic_log(topic)
            return sum(
                len(partitions[i]) - self._offsets[(topic, group, i)]
                for i in range(self._partitions)
            )

    async def depth(self, topic: str) -> int:
        async with self._lock:
            return sum(len(p) for p in self._topic_log(topic))
