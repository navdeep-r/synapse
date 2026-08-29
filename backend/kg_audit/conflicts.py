"""Contradiction, cardinality-conflict and ACL audits for the Curation screen."""

from __future__ import annotations

from collections import defaultdict


def find_contradictions(edges: list[dict]) -> list[dict]:
    """Facts that were superseded — the visible output of the temporal model."""
    results = []
    for edge in edges:
        if not edge.get("invalid_at"):
            continue
        results.append(
            {
                "id": edge.get("uuid"),
                "subject": edge.get("source_name"),
                "relation_type": edge.get("relation_type"),
                "object": edge.get("target_name"),
                "fact": edge.get("fact"),
                "valid_at": _text(edge.get("valid_at")),
                "invalid_at": _text(edge.get("invalid_at")),
                "status": "superseded",
            }
        )
    return results


def find_conflicts(edges: list[dict], relation_types: list[dict]) -> list[dict]:
    """Active edges that violate a relation's `max_active_outgoing` constraint.

    This is the check that gives `max_active_outgoing` teeth: without it the
    constraint is documentation rather than a rule.
    """
    limits = {
        relation["relation_type"]: int(relation.get("max_active_outgoing") or 1)
        for relation in relation_types
    }

    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for edge in edges:
        if edge.get("invalid_at") or edge.get("expired_at"):
            continue
        key = (
            edge.get("source_uuid") or "",
            edge.get("relation_type") or "",
        )
        grouped[key].append(edge)

    conflicts = []
    for (_source_uuid, relation_type), bucket in grouped.items():
        limit = limits.get(relation_type)
        if limit is None or len(bucket) <= limit:
            continue
        conflicts.append(
            {
                "id": f"conf-{bucket[0].get('uuid')}",
                "subject": bucket[0].get("source_name"),
                "relation_type": relation_type,
                "active_count": len(bucket),
                "max_active_outgoing": limit,
                "targets": [edge.get("target_name") for edge in bucket],
                "detail": (
                    f"{bucket[0].get('source_name')} has {len(bucket)} active "
                    f"{relation_type} edges but the ontology allows {limit}."
                ),
            }
        )
    return conflicts


def find_acl_issues(edges: list[dict], provenance_tags: dict[str, str]) -> list[dict]:
    """Facts whose supporting chunks disagree on access tag.

    A fact derived from both `public` and `restricted` sources cannot be exported
    under a single tag without a decision, so it is surfaced rather than guessed.
    """
    issues = []
    for edge in edges:
        episodes = edge.get("episodes") or []
        if isinstance(episodes, str):
            episodes = [episodes]
        tags = {provenance_tags.get(ep) for ep in episodes if provenance_tags.get(ep)}
        if len(tags) > 1:
            issues.append(
                {
                    "id": f"acl-{edge.get('uuid')}",
                    "fact": edge.get("fact"),
                    "subject": edge.get("source_name"),
                    "object": edge.get("target_name"),
                    "tags": sorted(t for t in tags if t),
                    "detail": (
                        "Supporting sources carry conflicting access tags: "
                        f"{', '.join(sorted(t for t in tags if t))}."
                    ),
                }
            )
    return issues


def _text(value: object) -> str | None:
    return None if value is None else str(value)
