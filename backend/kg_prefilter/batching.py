"""Episode batching.

Graphiti recommends episode granularity around a coherent unit of meaning rather
than one call per sentence. Grouping adjacent small chunks into a single episode
is the difference between one LLM round trip and ten.
"""

from __future__ import annotations

import uuid

from kg_contracts import Chunk, EpisodePayload, PrefilterDecision, PrefilterOutcome
from kg_parsing.resume import extract_resume_subject, strip_resume_markers


def resolve_group_id(tenant: str, source: str, scheme: str) -> str:
    """Namespacing scheme from v2 doc 3.6.

    Graphiti resolves entities *within* a group_id, so this is the setting that
    decides between cross-department entity bleed and a fragmented view.
    """
    if scheme == "flat":
        return "enterprise"
    if scheme == "tenant_source":
        import re
        safe_source = re.sub(r'[^a-zA-Z0-9_\-]', '-', source)
        return f"{tenant}--{safe_source}"
    return tenant


def _resume_subject(chunks: list[Chunk]) -> str | None:
    for chunk in chunks:
        subject = extract_resume_subject(chunk.text)
        if subject:
            return subject
    return None


def build_episodes(
    chunks: list[Chunk],
    *,
    target_chars: int = 1200,
) -> list[EpisodePayload]:
    """Group consecutive chunks from the same document into episodes."""
    episodes: list[EpisodePayload] = []
    batch: list[Chunk] = []
    # Subject is document-scoped: the marker only appears in early chunks, but
    # later experience/project chunks still need it to emit BELONGS_TO / USES.
    document_subject = _resume_subject(chunks)

    def flush() -> None:
        if not batch:
            return
        first = batch[0]
        provenance = first.provenance
        body = "\n\n".join(strip_resume_markers(c.text) for c in batch)
        if document_subject and document_subject.lower() not in body.lower():
            body = (
                f"This section is from the resume of {document_subject} (Employee). "
                f"Relate employers with BELONGS_TO, projects with MANAGED_BY "
                f"(project→{document_subject}), and technologies with USES.\n\n"
                f"{body}"
            )
        name = (
            first.chunk_id
            if len(batch) == 1
            else f"{first.chunk_id}+{len(batch) - 1}"
        )
        source_desc = f"{provenance.source}/{provenance.source_document_name}"
        if document_subject:
            source_desc = f"resume of {document_subject}; {source_desc}"
        episodes.append(
            EpisodePayload(
                episode_id=f"ep-{uuid.uuid4().hex[:12]}",
                name=name,
                body=body,
                source_description=source_desc,
                reference_time=provenance.timestamp,
                document_id=first.document_id,
                source_id=provenance.source,
                source_type=provenance.source_type,
                chunk_ids=[c.chunk_id for c in batch],
            )
        )
        batch.clear()

    for chunk in chunks:
        crosses_document = batch and batch[0].document_id != chunk.document_id
        would_overflow = batch and sum(len(c.text) for c in batch) + len(chunk.text) > target_chars
        if crosses_document or would_overflow:
            flush()
        batch.append(chunk)

    flush()
    return episodes


def prune_short_episodes(
    episodes: list[EpisodePayload], *, min_chars: int
) -> tuple[list[EpisodePayload], list[PrefilterDecision]]:
    """Drop assembled episodes too small to be worth an LLM round trip.

    Applied after batching so a standalone heading survives by merging with its
    body; only genuinely isolated fragments are dropped.
    """
    kept: list[EpisodePayload] = []
    dropped: list[PrefilterDecision] = []

    for episode in episodes:
        if len(episode.body.strip()) >= min_chars:
            kept.append(episode)
            continue
        dropped.extend(
            PrefilterDecision(
                chunk_id=chunk_id,
                outcome=PrefilterOutcome.TOO_SHORT,
                rule=f"episode_min_chars<{min_chars}",
                dropped_text=episode.body,
            )
            for chunk_id in episode.chunk_ids
        )

    return kept, dropped
