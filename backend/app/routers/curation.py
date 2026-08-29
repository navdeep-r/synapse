"""Curation endpoints — the steward review queue.

The merge queue is regenerated from the live graph on read, then reconciled with
stored decisions, so a pair a steward has already ruled on never comes back.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException

from app.services import build_provenance_index, episodes_of, read_graph
from app.state import AppState, get_state
from kg_audit import find_acl_issues, find_conflicts, find_contradictions, find_merge_candidates
from kg_ontology import OntologyRegistry
from kg_acl.authorized_graph import AuthorizedGraphRepository
from kg_acl.principal import get_authorized_graph, require_supervisor

router = APIRouter(dependencies=[Depends(require_supervisor)])


def _now() -> str:
    return datetime.now(UTC).isoformat()


@router.get("/curation/queue")
async def review_queue(state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)) -> list[dict]:
    try:
        entities, _edges = await read_graph(state, graph)
        candidates = find_merge_candidates(entities)
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=traceback.format_exc())


    decided = {
        (row["primary_uuid"], row["secondary_uuid"])
        for row in await state.db.fetch_all(
            "SELECT primary_uuid, secondary_uuid FROM curation_candidates WHERE status != 'open'"
        )
    }

    payload = []
    for candidate in candidates:
        pair = (candidate.primary_uuid, candidate.secondary_uuid)
        if pair in decided or (pair[1], pair[0]) in decided:
            continue

        row = await state.db.fetch_one(
            "SELECT id FROM curation_candidates WHERE primary_uuid = ? AND secondary_uuid = ?",
            pair,
        )
        candidate_id = row["id"] if row else f"cand-{uuid.uuid4().hex[:10]}"
        if row is None:
            await state.db.execute(
                "INSERT INTO curation_candidates(id, group_id, entity_type, primary_uuid, "
                "primary_name, secondary_uuid, secondary_name, confidence, reason, status, "
                "created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)",
                (
                    candidate_id,
                    candidate.group_id,
                    candidate.entity_type,
                    candidate.primary_uuid,
                    candidate.primary_name,
                    candidate.secondary_uuid,
                    candidate.secondary_name,
                    candidate.confidence,
                    candidate.reason,
                    _now(),
                ),
            )

        payload.append(
            {
                "id": candidate_id,
                "entity_type": candidate.entity_type,
                "primary_id": candidate.primary_uuid,
                "primary_name": candidate.primary_name,
                "secondary_id": candidate.secondary_uuid,
                "secondary_name": candidate.secondary_name,
                "confidence": candidate.confidence,
                "reason": candidate.reason,
                "tenant": candidate.group_id,
                "status": "open",
            }
        )

    return payload


@router.post("/curation/queue/{candidate_id}/merge")
async def merge_candidate(candidate_id: str, state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)) -> dict:
    return await _decide(state, candidate_id, "merged")


@router.post("/curation/queue/{candidate_id}/distinct")
async def keep_distinct(candidate_id: str, state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)) -> dict:
    return await _decide(state, candidate_id, "distinct")


async def _decide(state: AppState, candidate_id: str, decision: str) -> dict:
    row = await state.db.fetch_one(
        "SELECT * FROM curation_candidates WHERE id = ?", (candidate_id,)
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown candidate: {candidate_id}")

    await state.db.execute(
        "UPDATE curation_candidates SET status = ?, decided_at = ? WHERE id = ?",
        (decision, _now(), candidate_id),
    )

    if decision == "merged":
        # The decision is recorded rather than rewriting the graph: Graphiti owns
        # node identity, and a destructive merge here would desynchronise the
        # provenance stored against the secondary node's episodes.
        await state.db.execute(
            "INSERT INTO quarantine(id, kind, subject, detail, created_at) VALUES(?, ?, ?, ?, ?)",
            (
                f"merge-{candidate_id}",
                "merge_decision",
                row["primary_name"],
                f"Steward merged '{row['secondary_name']}' into '{row['primary_name']}'.",
                _now(),
            ),
        )

    return {
        "id": candidate_id,
        "status": decision,
        "primary_name": row["primary_name"],
        "secondary_name": row["secondary_name"],
        "decided_at": _now(),
    }


@router.get("/curation/quarantine")
async def quarantine(state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)) -> list[dict]:
    rows = await state.db.fetch_all("SELECT * FROM quarantine ORDER BY created_at DESC LIMIT 200")
    return [
        {
            "id": row["id"],
            "kind": row["kind"],
            "subject": row["subject"],
            "detail": row["detail"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


@router.get("/curation/contradictions")
async def contradictions(state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)) -> list[dict]:
    _entities, edges = await read_graph(state, graph)
    return find_contradictions(edges)


@router.get("/curation/conflicts")
async def conflicts(state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)) -> list[dict]:
    _entities, edges = await read_graph(state, graph)
    relations = await OntologyRegistry(state.db).relation_types()
    return find_conflicts(edges, relations)


@router.get("/curation/acl")
async def acl_issues(state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)) -> list[dict]:
    _entities, edges = await read_graph(state, graph)
    index = await build_provenance_index(state)
    tags = {
        episode: index.document_tags.get(document, "internal")
        for episode, document in index.episode_to_document.items()
    }
    for edge in edges:
        edge["episodes"] = episodes_of(edge)
    return find_acl_issues(edges, tags)


@router.get("/curation/calibration")
async def calibration(state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)) -> list[dict]:
    """Threshold suggestions derived from the current review queue (R7)."""
    await _refresh_calibration(state)
    rows = await state.db.fetch_all(
        "SELECT * FROM calibration WHERE status = 'open' ORDER BY created_at DESC"
    )
    return [
        {
            "id": row["id"],
            "title": row["title"],
            "current_val": row["current_val"],
            "recommended_val": row["recommended_val"],
            "recall_impact": row["recall_impact"],
            "status": row["status"],
        }
        for row in rows
    ]


@router.post("/curation/calibration/{item_id}/approve")
async def approve_calibration(item_id: str, state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)) -> dict:
    return await _resolve_calibration(state, item_id, "approved")


@router.post("/curation/calibration/{item_id}/dismiss")
async def dismiss_calibration(item_id: str, state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)) -> dict:
    return await _resolve_calibration(state, item_id, "dismissed")


async def _resolve_calibration(state: AppState, item_id: str, status: str) -> dict:
    row = await state.db.fetch_one("SELECT * FROM calibration WHERE id = ?", (item_id,))
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown calibration item: {item_id}")
    await state.db.execute("UPDATE calibration SET status = ? WHERE id = ?", (status, item_id))
    return {"id": item_id, "status": status, "title": row["title"]}


async def _refresh_calibration(state: AppState) -> None:
    """Propose a filter threshold change when drops look excessive (R7).

    The pre-ingest filter is the one place a false positive removes content
    before it can ever reach the graph, so a high drop rate is surfaced as a
    reviewable proposal rather than left to be noticed by accident.
    """
    counters = await state.db.counters()
    seen = counters.get("prefilter_seen", 0)
    dropped = counters.get("prefilter_dropped", 0)
    if seen < 20:
        return

    drop_rate = dropped / seen
    existing = await state.db.fetch_one(
        "SELECT id FROM calibration WHERE id = 'cal-prefilter-drop-rate'"
    )
    if drop_rate <= 0.4 or existing is not None:
        return

    await state.db.execute(
        "INSERT INTO calibration(id, title, current_val, recommended_val, recall_impact, "
        "status, created_at) VALUES(?, ?, ?, ?, ?, 'open', ?)",
        (
            "cal-prefilter-drop-rate",
            "Pre-ingest filter is dropping an unusual share of content",
            round(drop_rate, 4),
            0.25,
            (
                f"{dropped} of {seen} chunks were filtered before extraction. Review the drop "
                "log for false positives before treating this rate as normal."
            ),
            _now(),
        ),
    )
