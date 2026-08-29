"""Turning raw uploads into canonical documents."""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime

from kg_contracts import CanonicalDocument, SourceKind
from kg_parsing import parse_document
from kg_parsing.resume import normalize_resume_text

_SLUG = re.compile(r"[^a-z0-9]+")


def _slug(value: str) -> str:
    return _SLUG.sub("-", value.lower()).strip("-")[:40] or "doc"


def build_document(
    filename: str,
    data: bytes,
    source_id: str,
    source_kind: SourceKind = SourceKind.UPLOAD,
    access_tag: str = "internal",
    tenant: str = "synapse",
) -> CanonicalDocument:
    """Parse bytes into a `CanonicalDocument`. Raises `ParseError` on failure."""
    text, media_type = parse_document(filename, data)
    # Resumes need shaping before Graphiti sees them — name order and contact
    # lines otherwise become the loudest (and wrongest) entities in the graph.
    text = normalize_resume_text(text, filename)
    content_hash = hashlib.sha256(data).hexdigest()
    document_id = f"doc-{_slug(filename)}-{uuid.uuid4().hex[:8]}"

    return CanonicalDocument(
        document_id=document_id,
        source_id=source_id,
        source_kind=source_kind,
        name=filename,
        media_type=media_type,
        text=text,
        tenant=tenant,
        content_hash=content_hash,
        ingested_at=datetime.now(UTC),
        byte_size=len(data),
        access_tag=access_tag,
        metadata={
            "domain": "knowledge",
            "category": "document",
            "type": "document",
            "document_id": document_id,
            "filename": filename,
            "document_type": filename.split('.')[-1].lower() if '.' in filename else "unknown",
            "department": "general",
            "version": "1.0",
            "language": "en"
        }
    )
