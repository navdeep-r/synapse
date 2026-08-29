"""Full-stack behaviour: upload a document and read the graph back through the API."""

from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app

API = "/api/v1"

HANDBOOK = b"""Engineering Handbook

Page 1 of 12

Alice Johnson is the engineering manager for the Platform team.
Alice Johnson reports to Robert Smith.

The billing-service depends on auth-library.
The billing-service uses PostgreSQL.

Copyright 2024 Acme Corp
"""

REORG = b"""Reorganisation Notice

Alice Johnson now reports to Carol Danvers.
"""


@pytest_asyncio.fixture(scope="module")
async def app_client() -> AsyncIterator[tuple[AsyncClient, object]]:
    settings = Settings(
        data_dir=Path(tempfile.mkdtemp(prefix="synapse-e2e-")),
        strict_prompts=True,
        llm_provider="stub",
    )
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        async with app.router.lifespan_context(app):
            state = app.state.synapse
            await state.graph.ensure_ready()
            
            # Wipe Neo4j to ensure test isolation matches the fresh SQLite db
            if state.settings.graph_backend == "neo4j":
                await state.graph.query("MATCH (n) DETACH DELETE n", group_id="synapse", read_only=False)
                
            yield http, state


@pytest.mark.asyncio
async def test_upload_flows_into_the_graph(app_client) -> None:
    client, state = app_client

    response = await client.post(
        f"{API}/uploads", files={"file": ("handbook.txt", HANDBOOK, "text/plain")}
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "queued"

    print("GRAPH BACKEND:", state.settings.graph_backend)
    print("GRAPH DB PATH:", state.settings.graph_db_path)

    await state.pipeline.drain()
    
    for table in ["episode_receipts", "event_receipts", "dead_letters"]:
        rows = await state.db.fetch_all(f"SELECT * FROM {table}")
        for r in rows:
            print(f"{table} ROW:", dict(r))

    topology = (await client.get(f"{API}/graph/topology")).json()
    assert topology["nodes"], "topology should not be empty after an upload"
    assert topology["links"], "facts should have produced links"

    names = {node["name"] for node in topology["nodes"]}
    assert {"Alice Johnson", "Robert Smith", "billing-service"} <= names


@pytest.mark.asyncio
async def test_facts_carry_real_provenance(app_client) -> None:
    """The four fields ViewerScreen used to fabricate are now served for real."""
    client, _state = app_client

    topology = (await client.get(f"{API}/graph/topology")).json()
    alice = next(n for n in topology["nodes"] if n["name"] == "Alice Johnson")

    facts = (await client.get(f"{API}/entities/{alice['id']}")).json()["facts"]
    assert facts, "Alice should have at least one outgoing fact"

    fact = facts[0]
    print("FACT RETURNED:", fact)
    assert fact["chunk_ids"], "a fact must trace back to the chunk it came from"
    assert fact["source_document_name"] == "handbook.txt"
    assert fact["access_tag"] == "internal"
    assert fact["mutation_id"] != "unknown"
    assert fact["target_name"], "target_name spares the UI from rendering a raw uuid"


@pytest.mark.asyncio
async def test_prefilter_dropped_boilerplate(app_client) -> None:
    """R7: drops are recorded with the rule that fired, not discarded."""
    _client, state = app_client

    drops = await state.db.fetch_all("SELECT * FROM prefilter_drops")
    rules = {row["rule"] for row in drops}
    assert "page_marker" in rules
    assert "copyright_notice" in rules
    for row in drops:
        assert row["dropped_text"], "the dropped text must be retained for threshold review"


@pytest.mark.asyncio
async def test_contradicting_upload_supersedes_the_old_fact(app_client) -> None:
    client, state = app_client

    response = await client.post(
        f"{API}/uploads", files={"file": ("reorg.txt", REORG, "text/plain")}
    )
    assert response.status_code == 200
    await state.pipeline.drain()

    contradictions = (await client.get(f"{API}/curation/contradictions")).json()
    assert contradictions, "the superseded reporting line should surface as a contradiction"

    superseded = contradictions[0]
    assert superseded["relation_type"] == "REPORTS_TO"
    assert superseded["invalid_at"] is not None

    # The topology hides superseded facts, so Alice now reports only to Carol.
    topology = (await client.get(f"{API}/graph/topology")).json()
    names = {node["id"]: node["name"] for node in topology["nodes"]}
    reports_to = [
        (names.get(link["source"]), names.get(link["target"]))
        for link in topology["links"]
        if link.get("relation_type") == "REPORTS_TO"
    ]
    assert ("Alice Johnson", "Carol Danvers") in reports_to
    assert ("Alice Johnson", "Robert Smith") not in reports_to


@pytest.mark.asyncio
async def test_replay_is_filtered_as_duplicate(app_client) -> None:
    """Re-ingesting identical content must not duplicate the graph."""
    client, state = app_client

    before = (await client.get(f"{API}/graph/topology")).json()
    counters_before = await state.db.counters()

    response = await client.post(f"{API}/sources/upload/replay")
    assert response.status_code == 200
    await state.pipeline.drain()

    after = (await client.get(f"{API}/graph/topology")).json()
    counters_after = await state.db.counters()

    assert len(after["nodes"]) == len(before["nodes"]), "replay must not fork new entities"
    assert counters_after["prefilter_dropped"] > counters_before["prefilter_dropped"]


@pytest.mark.asyncio
async def test_quality_metrics_report_zero_fallbacks(app_client) -> None:
    """R2: the headline safety number, served to the console."""
    client, _state = app_client
    metrics = (await client.get(f"{API}/observability/quality-metrics")).json()

    assert metrics["stub_fallback_rate"] == "0.0%"
    assert metrics["llm_calls"] > 0, "the stub should actually have been exercised"
    assert metrics["episodes_avoided"] > 0, "the pre-ingest filter should have saved calls"


@pytest.mark.asyncio
async def test_pipeline_counters_advanced(app_client) -> None:
    client, _state = app_client
    pipeline = (await client.get(f"{API}/observability/pipeline")).json()
    by_name = {stage["name"]: stage for stage in pipeline["stages"]}

    assert by_name["Ingestion"]["processed_count"] > 0
    assert by_name["Chunking"]["processed_count"] > 0
    assert by_name["Extraction"]["processed_count"] > 0
    assert pipeline["active_stage"] == "Idle", "the funnel should be idle once the queue drains"


@pytest.mark.asyncio
async def test_snapshot_generation_and_download(app_client) -> None:
    client, _state = app_client

    response = await client.post(
        f"{API}/snapshot/generate",
        json={
            "factScope": "current",
            "reportScope": "flagged",
            "accessControlHandling": "tag_downstream",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["passed"] is True, body["validation_checks"]
    assert len(body["validation_checks"]) == 4
    assert {c["name"] for c in body["validation_checks"]} == {
        "No orphan edges",
        "Ontology versions resolvable",
        "ACL policy actually applied",
        "Payload size within threshold",
    }
    assert body["edge_count"] > 0

    listing = (await client.get(f"{API}/snapshots")).json()
    assert listing and listing[0]["version"] == body["version"]

    download = await client.get(f"{API}/snapshots/{body['version']}/download")
    assert download.status_code == 200
    assert download.content[:2] == b"\x1f\x8b", "snapshot should be gzip"


@pytest.mark.asyncio
async def test_strict_acl_policy_removes_internal_facts(app_client) -> None:
    """The ACL policy must observably change what leaves the platform."""
    client, _state = app_client

    response = await client.post(
        f"{API}/snapshot/generate",
        json={
            "factScope": "current",
            "reportScope": "none",
            "accessControlHandling": "filter_group",
        },
    )
    assert response.status_code == 200
    body = response.json()

    # Uploads default to 'internal', which the strict policy excludes, so the
    # snapshot is empty and correctly fails the non-empty check (Payload size > 0).
    assert body["removed_edges"] > 0
    assert body["passed"] is False
    failed = [c for c in body["validation_checks"] if not c["passed"]]
    assert [c["name"] for c in failed] == ["Payload size within threshold"]


@pytest.mark.asyncio
async def test_simulate_query_returns_provenance(app_client) -> None:
    client, _state = app_client

    response = await client.post(
        f"{API}/query/simulate",
        json={"query": "Who does Alice Johnson report to?", "access_level": "internal"},
    )
    assert response.status_code == 200
    body = response.json()

    assert body["fact_count"] > 0
    top = body["retrieved_facts"][0]
    assert top["provenance"], "every retrieved fact must be traceable"
    assert top["provenance"][0]["source_document_name"]


@pytest.mark.asyncio
async def test_simulate_query_withholds_by_access_level(app_client) -> None:
    """A public-level agent must not see internal facts."""
    client, _state = app_client

    response = await client.post(
        f"{API}/query/simulate",
        json={"query": "Who does Alice Johnson report to?", "access_level": "public"},
    )
    body = response.json()

    assert body["withheld_count"] > 0
    assert body["fact_count"] == 0
    assert "internal" in body["withheld_facts"][0]["reason"]


@pytest.mark.asyncio
async def test_reports_are_generated(app_client) -> None:
    client, _state = app_client
    reports = (await client.get(f"{API}/reports")).json()
    assert reports, "a connected graph should yield at least one community report"
    assert reports[0]["entity_count"] >= 2
