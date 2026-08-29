"""Observability endpoints.

`/observability/pipeline` is polled once per second, so it reads only in-memory
counters. The other three do real work and are polled every five seconds.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException

from app.services import read_graph
from app.state import AppState, get_state
from kg_audit import find_conflicts
from kg_ingest import DocumentRepository, humanize_timestamp
from kg_observability.receipts import ReceiptStore
from kg_observability.reconcile import reconcile
from kg_observability.telemetry import TelemetryStore
from kg_ontology import OntologyRegistry
from kg_acl.authorized_graph import AuthorizedGraphRepository
from kg_acl.principal import get_authorized_graph, require_supervisor

router = APIRouter(dependencies=[Depends(require_supervisor)])


@router.get("/observability/pipeline")
async def pipeline(state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)) -> dict:
    """Cheap in-memory read: `processed_count` is always a non-null int."""
    return state.tracker.snapshot()


@router.get("/observability/llm-telemetry")
async def llm_telemetry(state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)) -> dict:
    """Detailed LLM telemetry."""
    store = TelemetryStore(state.db)
    return {
        "aggregate": await store.get_aggregate_stats(),
        "recent_calls": await store.get_recent_calls(limit=100),
    }


@router.get("/observability/consistency")
async def consistency(state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)) -> dict:
    """R3: real reconciliation, not an empty stub.

    Note the camelCase keys — this endpoint differs from every other one in the
    API because `MismatchItem` in ObservabilityScreen declares them that way.
    """
    mismatches = await reconcile(
        state.db,
        graph,
        stuck_after_seconds=state.settings.reconcile_stuck_after_seconds,
    )
    escalated = {
        row["mismatch_id"]
        for row in await state.db.fetch_all("SELECT mismatch_id FROM consistency_escalations")
    }
    return {
        "mismatches": [m.to_payload() for m in mismatches if m.id not in escalated]
    }


@router.post("/observability/consistency/{mismatch_id}/escalate")
async def escalate(mismatch_id: str, state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)) -> dict:
    """Acknowledge a mismatch so it stops being reported."""
    mismatches = await reconcile(
        state.db,
        graph,
        stuck_after_seconds=state.settings.reconcile_stuck_after_seconds,
    )
    match = next((m for m in mismatches if m.id == mismatch_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Unknown mismatch: {mismatch_id}")

    await state.db.execute(
        "INSERT INTO consistency_escalations(mismatch_id, created_at) VALUES(?, ?) "
        "ON CONFLICT(mismatch_id) DO NOTHING",
        (mismatch_id, datetime.now(UTC).isoformat()),
    )
    await state.db.execute(
        "INSERT INTO quarantine(id, kind, subject, detail, created_at) VALUES(?, ?, ?, ?, ?)",
        (
            f"esc-{mismatch_id}",
            "consistency",
            match.tx_id,
            match.detail,
            datetime.now(UTC).isoformat(),
        ),
    )
    return {"id": mismatch_id, "status": "escalated", "detail": match.detail}


@router.get("/observability/quality-metrics")
async def quality_metrics(state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)) -> dict:
    """Percentages are pre-formatted strings; `review_rate` alone is a 0-1 fraction."""
    counters = await state.db.counters()
    documents = DocumentRepository(state.db)

    # Source freshness: how long since anything was ingested.
    latest = await documents.latest_ingested_at()
    freshness = humanize_timestamp(latest) if latest else "No data"

    # CDC skip rate: share of chunks the pre-ingest filter dropped.
    seen = counters.get("prefilter_seen", 0)
    dropped = counters.get("prefilter_dropped", 0)
    skip_rate = dropped / seen if seen else 0.0

    # Parse failure rate over attempted documents.
    doc_count = await documents.count()
    failures = counters.get("parse_failures", 0)
    attempted = doc_count + failures
    parse_failure_rate = failures / attempted if attempted else 0.0

    # Temporal consistency: committed receipts over all non-failed receipts.
    receipt_counts = await ReceiptStore(state.db).counts()
    committed = receipt_counts.get("committed", 0)
    pending = receipt_counts.get("pending", 0)
    temporal = committed / (committed + pending) if (committed + pending) else 1.0

    # Conflict rate: active edges breaching max_active_outgoing.
    _entities, edges = await read_graph(state, graph)
    active = [e for e in edges if not e.get("invalid_at") and not e.get("expired_at")]
    relations = await OntologyRegistry(state.db).relation_types()
    conflicts = find_conflicts(edges, relations)
    conflict_rate = len(conflicts) / len(active) if active else 0.0

    # Review rate: open merge candidates over entities. A 0-1 fraction: the
    # screen multiplies by 100 itself.
    open_candidates = int(
        await state.db.fetch_value(
            "SELECT COUNT(*) AS n FROM curation_candidates WHERE status = 'open'", (), 0
        )
        or 0
    )
    entity_total = len(_entities)
    review_rate = open_candidates / entity_total if entity_total else 0.0

    # R2: a silent stub regression shows up here as a number.
    llm_calls = counters.get("llm_calls", 0) + graph.counters.get("llm_calls", 0)
    fallbacks = counters.get("stub_fallback", 0) + graph.counters.get("stub_fallback", 0)
    fallback_rate = fallbacks / llm_calls if llm_calls else 0.0

    return {
        "source_freshness": freshness,
        "cdc_skip_rate": _pct(skip_rate),
        "parse_failure_rate": _pct(parse_failure_rate),
        "temporal_consistency": _pct(temporal),
        "active_edge_conflict_rate": _pct(conflict_rate),
        "review_rate": round(review_rate, 4),
        # R2 and R7: surfaced so a regression is visible rather than discovered.
        "stub_fallback_rate": _pct(fallback_rate),
        "prefilter_drop_rate": _pct(skip_rate),
        "episodes_avoided": counters.get("episodes_avoided", 0),
        "llm_calls": llm_calls,
    }


@router.get("/observability/export-metrics")
async def export_metrics(state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)) -> dict:
    """Backs the previously-empty Export Metrics tab."""
    rows = await state.db.fetch_all("SELECT * FROM snapshots ORDER BY created_at DESC")

    last = rows[0] if rows else None
    fetched = next((r for r in rows if r.get("fetched_at")), None)

    return {
        "last_generation": (
            None
            if last is None
            else {
                "version": last["version"],
                "outcome": "passed" if last["passed"] else "failed",
                "entity_count": int(last["entity_count"]),
                "edge_count": int(last["edge_count"]),
                "removed_edges": int(last["removed_edges"]),
                "created_at": humanize_timestamp(last["created_at"]),
            }
        ),
        "last_downstream_fetch": (
            None
            if fetched is None
            else {
                "version": fetched["version"],
                "fetched_at": humanize_timestamp(fetched["fetched_at"]),
            }
        ),
        "size_trend": [
            {"version": row["version"], "byte_size": int(row["byte_size"])}
            for row in reversed(rows)
            if row["passed"]
        ],
        "total_snapshots": len(rows),
    }


def _pct(value: float) -> str:
    """Percentages are rendered verbatim by the console."""
    return f"{value * 100:.1f}%"
