"""Simulate Agent Query.

Shows what an agent would receive for a question: the retrieved facts, their
provenance, and what was withheld by access control. The point of the panel is
auditing retrieval and ACL behaviour, not answering the question.
"""

from __future__ import annotations

from typing import Any

from kg_export.policies import TAG_RANK, effective_tag
from kg_graphiti.resolution import canonical_tokens, jaro_winkler

_ACCESS_CEILING = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}


def _score(query: str, edge: dict) -> float:
    """Lexical relevance of a fact to the query."""
    query_tokens = set(canonical_tokens(query))
    if not query_tokens:
        return 0.0

    haystack = " ".join(
        str(edge.get(key) or "")
        for key in ("source_name", "relation_type", "target_name", "fact")
    )
    fact_tokens = set(canonical_tokens(haystack))
    if not fact_tokens:
        return 0.0

    overlap = len(query_tokens & fact_tokens) / len(query_tokens)

    # Reward near-matches so "billing service" still finds "billing-service".
    fuzzy = 0.0
    for token in query_tokens:
        best = max((jaro_winkler(token, other) for other in fact_tokens), default=0.0)
        fuzzy += best
    fuzzy /= len(query_tokens)

    return round(0.7 * overlap + 0.3 * fuzzy, 4)


def simulate_query(
    query: str,
    edges: list[dict],
    *,
    edge_tags: dict[str, list[str]],
    provenance: dict[str, list[dict]],
    access_level: str = "internal",
    limit: int = 10,
    include_invalid: bool = False,
) -> dict[str, Any]:
    """Retrieve facts for a query and report what access control withheld."""
    ceiling = _ACCESS_CEILING.get(access_level, 1)

    scored: list[tuple[float, dict]] = []
    for edge in edges:
        if not include_invalid and (edge.get("invalid_at") or edge.get("expired_at")):
            continue
        score = _score(query, edge)
        if score > 0.1:
            scored.append((score, edge))

    scored.sort(key=lambda pair: pair[0], reverse=True)

    retrieved: list[dict] = []
    withheld: list[dict] = []

    for score, edge in scored:
        uuid = edge.get("uuid") or ""
        tag = effective_tag(edge_tags.get(uuid, []))

        if TAG_RANK.get(tag, TAG_RANK["restricted"]) > ceiling:
            withheld.append(
                {
                    "fact_id": uuid,
                    "access_tag": tag,
                    "reason": (
                        f"Requires '{tag}' access; the simulated agent holds '{access_level}'."
                    ),
                }
            )
            continue

        if len(retrieved) >= limit:
            continue

        sources = provenance.get(uuid, [])
        retrieved.append(
            {
                "fact_id": uuid,
                "fact": edge.get("fact"),
                "subject": edge.get("source_name"),
                "relation_type": edge.get("relation_type"),
                "object": edge.get("target_name"),
                "relevance": score,
                "access_tag": tag,
                "valid_at": _text(edge.get("valid_at")),
                "invalid_at": _text(edge.get("invalid_at")),
                "provenance": sources,
            }
        )

    context = "\n".join(
        f"- {item['fact']} (source: "
        f"{', '.join(p.get('source_document_name', 'unknown') for p in item['provenance']) or 'unknown'})"
        for item in retrieved
    )

    return {
        "query": query,
        "access_level": access_level,
        "retrieved_facts": retrieved,
        "withheld_facts": withheld,
        "context": context,
        "fact_count": len(retrieved),
        "withheld_count": len(withheld),
    }


def _text(value: object) -> str | None:
    return None if value is None else str(value)
