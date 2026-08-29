"""Merge-candidate detection over entities the resolver already left distinct."""

from __future__ import annotations

from kg_audit.candidates import find_merge_candidates


def entity(uuid: str, name: str, group_id: str = "tenant-a", label: str = "Organization") -> dict:
    return {"uuid": uuid, "name": name, "group_id": group_id, "labels": ["Entity", label]}


def test_surviving_alias_pair_is_the_top_candidate() -> None:
    """The resolver kept both, but the alias rules say they are one company.

    This is the case the queue exists for. Skipping pairs that `compare`
    matches would empty the queue precisely when it is most useful.
    """
    candidates = find_merge_candidates(
        [entity("u1", "Microsoft"), entity("u2", "MSFT")]
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert {candidate.primary_name, candidate.secondary_name} == {"Microsoft", "MSFT"}
    assert candidate.confidence >= 0.9
    assert "missed merge" in candidate.reason


def test_longer_name_becomes_the_survivor() -> None:
    candidates = find_merge_candidates(
        [entity("u1", "TechCorp"), entity("u2", "Tech Corp Inc")]
    )

    assert candidates[0].primary_name == "Tech Corp Inc"
    assert candidates[0].secondary_name == "TechCorp"


def test_distinct_entities_are_not_proposed() -> None:
    """The near-miss pairs the resolution fixtures cover must stay separate."""
    candidates = find_merge_candidates(
        [
            entity("u1", "billing-service", label="Repository"),
            entity("u2", "reporting-service", label="Repository"),
            entity("u3", "Acme Corp"),
            entity("u4", "PostgreSQL", label="Product"),
        ]
    )

    pairs = {(c.primary_name, c.secondary_name) for c in candidates}
    assert ("reporting-service", "billing-service") not in pairs
    assert ("billing-service", "reporting-service") not in pairs


def test_candidates_never_cross_group() -> None:
    """Graphiti never resolves across groups, so proposing it would be wrong."""
    candidates = find_merge_candidates(
        [
            entity("u1", "Microsoft", group_id="tenant-a"),
            entity("u2", "MSFT", group_id="tenant-b"),
        ]
    )

    assert candidates == []


def test_ordering_is_by_confidence() -> None:
    candidates = find_merge_candidates(
        [
            entity("u1", "Microsoft"),
            entity("u2", "MSFT"),
            entity("u3", "TechCorp"),
            entity("u4", "Tech Corp Inc"),
        ]
    )

    scores = [c.confidence for c in candidates]
    assert scores == sorted(scores, reverse=True)


def test_entity_type_is_carried_through() -> None:
    candidates = find_merge_candidates(
        [entity("u1", "Microsoft"), entity("u2", "MSFT")]
    )
    assert candidates[0].entity_type == "Organization"
