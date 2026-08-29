"""The idempotency contract.

THE RULE
--------
An idempotency key identifies a *logical change*, not a delivery. Two emissions
of the same logical change — a Kafka redelivery, a connector restart replaying
its last window, an operator re-running a backfill — must produce byte-identical
keys so the consumer collapses them. A genuine subsequent change to the same
source record must produce a different key.

That gives the key its inputs: everything that identifies *which record changed*
plus a hash of *what it changed to*. Deliberately excluded:

- `event_id` — unique per emission; including it would defeat the whole purpose.
- `emitted_at` / `captured_at` — wall-clock differs across redeliveries.
- `sequence` — a connector that cannot supply a stable version would otherwise
  produce a new key on every poll of an unchanged row.
- `lineage` — a DLQ replay must dedup against the original attempt.

`occurred_at` IS included. Two updates that set a field back to a previous value
are genuinely different facts in a bi-temporal graph ("Alice moved to Platform,
then back to Payments"), and hashing payload alone would collapse the second
into the first and lose the history.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

KEY_VERSION = "v1"


def _canonical_json(value: Any) -> str:
    """Stable JSON: sorted keys, no incidental whitespace, no float drift."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def payload_hash(payload: Any) -> str:
    """SHA-256 over the canonicalised payload.

    Accepts a Pydantic model or a plain mapping. `kind` is stripped because it is
    a discriminator for the Python union, not part of the source data — including
    it would change the hash if the discriminator were ever renamed.
    """
    if hasattr(payload, "model_dump"):
        data = payload.model_dump(exclude={"kind"}, mode="json")
    else:
        data = {k: v for k, v in dict(payload).items() if k != "kind"}
    return hashlib.sha256(_canonical_json(data).encode("utf-8")).hexdigest()


def derive_key(
    *,
    source_system: str,
    connector_id: str,
    record_id: str,
    operation: str,
    payload_hash_hex: str,
    occurred_at: datetime,
) -> str:
    """Deterministic idempotency key for one logical change.

    `connector_id` is included so two connector instances pointed at the same
    source (a migration running old and new side by side) do not silently
    deduplicate against each other — that would look like success while one of
    them was actually being ignored.
    """
    material = "|".join(
        [
            KEY_VERSION,
            source_system,
            connector_id,
            record_id,
            operation,
            payload_hash_hex,
            occurred_at.astimezone().isoformat(),
        ]
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"{KEY_VERSION}:{digest}"


def partition_key_for(entity_key: str) -> str:
    """The Kafka message key.

    ENTITY-SCOPED, NEVER SOURCE-SCOPED. Kafka guarantees ordering only within a
    partition, so the key decides what is ordered relative to what.

    Keying by source system would put CRM and HRMS updates for the same person on
    different partitions. Those partitions are consumed concurrently by different
    workers, so two deltas about one entity race: entity resolution can decide the
    merge twice from two different starting states, and the bi-temporal write can
    apply the older fact last, leaving the *superseded* value as the open edge.
    Nothing errors — the graph just quietly states the wrong current fact, and the
    audit trail looks clean because both writes individually succeeded.

    Keying by entity puts every delta for one entity in one partition, consumed in
    strict order by one worker. The worker holds the lock implicitly by owning the
    partition.

    If entity_key isn't known yet, we fall back to a random key to spread the load.
    The sink worker will have to derive the entity key during execution.
    """
    if not entity_key:
        return str(uuid.uuid4())
    # MD5 is used here solely for uniformly distributing partition keys across
    # Kafka partitions, not for cryptographic security. It provides better
    # bit diffusion than hash() and avoids MurmurHash dependencies.
    digest = hashlib.md5(entity_key.encode("utf-8")).hexdigest()
    return f"{KEY_VERSION}:{digest}"
