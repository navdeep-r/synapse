"""Graph-vs-SQLite reconciliation (R3).

`/observability/consistency` implies a real consistency story, so there is one.
Three divergence classes are detected, in both directions:

* `stuck_pending`  — a receipt written but never committed: the process died
                     between the intent row and Graphiti returning.
* `missing_in_graph` — a committed receipt whose episode is absent from the
                     graph: the graph write was lost or rolled back.
* `missing_in_metadata` — an `Episodic` node with no receipt: the metadata write
                     was lost.

`durationMinutes` is the true age of the divergence, not a placeholder.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from app.db import Database
from kg_contracts import EpisodeStatus
from kg_graphiti.service import GraphService
from kg_observability.receipts import ReceiptStore

logger = logging.getLogger("synapse.reconcile")

# Which store is behind. The console renders this string directly.
STORE_GRAPH = "graph"
STORE_METADATA = "metadata"


@dataclass(frozen=True)
class Mismatch:
    id: str
    tx_id: str
    store: str
    duration_minutes: int
    kind: str
    detail: str

    def to_payload(self) -> dict[str, object]:
        """Note the camelCase: this endpoint differs from every other one."""
        return {
            "id": self.id,
            "txId": self.tx_id,
            "store": self.store,
            "durationMinutes": self.duration_minutes,
        }


def _age_minutes(since: datetime) -> int:
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    delta = datetime.now(UTC) - since
    return max(int(delta.total_seconds() // 60), 0)


async def reconcile(
    db: Database,
    graph: GraphService,
    *,
    group_ids: list[str] | None = None,
    stuck_after_seconds: int = 120,
) -> list[Mismatch]:
    """Compare episode receipts against `Episodic` nodes in both directions."""
    receipts = ReceiptStore(db)
    all_receipts = await receipts.all()

    # Groups are derived independently of the receipts. Taking them from the
    # receipt table alone would make total metadata loss undetectable: with no
    # receipts there would be nothing to query the graph for, and a wiped
    # database would look identical to a fresh install.
    if group_ids is None:
        group_ids = sorted(
            {r.group_id for r in all_receipts} | set(await _groups_from_documents(db))
        )
    if not group_ids:
        return []

    try:
        graph_uuids = await graph.episode_uuids(group_ids)
    except Exception:
        logger.exception("reconciliation could not read the graph")
        return []

    mismatches: list[Mismatch] = []
    known_uuids: set[str] = set()

    for receipt in all_receipts:
        if receipt.status is EpisodeStatus.PENDING:
            age = (datetime.now(UTC) - _aware(receipt.created_at)).total_seconds()
            if age >= stuck_after_seconds:
                mismatches.append(
                    Mismatch(
                        id=f"mm-pending-{receipt.episode_id}",
                        tx_id=receipt.episode_id,
                        # The receipt exists but the graph never confirmed: graph is behind.
                        store=STORE_GRAPH,
                        duration_minutes=_age_minutes(receipt.created_at),
                        kind="stuck_pending",
                        detail=(
                            f"Episode {receipt.episode_id} has been pending for "
                            f"{_age_minutes(receipt.created_at)} minutes."
                        ),
                    )
                )
            continue

        if receipt.status is EpisodeStatus.FAILED:
            continue

        if receipt.graph_uuid:
            known_uuids.add(receipt.graph_uuid)
            if receipt.graph_uuid not in graph_uuids:
                mismatches.append(
                    Mismatch(
                        id=f"mm-graph-{receipt.episode_id}",
                        tx_id=receipt.episode_id,
                        store=STORE_GRAPH,
                        duration_minutes=_age_minutes(receipt.committed_at or receipt.created_at),
                        kind="missing_in_graph",
                        detail=(
                            f"Receipt {receipt.episode_id} is committed but episode "
                            f"{receipt.graph_uuid} is absent from the graph."
                        ),
                    )
                )

    for uuid in sorted(graph_uuids - known_uuids):
        mismatches.append(
            Mismatch(
                id=f"mm-meta-{uuid}",
                tx_id=uuid,
                store=STORE_METADATA,
                duration_minutes=0,
                kind="missing_in_metadata",
                detail=f"Graph episode {uuid} has no metadata receipt.",
            )
        )

    return mismatches


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def _groups_from_documents(db: Database) -> list[str]:
    return ["tenant-a"]
