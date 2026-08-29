# Enterprise ingestion pipeline

Everything before the ontology. Connectors, canonical events, transport, dedup.
Synapse's knowledge core consumes `CanonicalEvent` and knows nothing about
connectors, CDC, or the broker.

```
connectors ─┬─ parse/OCR ──────┐
            └─ extract/validate┴─ map ─→ [schema registry gate] ─→ topic ─→ consumer ─→ Synapse core
                                                                     │
                                                                     └─→ DLQ (per stage)
```

## Decision: what is the source of truth

**The event log is the source of truth for what happened. The graph is
authoritative for what is currently known.**

The canonical topic is retained indefinitely and the graph is treated as a
derived projection that can be rebuilt from it. The reason is specific to this
system rather than general event-sourcing enthusiasm: entity resolution is the
highest-risk component here and it _will_ change. When the resolver improves,
being able to re-derive the whole graph from the original events is worth more
than the retention cost. Without it, every past resolution mistake is permanent.

**The honest caveat.** Replay re-derives; it does not reproduce. Extraction runs
through an LLM, so replaying the same events yields a _different_ graph — ideally
a better one. Anyone expecting byte-identical rebuild will be surprised. What
replay actually guarantees is that no source information is lost, not that the
derivation is reproducible.

Consequences to hold onto:

- Retention on the canonical topic is infinite, not the 7-day default. Losing the
  log means losing the ability to rebuild.
- The graph may be dropped and rebuilt; the log may not.
- Curation decisions (merge/distinct verdicts made by a human) are **not**
  derivable from the log and must be persisted separately and re-applied after a
  rebuild. This is the one place the projection model leaks, and forgetting it
  would silently discard human review on the first replay.

## Decision: partition by entity, never by source system

The Kafka message key is `<tenant_id>:<entity_key>`.

Ordering is only guaranteed within a partition, so the key decides what is
ordered relative to what. Keying by source system is the intuitive choice and it
is wrong: CRM and HRMS updates about the same person land on different
partitions, get consumed concurrently by different workers, and then two things
break at once. Entity resolution decides the merge twice from two different
starting states, and the bi-temporal write can apply the older fact last —
leaving the _superseded_ value as the open edge.

Nothing errors. Both writes individually succeed, the audit trail looks clean,
and the graph quietly states the wrong current fact.

Entity keying puts every delta for one entity in one partition, consumed in offset
order by one consumer, which serialises resolution and temporal writes per entity
while still parallelising across entities.

Two corollaries:

- **Partition count is immutable once data exists.** Changing it reshuffles
  key-to-partition assignment and breaks ordering across the change.
- **Partition order is not enough on its own.** A connector restart or consumer
  rebalance can replay an older offset after a newer one was applied, so
  `entity_watermarks` tracks the highest source `sequence` applied per entity and
  drops anything at or below it.

## Idempotency

`idempotency_key` identifies a _logical change_, not a delivery. Derivation is in
`idempotency.py`; the contract is that redeliveries hash identically and genuine
changes do not.

Deliberately excluded from the key: `event_id` (unique per delivery — including it
would defeat the purpose), `emitted_at`/`captured_at` (wall clock differs across
redeliveries), `sequence` (a connector without stable versions would produce a new
key every poll), and `lineage` (a DLQ replay must dedup against the original).

Deliberately **included**: `occurred_at`. In a bi-temporal graph, "Alice moved to
Platform, then back to Payments" is three facts, not two. Hashing payload alone
would collapse the third into the first and erase the history this system exists
to keep.

Consumption extends Synapse's existing intent-then-commit receipt pattern one
level out. `episode_receipts` answers "did this graph write land?"; `event_receipts`
answers "have we already applied this logical change?". The claim protocol returns
`NEW`, `RETRY`, `DUPLICATE`, `IN_FLIGHT` or `STALE`, and only the first two
authorise processing.

The watermark advances on **commit**, never on claim. Advancing at claim time
would make a crashed event look superseded by itself, and the retry would then be
dropped as stale — losing the change with no error anywhere.

## CDC strategy per source type

Log-based wherever the source has a durable log. Polling cannot observe a delete,
and cannot see two updates inside one interval — it reports the net result and
silently loses intermediate state, which matters here because Synapse records
history rather than current state.

| Source                           | Mode                                | Why                                                                                                    |
| -------------------------------- | ----------------------------------- | ------------------------------------------------------------------------------------------------------ |
| SQL, ERP (Postgres/MySQL/Oracle) | `LOG_BASED` (Debezium)              | WAL/binlog gives deletes and every intermediate update, with an LSN that maps directly onto `sequence` |
| CRM (Salesforce, HubSpot)        | `WEBHOOK` + nightly `FULL_SNAPSHOT` | No log exposed; webhooks are lossy under outage, so the snapshot closes the gap                        |
| HRMS (Workday, BambooHR)         | `POLLING` on `last_modified`        | Low change volume; deletes are rare and handled by the reconciliation snapshot                         |
| Slack                            | `WEBHOOK` (Events API)              | Push-native; edits and deletes arrive as their own events                                              |
| Notion, Docs                     | `POLLING` on `last_edited_time`     | API exposes no change log; cursor-based pagination bounds the scan                                     |
| CSV, PDF, uploads                | `MANUAL_UPLOAD`                     | No source of change at all — the file _is_ the event                                                   |

`FULL_SNAPSHOT` is a correctness backstop, not a mode of operation: it is the only
way to detect a delete that a lossy channel missed.

## Failure routing

Three kinds of failure, three responses. Conflating them is how pipelines either
lose data or wedge.

| Failure                    | Response                                  | Why                                                                                |
| -------------------------- | ----------------------------------------- | ---------------------------------------------------------------------------------- |
| Poison (undecodable frame) | DLQ, commit offset                        | The bytes will never change; retrying spins forever and blocks the partition       |
| Handler failure            | DLQ, mark receipt `FAILED`, commit offset | May be transient, so it stays reclaimable — but must not block the partition       |
| Duplicate / stale          | Commit offset, count it                   | Not a failure, but a rising duplicate rate means something upstream is misbehaving |

Offsets commit **after** the handler, making delivery at-least-once. That is
deliberate: at-most-once drops data on a crash, and the dedup layer exists
precisely to make the resulting redeliveries harmless.

A blocked partition is the worst failure mode in this design. Because partitioning
is entity-keyed, one poison message would halt updates for every entity hashing to
that partition — silently, while the rest of the pipeline looks healthy.

## Transport

`InProcessBus` is a partitioned in-memory log with consumer-group offsets. Not
durable, no network, dev and tests only. What it reproduces faithfully is the one
property everything else depends on: messages sharing a key are delivered to one
consumer in publish order.

That fidelity is the point. If the dev bus delivered out of order, every test of
resolution and temporal writes would be passing under guarantees production does
not have. `EventBus` is the seam; a Kafka implementation must preserve same-key
same-partition, per-partition FIFO, and post-handler offset commit. Throughput and
durability change — observable ordering does not.

## Not built yet

- Kafka transport (`EventBus` implementation)
- Connectors themselves, and the parse/OCR and structured-extraction worker pools
- Wiring the consumer into the existing `Pipeline`, which still ingests directly
- Merge-conflict handling in the Curation queue for contradictory resolutions from
  two sources
- Neo4j causal cluster with read replicas
- Per-stage latency SLAs and lag-based autoscaling triggers
