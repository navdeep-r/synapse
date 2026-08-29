"""Read-side helpers shared by the routers.

Joins the graph (entities, facts) to SQLite (provenance, receipts, documents),
which is the bridge that makes every fact traceable to the chunk it came from.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.state import AppState


@dataclass
class ProvenanceIndex:
    """Maps a graph episode uuid back to the chunks and document it came from."""

    episode_to_chunks: dict[str, list[str]] = field(default_factory=dict)
    episode_to_document: dict[str, str] = field(default_factory=dict)
    document_names: dict[str, str] = field(default_factory=dict)
    document_tags: dict[str, str] = field(default_factory=dict)
    episode_to_receipt: dict[str, str] = field(default_factory=dict)

    def chunks_for(self, episodes: list[str]) -> list[str]:
        out: list[str] = []
        for episode in episodes:
            out.extend(self.episode_to_chunks.get(episode, []))
        return out

    def documents_for(self, episodes: list[str]) -> list[str]:
        return [
            self.episode_to_document[e] for e in episodes if e in self.episode_to_document
        ]

    def document_name(self, episodes: list[str]) -> str:
        for document_id in self.documents_for(episodes):
            name = self.document_names.get(document_id)
            if name:
                return name
        return "Unknown Document"

    def tags_for(self, episodes: list[str]) -> list[str]:
        return [
            self.document_tags[d] for d in self.documents_for(episodes) if d in self.document_tags
        ]

    def access_tag(self, episodes: list[str]) -> str:
        from kg_export.policies import effective_tag

        tags = self.tags_for(episodes)
        return effective_tag(tags) if tags else "internal"

    def mutation_id(self, episodes: list[str]) -> str:
        for episode in episodes:
            receipt = self.episode_to_receipt.get(episode)
            if receipt:
                return receipt
        return "unknown"

    def records_for(self, episodes: list[str]) -> list[dict[str, Any]]:
        records = []
        for episode in episodes:
            document_id = self.episode_to_document.get(episode)
            records.append(
                {
                    "episode_uuid": episode,
                    "mutation_id": self.episode_to_receipt.get(episode, "unknown"),
                    "chunk_ids": self.episode_to_chunks.get(episode, []),
                    "document_id": document_id,
                    "source_document_name": self.document_names.get(
                        document_id or "", "Unknown Document"
                    ),
                    "access_tag": self.document_tags.get(document_id or "", "internal"),
                }
            )
        return records


async def build_provenance_index(state: "AppState") -> ProvenanceIndex:
    index = ProvenanceIndex()

    receipts = await state.db.fetch_all(
        "SELECT episode_id, document_id, graph_uuid, chunk_ids_json FROM episode_receipts "
        "WHERE graph_uuid IS NOT NULL"
    )
    for row in receipts:
        uuid = row["graph_uuid"]
        index.episode_to_chunks[uuid] = json.loads(row["chunk_ids_json"] or "[]")
        index.episode_to_document[uuid] = row["document_id"]
        index.episode_to_receipt[uuid] = row["episode_id"]

    documents = await state.db.fetch_all("SELECT document_id, name, access_tag FROM documents")
    for row in documents:
        index.document_names[row["document_id"]] = row["name"]
        index.document_tags[row["document_id"]] = row["access_tag"]

    return index


def episodes_of(edge: dict) -> list[str]:
    """`episodes` comes back as a list or a bare string depending on the driver."""
    episodes = edge.get("episodes") or []
    if isinstance(episodes, str):
        return [episodes]
    return [str(e) for e in episodes]


async def read_graph(state: AppState, graph: "AuthorizedGraphRepository") -> tuple[list[dict], list[dict]]:
    """Entities and edges across every known group."""
    if not graph.is_ready:
        return [], []
    groups = await state.group_ids()
    try:
        entities = await graph.entities(groups)
        edges = await graph.edges(groups)
    except Exception:
        return [], []
    return entities, edges


def edge_tag_map(edges: list[dict], index: ProvenanceIndex) -> dict[str, list[str]]:
    return {
        (edge.get("uuid") or ""): index.tags_for(episodes_of(edge)) for edge in edges
    }
