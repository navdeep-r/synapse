"""Snapshot generation, history and delivery."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.services import build_provenance_index, edge_tag_map, episodes_of, read_graph
from app.state import AppState, get_state
from kg_export import SnapshotBuilder
from kg_export.snapshots import checks_to_payload
from kg_ingest import humanize_timestamp
from kg_ontology import OntologyRegistry
from kg_acl.authorized_graph import AuthorizedGraphRepository
from kg_acl.principal import get_authorized_graph

router = APIRouter()

CONFIRMATION_PHRASE = "I understand the risk"

# The form's access-control choices map onto the three ACL policies.
POLICY_BY_HANDLING = {
    "filter_group": "strict",
    "tag_downstream": "balanced",
    "export_unrestricted": "permissive",
}


class SnapshotRequest(BaseModel):
    """Mirrors the Zod schema in ExportScreen."""

    factScope: str = "current"
    reportScope: str = "flagged"
    accessControlHandling: str
    confirmationPhrase: str | None = None


@router.post("/snapshot/generate")
async def generate_snapshot(
    request: SnapshotRequest, state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)
) -> dict:
    if request.accessControlHandling not in POLICY_BY_HANDLING:
        raise HTTPException(
            status_code=422,
            detail=(
                "accessControlHandling must be one of: "
                f"{', '.join(sorted(POLICY_BY_HANDLING))}."
            ),
        )

    # Re-validated server-side: the client's Zod check is a convenience, not a control.
    if (
        request.accessControlHandling == "export_unrestricted"
        and (request.confirmationPhrase or "").strip() != CONFIRMATION_PHRASE
    ):
        raise HTTPException(
            status_code=422,
            detail=f"You must type '{CONFIRMATION_PHRASE}' to export unrestricted.",
        )

    state.tracker.begin("Export")
    try:
        entities, edges = await read_graph(state, graph)
        index = await build_provenance_index(state)
        for edge in edges:
            edge["episodes"] = episodes_of(edge)

        scoped_edges = edges
        relations = await OntologyRegistry(state.db).relation_types()

        result = SnapshotBuilder(state.settings.snapshot_dir).build(
            entities=entities,
            edges=scoped_edges,
            fact_scope="active" if request.factScope == "current" else "all",
            report_scope=request.reportScope,
            acl_policy=POLICY_BY_HANDLING[request.accessControlHandling],
            edge_tags=edge_tag_map(scoped_edges, index),
            reports=await _reports(state, graph),
            known_relations={r["relation_type"] for r in relations},
        )

        await state.db.execute(
            "INSERT INTO snapshots(version, fact_scope, report_scope, acl_policy, "
            "entity_count, edge_count, removed_edges, byte_size, validation_json, passed, path, "
            "created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                result.version,
                result.fact_scope,
                result.report_scope,
                result.acl_policy,
                result.entity_count,
                result.edge_count,
                result.removed_edges,
                result.byte_size,
                json.dumps(checks_to_payload(result.checks)),
                int(result.passed),
                result.path,
                result.created_at,
            ),
        )
        state.tracker.record("Export", processed=1, errors=0 if result.passed else 1)

        # Auto-deliver to AgentSuite if snapshot passed validation
        delivery_status = "not delivered"
        if result.passed and result.path:
            delivery = await state.db.get_setting("delivery", {})
            endpoint = _resolve_delivery_endpoint(delivery, state.settings)
            _ok, delivery_status = await _deliver_snapshot(result.path, endpoint)

        return {
            "version": result.version,
            "passed": result.passed,
            "validation_checks": checks_to_payload(result.checks),
            "entity_count": result.entity_count,
            "edge_count": result.edge_count,
            "report_count": result.report_count,
            "removed_edges": result.removed_edges,
            "byte_size": result.byte_size,
            "acl_policy": result.acl_policy,
            "created_at": result.created_at,
            "delivery_status": delivery_status,
            # Presentation fields, shared with /snapshots so the history table and
            # the freshly generated result can be rendered by the same code.
            "date": humanize_timestamp(result.created_at),
            "size": _human_size(result.byte_size),
            "size_bytes": result.byte_size,
            "policy": result.acl_policy,
            "diff": {
                "addedEntities": result.entity_count,
                "removedEdges": result.removed_edges,
            },
        }
    finally:
        state.tracker.idle()


@router.get("/snapshots")
async def list_snapshots(state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)) -> list[dict]:
    rows = await state.db.fetch_all("SELECT * FROM snapshots ORDER BY created_at DESC")
    return [
        {
            "version": row["version"],
            "date": humanize_timestamp(row["created_at"]),
            "size": _human_size(int(row["byte_size"])),
            "size_bytes": int(row["byte_size"]),
            "passed": bool(row["passed"]),
            "acl_policy": row["acl_policy"],
            "policy": row["acl_policy"],
            "entity_count": int(row["entity_count"]),
            "edge_count": int(row["edge_count"]),
            "removed_edges": int(row["removed_edges"]),
            "validation_checks": json.loads(row["validation_json"] or "[]"),
            "diff": {
                "addedEntities": int(row["entity_count"]),
                "removedEdges": int(row["removed_edges"]),
            },
        }
        for row in rows
    ]


@router.get("/snapshots/{version}/download")
async def download_snapshot(version: str, state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)) -> FileResponse:
    if version == "latest":
        # The delivery tab hands this URL to downstream systems, which have no
        # way to know the newest version string. Only a passing snapshot is
        # ever served here: a failed one must not leave the platform.
        row = await state.db.fetch_one(
            "SELECT * FROM snapshots WHERE passed = 1 ORDER BY created_at DESC LIMIT 1"
        )
        if row is None:
            raise HTTPException(status_code=404, detail="No validated snapshot has been generated.")
        version = row["version"]
    else:
        row = await state.db.fetch_one("SELECT * FROM snapshots WHERE version = ?", (version,))

    if row is None or not row.get("path"):
        raise HTTPException(status_code=404, detail=f"Unknown snapshot: {version}")

    path = Path(row["path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Snapshot file is no longer on disk.")

    await state.db.execute(
        "UPDATE snapshots SET fetched_at = ? WHERE version = ?",
        (datetime.now(UTC).isoformat(), version),
    )
    return FileResponse(path, media_type="application/gzip", filename=f"{version}.json.gz")


@router.post("/snapshots/{version}/notify")
async def notify_downstream(version: str, state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)) -> dict:
    row = await state.db.fetch_one("SELECT * FROM snapshots WHERE version = ?", (version,))
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown snapshot: {version}")
    if not row["passed"]:
        raise HTTPException(
            status_code=409,
            detail="Snapshot failed validation and must not be delivered downstream.",
        )

    now = datetime.now(UTC).isoformat()
    await state.db.execute(
        "UPDATE snapshots SET notified_at = ? WHERE version = ?", (now, version)
    )
    delivery = await state.db.get_setting("delivery", {})
    endpoint = _resolve_delivery_endpoint(delivery, state.settings)
    success, detail = await _deliver_snapshot(row["path"], endpoint)
    return {
        "version": version,
        "notified_at": now,
        "endpoint": endpoint,
        "status": "delivered" if success else "failed",
        "detail": detail,
    }


def _resolve_delivery_endpoint(delivery_setting: dict | None = None, settings: Any = None) -> str:
    """Resolve downstream sync endpoint prioritizing DB settings, environment variables, or config."""
    import os
    setting_val = (delivery_setting or {}).get("endpoint")
    if setting_val and str(setting_val).strip():
        return str(setting_val).strip()
    return (
        os.getenv("AGENTSUITE_SYNC_URL")
        or os.getenv("SYNAPSE_AGENTSUITE_SYNC_URL")
        or os.getenv("SYNAPSE_DELIVERY_ENDPOINT")
        or (getattr(settings, "agentsuite_sync_url", None) if settings else None)
        or (getattr(settings, "delivery_endpoint", None) if settings else None)
        or "http://localhost:8001/api/v1/graph/sync"
    )


async def _deliver_snapshot(path_str: str | None, endpoint_url: str | None = None) -> tuple[bool, str]:
    if not path_str:
        return False, "No snapshot path provided."
    path = Path(path_str)
    if not path.exists():
        return False, "Snapshot file does not exist on disk."
    
    url = endpoint_url or _resolve_delivery_endpoint()
    try:
        import gzip
        import httpx
        with gzip.open(path, "rt", encoding="utf-8") as gz:
            snapshot_data = json.load(gz)
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=snapshot_data)
            if resp.status_code < 400:
                return True, f"Successfully delivered snapshot to AgentSuite at {url} (HTTP {resp.status_code})."
            return False, f"Failed delivery to {url}: HTTP {resp.status_code} - {resp.text[:120]}"
    except Exception as exc:
        return False, f"Delivery attempt to {url} failed: {exc}"


async def _reports(state: AppState, graph: AuthorizedGraphRepository) -> list[dict]:
    from app.routers.reports import build_reports

    return await build_reports(state, graph)


def _human_size(byte_size: int) -> str:
    if byte_size <= 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if byte_size < 1024:
            return f"{byte_size:.0f} {unit}" if unit == "B" else f"{byte_size:.1f} {unit}"
        byte_size /= 1024.0
    return f"{byte_size:.1f} TB"
