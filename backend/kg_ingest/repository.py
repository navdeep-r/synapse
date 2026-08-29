"""Persistence for sources, documents, chunks and the activity feed."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from app.db import Database
from kg_contracts import CanonicalDocument, Chunk, Provenance


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def humanize_timestamp(value: datetime | str | None) -> str:
    """Render a relative time. The console prints `last_cdc_run` verbatim."""
    moment = _parse_iso(value) if isinstance(value, str) else value
    if moment is None:
        return "Never"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)

    seconds = (datetime.now(UTC) - moment).total_seconds()
    if seconds < 60:
        return "Just now"
    if seconds < 3600:
        minutes = int(seconds // 60)
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    if seconds < 86400:
        hours = int(seconds // 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = int(seconds // 86400)
    return f"{days} day{'s' if days != 1 else ''} ago"


class SourceRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def ensure(
        self, source_id: str, source_type: str, status: str = "confirmed", tenant: str = "default"
    ) -> None:
        await self._db.execute(
            "INSERT INTO sources(id, type, tenant, status, last_cdc_run, doc_count, created_at) "
            "VALUES(?, ?, ?, ?, NULL, 0, ?) ON CONFLICT(id) DO NOTHING",
            (source_id, source_type, tenant, status, _iso(datetime.now(UTC))),
        )

    async def list_all(self) -> list[dict]:
        return await self._db.fetch_all("SELECT * FROM sources ORDER BY id")

    async def get(self, source_id: str) -> dict | None:
        return await self._db.fetch_one("SELECT * FROM sources WHERE id = ?", (source_id,))

    async def touch(self, source_id: str, status: str | None = None) -> None:
        now = _iso(datetime.now(UTC))
        if status:
            await self._db.execute(
                "UPDATE sources SET last_cdc_run = ?, status = ? WHERE id = ?",
                (now, status, source_id),
            )
        else:
            await self._db.execute(
                "UPDATE sources SET last_cdc_run = ? WHERE id = ?", (now, source_id)
            )

    async def set_status(self, source_id: str, status: str) -> None:
        await self._db.execute("UPDATE sources SET status = ? WHERE id = ?", (status, source_id))

    async def recount(self, source_id: str) -> None:
        await self._db.execute(
            "UPDATE sources SET doc_count = "
            "(SELECT COUNT(*) FROM documents WHERE source_id = ?) WHERE id = ?",
            (source_id, source_id),
        )


class ActivityRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def record(self, source_id: str, event: str, details: str = "") -> str:
        activity_id = f"act-{uuid.uuid4().hex[:12]}"
        await self._db.execute(
            "INSERT INTO activity(id, source_id, event, details, created_at) VALUES(?, ?, ?, ?, ?)",
            (activity_id, source_id, event, details, _iso(datetime.now(UTC))),
        )
        return activity_id

    async def list_for_source(self, source_id: str, limit: int = 100) -> list[dict]:
        return await self._db.fetch_all(
            "SELECT * FROM activity WHERE source_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (source_id, limit),
        )

    async def list_all(self, limit: int = 200) -> list[dict]:
        return await self._db.fetch_all(
            "SELECT * FROM activity ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
        )


class DocumentRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def find_by_hash(self, content_hash: str) -> dict | None:
        return await self._db.fetch_one(
            "SELECT * FROM documents WHERE content_hash = ?", (content_hash,)
        )

    async def save(self, document: CanonicalDocument) -> None:
        await self._db.execute(
            "INSERT INTO documents(document_id, source_id, source_kind, name, media_type, "
            "tenant, content_hash, byte_size, access_tag, text, ingested_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(document_id) DO UPDATE SET text = excluded.text",
            (
                document.document_id,
                document.source_id,
                document.source_kind.value,
                document.name,
                document.media_type,
                document.tenant,
                document.content_hash,
                document.byte_size,
                document.access_tag,
                document.text,
                _iso(document.ingested_at),
            ),
        )

    async def get(self, document_id: str) -> dict | None:
        return await self._db.fetch_one(
            "SELECT * FROM documents WHERE document_id = ?", (document_id,)
        )

    async def name_for(self, document_id: str) -> str:
        row = await self._db.fetch_one(
            "SELECT name FROM documents WHERE document_id = ?", (document_id,)
        )
        return row["name"] if row else "Unknown Document"

    async def count(self) -> int:
        return int(await self._db.fetch_value("SELECT COUNT(*) AS n FROM documents", (), 0) or 0)

    async def latest_ingested_at(self) -> datetime | None:
        raw = await self._db.fetch_value("SELECT MAX(ingested_at) AS m FROM documents")
        return _parse_iso(raw)

    async def save_chunks(self, chunks: list[Chunk]) -> None:
        await self._db.executemany(
            "INSERT INTO chunks(chunk_id, document_id, ordinal, text, offset, char_length, "
            "content_hash, provenance_json) VALUES(?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(chunk_id) DO NOTHING",
            [
                (
                    c.chunk_id,
                    c.document_id,
                    c.ordinal,
                    c.text,
                    c.offset,
                    c.char_length,
                    c.content_hash,
                    c.provenance.model_dump_json(),
                )
                for c in chunks
            ],
        )

    async def chunk_provenance(self, chunk_id: str) -> Provenance | None:
        row = await self._db.fetch_one(
            "SELECT provenance_json FROM chunks WHERE chunk_id = ?", (chunk_id,)
        )
        if not row:
            return None
        return Provenance.model_validate(json.loads(row["provenance_json"]))

    async def chunk_hash_exists(self, content_hash: str, exclude_document: str) -> bool:
        row = await self._db.fetch_one(
            "SELECT 1 AS hit FROM chunks WHERE content_hash = ? AND document_id != ? LIMIT 1",
            (content_hash, exclude_document),
        )
        return row is not None
