"""Paragraph-oriented chunking.

Rule-based and deterministic. Chunks are the unit that carries provenance
(`chunk_id`, `document_id`, `source`, `offset`, `timestamp`) all the way to a
graph fact, so the boundaries must be reproducible for the same input.
"""

from __future__ import annotations

import hashlib
import re

from kg_contracts import CanonicalDocument, Chunk, Provenance

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

TARGET_CHARS = 900
MAX_CHARS = 1600


def content_hash(text: str) -> str:
    return hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()


def _split_oversized(paragraph: str) -> list[str]:
    """Break a paragraph longer than MAX_CHARS on sentence boundaries."""
    if len(paragraph) <= MAX_CHARS:
        return [paragraph]

    parts: list[str] = []
    current = ""
    for sentence in _SENTENCE_SPLIT.split(paragraph):
        if current and len(current) + len(sentence) + 1 > TARGET_CHARS:
            parts.append(current.strip())
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current.strip():
        parts.append(current.strip())

    # A single sentence can still exceed the cap; fall back to a hard cut.
    final: list[str] = []
    for part in parts:
        while len(part) > MAX_CHARS:
            final.append(part[:MAX_CHARS])
            part = part[MAX_CHARS:]
        if part:
            final.append(part)
    return final


def chunk_document(document: CanonicalDocument) -> list[Chunk]:
    """Split a document into paragraph-level chunks with a provenance anchor.

    Deliberately does NOT merge adjacent paragraphs. Boilerplate is recognised
    per paragraph, so merging here would hide footers and page markers inside a
    legitimate chunk and defeat the pre-ingest filter. Regrouping into
    coherent-sized units happens after filtering, in `kg_prefilter.build_episodes`.
    """
    paragraphs: list[str] = []
    for block in _PARAGRAPH_SPLIT.split(document.text):
        block = block.strip()
        if block:
            paragraphs.extend(_split_oversized(block))

    chunks: list[Chunk] = []
    cursor = 0
    for ordinal, text in enumerate(paragraphs):
        offset = document.text.find(text[:60], cursor)
        if offset < 0:
            offset = cursor
        cursor = offset + len(text)
        chunk_id = f"{document.document_id}::c{ordinal:04d}"
        chunks.append(
            Chunk(
                chunk_id=chunk_id,
                document_id=document.document_id,
                ordinal=ordinal,
                text=text,
                offset=offset,
                char_length=len(text),
                content_hash=content_hash(text),
                provenance=Provenance(
                    chunk_id=chunk_id,
                    document_id=document.document_id,
                    source=document.source_id,
                    source_document_name=document.name,
                    source_type=document.source_kind.value,
                    offset=offset,
                    timestamp=document.ingested_at,
                    access_tag=document.access_tag,
                ),
            )
        )
    return chunks
