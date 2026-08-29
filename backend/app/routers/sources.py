"""Sources, activity feed and uploads for the Data Sources screen."""

from __future__ import annotations

import logging
from datetime import UTC

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.state import AppState, get_state
from kg_ingest import ActivityRepository, SourceRepository, humanize_timestamp
from kg_ingest.service import build_document
from kg_parsing import SUPPORTED_EXTENSIONS, ParseError

logger = logging.getLogger("synapse.sources")
router = APIRouter()

# `status` must be one of these three; the Badge component keys off them.
VALID_STATUSES = {"confirmed", "signal", "alert"}


def _source_payload(row: dict) -> dict:
    return {
        "id": row["id"],
        "type": row["type"],
        "tenant": row["tenant"],
        # Pre-formatted: the screen prints this string verbatim.
        "last_cdc_run": humanize_timestamp(row.get("last_cdc_run")),
        "status": row["status"] if row["status"] in VALID_STATUSES else "signal",
        "doc_count": int(row.get("doc_count") or 0),
    }


def _activity_payload(row: dict) -> dict:
    return {
        "id": row["id"],
        "timestamp": humanize_timestamp(row.get("created_at")),
        "event": row["event"],
        "source_id": row["source_id"],
        "details": row.get("details") or "",
    }


@router.get("/sources")
async def list_sources(state: AppState = Depends(get_state)) -> list[dict]:
    rows = await SourceRepository(state.db).list_all()
    return [_source_payload(row) for row in rows]


@router.get("/sources/{source_id}/activity")
async def source_activity(source_id: str, state: AppState = Depends(get_state)) -> list[dict]:
    rows = await ActivityRepository(state.db).list_for_source(source_id)
    return [_activity_payload(row) for row in rows]


@router.get("/activity")
async def all_activity(state: AppState = Depends(get_state)) -> list[dict]:
    rows = await ActivityRepository(state.db).list_all()
    return [_activity_payload(row) for row in rows]


@router.post("/uploads")
async def upload(
    file: UploadFile = File(...), state: AppState = Depends(get_state)
) -> dict:
    """Accept a document and queue it for processing.

    R8: only the advertised size cap and extension allow-list are enforced. The
    client enforces neither, and no content-level defence against hostile files
    is implemented.
    """
    settings = state.settings
    filename = file.filename or "upload.txt"

    data = await file.read()
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"File exceeds the {settings.max_upload_bytes // (1024 * 1024)}MB limit "
                f"({len(data) // (1024 * 1024)}MB received)."
            ),
        )
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        document = build_document(
            filename,
            data,
            source_id="upload",
            tenant=settings.default_tenant,
        )
    except ParseError as exc:
        await _quarantine(state, filename, str(exc))
        raise HTTPException(
            status_code=400,
            detail=(
                f"{exc} Supported types: {', '.join(sorted(SUPPORTED_EXTENSIONS))}."
            ),
        ) from exc

    await SourceRepository(state.db).ensure("upload", "upload", settings.default_tenant)

    assert state.pipeline is not None
    await state.pipeline.submit(document)

    return {
        "document_id": document.document_id,
        "name": document.name,
        "byte_size": document.byte_size,
        "status": "queued",
        "queued_behind": max(state.pipeline.pending - 1, 0),
    }


@router.post("/sources/{source_id}/replay")
async def replay(source_id: str, state: AppState = Depends(get_state)) -> dict:
    """Re-run every document for a source through the pipeline.

    A replay is a genuine test of the pre-ingest filter: identical content should
    be dropped as duplicates rather than duplicating the graph.
    """
    sources = SourceRepository(state.db)
    if await sources.get(source_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown source: {source_id}")

    rows = await state.db.fetch_all(
        "SELECT * FROM documents WHERE source_id = ? ORDER BY ingested_at", (source_id,)
    )
    await ActivityRepository(state.db).record(
        source_id, "Replay triggered", f"{len(rows)} document(s) re-queued"
    )
    await sources.touch(source_id, status="signal")

    assert state.pipeline is not None
    from datetime import datetime

    from kg_contracts import CanonicalDocument, SourceKind

    for row in rows:
        await state.pipeline.submit(
            CanonicalDocument(
                document_id=row["document_id"],
                source_id=row["source_id"],
                source_kind=SourceKind(row["source_kind"]),
                name=row["name"],
                media_type=row["media_type"],
                text=row["text"],
                content_hash=row["content_hash"],
                ingested_at=datetime.fromisoformat(row["ingested_at"]),
                byte_size=int(row["byte_size"] or 0),
                access_tag=row["access_tag"],
            )
        )

    return {"source_id": source_id, "replayed": len(rows), "status": "queued"}


async def _quarantine(state: AppState, subject: str, detail: str) -> None:
    import uuid
    from datetime import datetime

    await state.db.execute(
        "INSERT INTO quarantine(id, kind, subject, detail, created_at) VALUES(?, ?, ?, ?, ?)",
        (
            f"qr-{uuid.uuid4().hex[:10]}",
            "parse_failure",
            subject,
            detail,
            datetime.now(UTC).isoformat(),
        ),
    )
    await state.db.increment("parse_failures")
