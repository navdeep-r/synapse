"""In-process pipeline orchestrator.

Runs the seven stages for a document without Kafka or Temporal: an asyncio queue
plus a single worker task. Uploads return immediately with a job id and the
console watches progress through the stage counters.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.db import Database
from kg_chunking import chunk_document
from kg_contracts import CanonicalDocument, PrefilterOutcome, SourceKind
from kg_graphiti.service import GraphService
from kg_ingest import ActivityRepository, DocumentRepository, SourceRepository
from kg_observability.receipts import ReceiptStore
from kg_observability.stages import StageTracker
from kg_prefilter import build_episodes, prefilter_chunks, prune_short_episodes

logger = logging.getLogger("synapse.pipeline")


@dataclass
class PipelineResult:
    document_id: str
    chunks: int = 0
    kept_chunks: int = 0
    dropped_chunks: int = 0
    episodes: int = 0
    nodes_created: int = 0
    edges_created: int = 0
    invalidated_edges: int = 0
    llm_calls: int = 0
    errors: list[str] = field(default_factory=list)


class Pipeline:
    """Owns the ingest queue and the stage tracker."""

    def __init__(
        self,
        db: Database,
        graph: GraphService,
        tracker: StageTracker,
        *,
        prefilter_min_chars: int = 40,
        batch_target_chars: int = 1200,
    ) -> None:
        self.db = db
        self.graph = graph
        self.tracker = tracker
        self.prefilter_min_chars = prefilter_min_chars
        self.batch_target_chars = batch_target_chars

        self.documents = DocumentRepository(db)
        self.sources = SourceRepository(db)
        self.activity = ActivityRepository(db)
        self.receipts = ReceiptStore(db)

        self._queue: asyncio.Queue[CanonicalDocument] = asyncio.Queue()
        self._worker: asyncio.Task | None = None
        self._last_result: PipelineResult | None = None

    # --- Worker lifecycle --------------------------------------------------

    def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run_forever())

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None

    async def submit(self, document: CanonicalDocument) -> None:
        await self.documents.save(document)
        now = datetime.now(UTC).isoformat()
        await self.db.execute(
            """INSERT INTO pipeline_jobs(job_id, status, created_at, updated_at)
               VALUES(?, 'pending', ?, ?)
               ON CONFLICT(job_id) DO UPDATE SET status = 'pending', updated_at = ?""",
            (document.document_id, now, now, now)
        )
        self.start()

    async def drain(self) -> None:
        """Wait for the queue to empty. Used by tests and the seed script."""
        while True:
            n = await self.db.fetch_value("SELECT COUNT(*) AS c FROM pipeline_jobs WHERE status IN ('pending', 'processing')", (), 0)
            if not n or int(n) == 0:
                break
            await asyncio.sleep(0.5)

    @property
    def pending(self) -> int:
        return 0 # Synchronous property doesn't work well with DB, tests should use drain()

    @property
    def last_result(self) -> PipelineResult | None:
        return self._last_result

    async def _run_forever(self) -> None:
        from kg_contracts.models import SourceKind
        
        # Recover any jobs that were processing when the server crashed
        await self.db.execute("UPDATE pipeline_jobs SET status = 'pending' WHERE status = 'processing'")
        
        while True:
            row = await self.db.fetch_one(
                """
                UPDATE pipeline_jobs 
                SET status = 'processing', updated_at = ? 
                WHERE job_id = (
                    SELECT job_id FROM pipeline_jobs 
                    WHERE status = 'pending' 
                    ORDER BY created_at ASC LIMIT 1
                )
                RETURNING job_id
                """, (datetime.now(UTC).isoformat(),)
            )
            if not row:
                self.tracker.idle()
                await asyncio.sleep(1.0)
                continue
                
            job_id = row["job_id"]
            
            doc_row = await self.documents.get(job_id)
            if not doc_row:
                await self.db.execute(
                    "UPDATE pipeline_jobs SET status = 'failed', error = 'Document not found' WHERE job_id = ?",
                    (job_id,)
                )
                continue
                
            document = CanonicalDocument(
                document_id=doc_row["document_id"],
                source_id=doc_row["source_id"],
                source_kind=SourceKind(doc_row["source_kind"]),
                name=doc_row["name"],
                media_type=doc_row["media_type"],
                content_hash=doc_row["content_hash"],
                byte_size=doc_row["byte_size"],
                text=doc_row["text"],
                access_tag=doc_row["access_tag"]
            )

            try:
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        self._last_result = await self.process(document)
                        break
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        if attempt == max_retries - 1:
                            raise e
                        await asyncio.sleep(2 ** attempt)

                now = datetime.now(UTC).isoformat()
                await self.db.execute(
                    "UPDATE pipeline_jobs SET status = 'completed', updated_at = ? WHERE job_id = ?",
                    (now, job_id)
                )
                
                metrics = [
                    (job_id, 'chunks', float(self._last_result.chunks), now),
                    (job_id, 'kept_chunks', float(self._last_result.kept_chunks), now),
                    (job_id, 'dropped_chunks', float(self._last_result.dropped_chunks), now),
                    (job_id, 'episodes', float(self._last_result.episodes), now),
                    (job_id, 'nodes_created', float(self._last_result.nodes_created), now),
                    (job_id, 'edges_created', float(self._last_result.edges_created), now),
                    (job_id, 'invalidated_edges', float(self._last_result.invalidated_edges), now),
                    (job_id, 'llm_calls', float(self._last_result.llm_calls), now),
                ]
                await self.db.executemany(
                    "INSERT INTO pipeline_metrics (job_id, metric_name, metric_value, recorded_at) VALUES (?, ?, ?, ?)",
                    metrics
                )
            except asyncio.CancelledError:
                await self.db.execute("UPDATE pipeline_jobs SET status = 'pending' WHERE job_id = ?", (job_id,))
                raise
            except Exception as e:
                logger.exception("pipeline failed for document %s", document.document_id)
                now = datetime.now(UTC).isoformat()
                await self.db.execute(
                    "UPDATE pipeline_jobs SET status = 'failed', error = ?, updated_at = ? WHERE job_id = ?",
                    (str(e), now, job_id)
                )

    # --- The seven stages --------------------------------------------------

    async def process(self, document: CanonicalDocument) -> PipelineResult:
        self.tracker.reset()
        result = PipelineResult(document_id=document.document_id)

        # 1. Ingestion
        self.tracker.begin("Ingestion")
        await self.documents.save(document)
        await self.sources.ensure(document.source_id, document.source_kind.value)
        await self.sources.recount(document.source_id)
        await self.sources.touch(document.source_id)
        
        # Ensure a deterministic Document node exists for the canonical document
        node_uuid = f"doc-{document.document_id}"
        metadata = getattr(document, 'metadata', {})
        
        # Don't create Document node for GitHub or Mail since they manage their own hierarchical nodes
        if document.source_kind not in (SourceKind.GITHUB, SourceKind.EMAIL):
            nodes = [{
                "uuid": node_uuid,
                "name": document.name,
                "labels": ["Document"],
                "summary": f"Uploaded document: {document.name}",
                "attributes": {
                    "type": "document",
                    "document_id": document.document_id,
                    "filename": document.name,
                    "source_type": document.source_kind.value,
                    "external_id": document.document_id,
                    "source_ids": [document.source_id],
                    "document_ids": [document.document_id],
                    "episode_ids": [],
                    **metadata
                }
            }]
            await self.graph.add_deterministic_nodes_and_edges("synapse", nodes, [])
            
        self.tracker.record("Ingestion")
        await self.activity.record(
            document.source_id, "Document ingested", f"{document.name} ({document.byte_size} bytes)"
        )

        # 2. Chunking
        self.tracker.begin("Chunking")
        chunks = await asyncio.to_thread(chunk_document, document)
        await self.documents.save_chunks(chunks)
        result.chunks = len(chunks)
        self.tracker.record("Chunking", processed=len(chunks))

        # 3. Extraction — pre-filter first, then the expensive path.
        self.tracker.begin("Extraction")
        seen = await self._known_hashes(document.document_id)
        filtered = await asyncio.to_thread(prefilter_chunks, chunks, seen_hashes=seen)
        await self._log_drops(document.document_id, filtered.decisions)

        episodes = await asyncio.to_thread(
            build_episodes,
            filtered.kept,
            target_chars=self.batch_target_chars,
        )
        episodes, short_drops = await asyncio.to_thread(
            prune_short_episodes,
            episodes, min_chars=self.prefilter_min_chars
        )
        await self._log_drops(document.document_id, short_drops)

        result.kept_chunks = len(filtered.kept)
        result.dropped_chunks = filtered.dropped + len(short_drops)
        result.episodes = len(episodes)

        await self.db.increment("prefilter_seen", filtered.total)
        await self.db.increment("prefilter_dropped", result.dropped_chunks)
        # Episodes avoided is the §8 cost benchmark: every drop is an LLM call not made.
        await self.db.increment("episodes_avoided", result.dropped_chunks)

        # Skip Graphiti extraction for GitHub and Email files to avoid hallucinated LLM entities.
        # The graph structure for code and mail is added deterministically via parsers.
        if document.source_kind in (SourceKind.GITHUB, SourceKind.EMAIL):
            for payload in episodes:
                await self.receipts.record_intent(payload)
            self.tracker.record("Extraction", processed=len(episodes))
            self.tracker.record("Resolution", processed=0)
            self.tracker.record("Consolidation", processed=0)
        else:
            for payload in episodes:
                await self.receipts.record_intent(payload)

                write = await self.graph.add_episode(payload)
                if write.error:
                    result.errors.append(write.error)
                    await self.receipts.fail(payload.episode_id, write.error)
                    self.tracker.record("Extraction", processed=0, errors=1)
                    continue

                await self.receipts.commit(
                    payload.episode_id,
                    graph_uuid=write.episode_uuid,
                    nodes_created=write.nodes_created,
                    edges_created=write.edges_created,
                    llm_calls=write.llm_calls,
                    latency_ms=write.latency_ms,
                )

                result.nodes_created += write.nodes_created
                result.edges_created += write.edges_created
                result.invalidated_edges += write.invalidated_edges
                result.llm_calls += write.llm_calls

                # Stages 3-5 all advance from one add_episode call: Graphiti performs
                # extraction, resolution and the temporal write inside a single pass.
                self.tracker.record("Extraction")
                self.tracker.record("Resolution", processed=write.nodes_created)
                self.tracker.record("Consolidation", processed=write.edges_created)

        await self.activity.record(
            document.source_id,
            "Episodes written",
            f"{result.episodes} episode(s), {result.dropped_chunks} chunk(s) filtered",
        )

        if result.invalidated_edges:
            await self.activity.record(
                document.source_id,
                "Facts superseded",
                f"{result.invalidated_edges} edge(s) closed by newer information",
            )

        # Snapshot per document so a crash costs at most the document in flight,
        # rather than every episode written since the process started.
        await self.graph.persist()
        await self.persist_stages()

        now = datetime.now(UTC).isoformat()
        job_id = f"job-{document.document_id}"
        await self.db.execute(
            "INSERT OR IGNORE INTO pipeline_jobs (job_id, status, created_at, updated_at) "
            "VALUES (?, 'completed', ?, ?)",
            (job_id, now, now)
        )
        for metric, val in [("chunks", result.chunks), ("nodes_created", result.nodes_created), ("edges_created", result.edges_created)]:
            await self.db.execute(
                "INSERT INTO pipeline_metrics (job_id, metric_name, metric_value, recorded_at) VALUES (?, ?, ?, ?)",
                (job_id, metric, float(val), now)
            )

        self.tracker.idle()
        return result

    async def persist_stages(self) -> None:
        """Write the funnel counters through so they survive a restart."""
        for name, value in self.tracker.as_counters().items():
            await self.db.execute(
                "INSERT INTO counters(name, value) VALUES(?, ?) "
                "ON CONFLICT(name) DO UPDATE SET value = excluded.value",
                (name, value),
            )

    async def build_communities(self) -> int:
        self.tracker.begin("Community Detection")
        count = await self.graph.build_communities()
        self.tracker.record("Community Detection", processed=count)
        self.tracker.idle()
        return count

    # --- Helpers -----------------------------------------------------------

    async def _known_hashes(self, document_id: str) -> set[str]:
        """Chunk hashes already ingested, so a re-upload is filtered as duplicate."""
        rows = await self.db.fetch_all(
            "SELECT DISTINCT content_hash FROM chunks WHERE document_id != ?", (document_id,)
        )
        return {row["content_hash"] for row in rows}

    async def _log_drops(self, document_id: str, decisions) -> None:
        dropped = [d for d in decisions if d.outcome is not PrefilterOutcome.KEPT]
        if not dropped:
            return
        now = datetime.now(UTC).isoformat()
        await self.db.executemany(
            "INSERT INTO prefilter_drops(chunk_id, document_id, outcome, rule, dropped_text, "
            "created_at) VALUES(?, ?, ?, ?, ?, ?)",
            [
                (d.chunk_id, document_id, d.outcome.value, d.rule, d.dropped_text, now)
                for d in dropped
            ],
        )


def _dumps(value: object) -> str:
    return json.dumps(value, default=str)
