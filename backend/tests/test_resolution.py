"""R1: the alias-matching core is gated on measured precision and recall."""

from __future__ import annotations

import pytest

from kg_graphiti.resolution import (
    canonical_key,
    compare,
    jaro_winkler,
    normalize,
    similarity,
)
from tests.fixtures.resolution_pairs import NEGATIVES, POSITIVES


@pytest.mark.parametrize(("left", "right", "why"), POSITIVES)
def test_aliases_merge(left: str, right: str, why: str) -> None:
    verdict = compare(left, right)
    assert verdict.is_match, f"expected merge ({why}): {left!r} vs {right!r} -> {verdict}"


@pytest.mark.parametrize(("left", "right", "why"), NEGATIVES)
def test_near_misses_stay_distinct(left: str, right: str, why: str) -> None:
    verdict = compare(left, right)
    assert not verdict.is_match, f"expected distinct ({why}): {left!r} vs {right!r} -> {verdict}"


def test_precision_and_recall_thresholds() -> None:
    """The headline numbers, asserted so a regression fails CI."""
    true_positives = sum(1 for left, right, _ in POSITIVES if compare(left, right).is_match)
    false_negatives = len(POSITIVES) - true_positives
    false_positives = sum(1 for left, right, _ in NEGATIVES if compare(left, right).is_match)

    recall = true_positives / (true_positives + false_negatives)
    precision = true_positives / (true_positives + false_positives)

    assert recall >= 0.95, f"recall regressed to {recall:.2%}"
    assert precision >= 0.95, f"precision regressed to {precision:.2%}"


def test_jaro_winkler_alone_would_have_failed() -> None:
    """Documents why similarity is not the merge trigger.

    `Tech Corp` / `Tech Corps Ltd` scores high enough that any Jaro-Winkler
    threshold admitting real aliases would also merge these two.
    """
    trap = jaro_winkler("techcorp", "techcorps")
    assert trap > 0.95, f"expected the trap pair to score high, got {trap}"
    assert not compare("Tech Corp", "Tech Corps Ltd").is_match


def test_normalization_basics() -> None:
    from kg_graphiti.resolution import canonical_keys

    assert normalize("Tech Corp, Inc.") == "tech corp inc"
    assert canonical_keys("Tech Corp Inc.") & canonical_keys("TechCorp")
    assert canonical_key("Bob Smith") == canonical_key("Robert Smith")
    assert not canonical_keys("Tech Corps Ltd") & canonical_keys("Tech Corp")


def test_similarity_is_bounded_and_ordered() -> None:
    assert similarity("Acme", "Acme") == 1.0
    assert 0.0 <= similarity("Acme Systems", "Acme Solutions") <= 1.0
    assert similarity("Bob Smith", "Robert Smith") > similarity("Bob Smith", "Jane Doe")
