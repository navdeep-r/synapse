"""End-to-end behaviour of the Graphiti layer with the offline stub."""

from __future__ import annotations

import pytest

from kg_graphiti.service import GraphService
from tests.conftest import episode


@pytest.mark.asyncio
async def test_episode_creates_entities_and_edges(graph: GraphService) -> None:
    group = "e2e-basic"
    result = await graph.add_episode(
        episode(
            "Alice Johnson is the engineering manager for the Platform team. "
            "Alice Johnson reports to Robert Smith. "
            "The billing-service depends on auth-library. "
            "The billing-service uses PostgreSQL.",
            group_id=group,
            name="handbook",
            days_ago=2,
        )
    )

    assert result.error is None
    assert result.nodes_created > 0
    assert result.edges_created > 0

    names = {row["name"] for row in await graph.entities([group])}
    assert {"Alice Johnson", "Robert Smith", "billing-service", "auth-library"} <= names

    edges = await graph.edges([group])
    triples = {(e["source_name"], e["relation_type"], e["target_name"]) for e in edges}
    assert ("Alice Johnson", "REPORTS_TO", "Robert Smith") in triples
    assert ("billing-service", "DEPENDS_ON", "auth-library") in triples


@pytest.mark.asyncio
async def test_contradiction_invalidates_the_old_edge_non_destructively(
    graph: GraphService,
) -> None:
    """The strongest reason to adopt Graphiti: facts are superseded, not overwritten."""
    group = "e2e-temporal"

    await graph.add_episode(
        episode("Alice Johnson reports to Robert Smith.", group_id=group, name="old", days_ago=2)
    )
    result = await graph.add_episode(
        episode("Alice Johnson now reports to Carol Danvers.", group_id=group, name="new")
    )

    assert result.invalidated_edges >= 1, "the superseded reporting line was not invalidated"

    edges = await graph.edges([group])
    by_target = {e["target_name"]: e for e in edges if e["relation_type"] == "REPORTS_TO"}

    assert "Robert Smith" in by_target, "the old fact must still exist (non-destructive)"
    assert by_target["Robert Smith"]["invalid_at"] is not None, "old fact should be closed"
    assert by_target["Carol Danvers"]["invalid_at"] is None, "new fact should be open"


@pytest.mark.asyncio
async def test_alias_resolves_onto_the_existing_entity(graph: GraphService) -> None:
    """R1: `Bob Smith` must land on the existing `Robert Smith`, not fork a new node."""
    group = "e2e-alias"

    await graph.add_episode(
        episode("Robert Smith manages the billing-service.", group_id=group, name="first")
    )
    await graph.add_episode(
        episode("Bob Smith uses PostgreSQL for reporting.", group_id=group, name="second")
    )

    names = [row["name"] for row in await graph.entities([group])]
    people = [n for n in names if "smith" in (n or "").casefold()]

    assert len(people) == 1, f"alias forked into separate nodes: {people}"
    assert people[0] == "Robert Smith"


@pytest.mark.asyncio
async def test_entities_get_their_ontology_type(graph: GraphService) -> None:
    """Typing drives the edge map and every type-filtered read, so assert the labels.

    Technologies are the failure case worth pinning: `PostgreSQL` has the shape of
    a company name and was typed `Organization` until the classifier learned to
    look for internal capitalisation.
    """
    group = "e2e-typing"
    await graph.add_episode(
        episode(
            "The billing-service uses PostgreSQL. "
            "Alice Johnson manages the billing-service. "
            "Tech Corp Inc uses MongoDB.",
            group_id=group,
            name="typed",
        )
    )

    types = {
        row["name"]: {label for label in (row["labels"] or []) if label != "Entity"}
        for row in await graph.entities([group])
    }

    assert types.get("PostgreSQL") == {"Product"}, "a database is not an Organization"
    assert types.get("MongoDB") == {"Product"}
    assert types.get("Alice Johnson") == {"Employee"}
    assert types.get("Tech Corp Inc") == {"Organization"}
    assert types.get("billing-service") == {"Repository"}


@pytest.mark.asyncio
async def test_edge_type_map_is_enforced(graph: GraphService) -> None:
    """The closed vocabulary must constrain type *pairs*, not just relation names.

    Graphiti forwards edge_type_map to the prompt but does not enforce it, so this
    is our gate. `Alice depends on Robert` is a well-formed DEPENDS_ON between two
    Employees, which the ontology does not permit, and must not reach the graph.
    """
    group = "e2e-edge-types"
    before = graph.counters.get("edge_type_rejected", 0)

    await graph.add_episode(
        episode(
            "The billing-service uses PostgreSQL. "
            "Alice Johnson manages the billing-service. "
            "Alice Johnson reports to Robert Smith. "
            "Alice Johnson depends on Robert Smith.",
            group_id=group,
            name="typed-edges",
        )
    )

    triples = {
        (e["source_name"], e["relation_type"], e["target_name"])
        for e in await graph.edges([group])
    }

    assert ("billing-service", "USES", "PostgreSQL") in triples
    assert ("Alice Johnson", "REPORTS_TO", "Robert Smith") in triples
    # MANAGED_BY reads "<source> is managed by <target>"; the map must agree with
    # the extractor's direction or every one of these edges is silently dropped.
    assert ("billing-service", "MANAGED_BY", "Alice Johnson") in triples

    assert ("Alice Johnson", "DEPENDS_ON", "Robert Smith") not in triples
    assert graph.counters.get("edge_type_rejected", 0) > before, "rejection was not counted"


@pytest.mark.asyncio
async def test_groups_are_isolated(graph: GraphService) -> None:
    """v2 doc 3.6: entities must not bleed across group_id boundaries."""
    await graph.add_episode(
        episode("Zeta Corp uses PostgreSQL.", group_id="iso-a", name="a")
    )
    await graph.add_episode(
        episode("Omega Corp uses PostgreSQL.", group_id="iso-b", name="b")
    )

    names_a = {row["name"] for row in await graph.entities(["iso-a"])}
    names_b = {row["name"] for row in await graph.entities(["iso-b"])}

    assert "Zeta Corp" in names_a and "Zeta Corp" not in names_b
    assert "Omega Corp" in names_b and "Omega Corp" not in names_a
