"""Cross-encoder stub.

Graphiti's default reranker is an OpenAI call, so a key-free deployment needs a
replacement. Scoring uses the same normalization as the resolution core, so
ranking agrees with merge decisions instead of contradicting them.
"""

from __future__ import annotations

from graphiti_core.cross_encoder.client import CrossEncoderClient

from kg_graphiti.embedder import cosine, embed
from kg_graphiti.resolution import canonical_tokens


class LexicalCrossEncoder(CrossEncoderClient):
    """Ranks passages by canonical-token overlap blended with vector similarity."""

    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        if not passages:
            return []

        query_tokens = set(canonical_tokens(query))
        query_vector = embed(query)

        scored: list[tuple[str, float]] = []
        for passage in passages:
            passage_tokens = set(canonical_tokens(passage))
            overlap = (
                len(query_tokens & passage_tokens) / len(query_tokens)
                if query_tokens
                else 0.0
            )
            vector_score = max(0.0, cosine(query_vector, embed(passage)))
            scored.append((passage, round(0.6 * overlap + 0.4 * vector_score, 6)))

        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored
