"""R5: contract snapshot tests for every endpoint the console consumes.

The generated TypeScript client in `console/src/api/` is dead code and there are
no runtime checks in the screens, so a backend shape change breaks a screen
silently with no build-time signal. These tests assert the exact key names,
casing and types, and each one cites the code that depends on it.
"""

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


@pytest_asyncio.fixture(scope="module")
async def client() -> AsyncIterator[AsyncClient]:
    settings = Settings(
        data_dir=Path(tempfile.mkdtemp(prefix="synapse-contract-")),
        strict_prompts=True,
        llm_provider="stub",
    )
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        # Trigger lifespan startup.
        async with app.router.lifespan_context(app):
            yield http


def assert_keys(payload: dict, expected: set[str], where: str) -> None:
    missing = expected - set(payload)
    assert not missing, f"{where}: response is missing {sorted(missing)}"


# --- Observability ---------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_contract(client: AsyncClient) -> None:
    """ObservabilityScreen.fetchPipeline, lines 78-95."""
    response = await client.get(f"{API}/observability/pipeline")
    assert response.status_code == 200
    body = response.json()

    assert_keys(body, {"active_stage", "stages"}, "/observability/pipeline")
    assert isinstance(body["active_stage"], str)

    names = [stage["name"] for stage in body["stages"]]
    assert names == [
        "Ingestion",
        "Chunking",
        "Extraction",
        "Resolution",
        "Consolidation",
        "Community Detection",
        "Export",
    ], "stage names must match the funnel ObservabilityScreen renders"

    for stage in body["stages"]:
        # `s.processed_count.toString()` is unguarded, so null would crash the screen.
        assert isinstance(stage["processed_count"], int)
        assert stage["processed_count"] is not None
        # `error_rate` is rendered verbatim, so it must arrive pre-formatted.
        assert isinstance(stage["error_rate"], str)
        assert stage["error_rate"].endswith("%")


@pytest.mark.asyncio
async def test_consistency_contract_is_camel_case(client: AsyncClient) -> None:
    """MismatchItem in ObservabilityScreen declares camelCase, unlike every other endpoint."""
    response = await client.get(f"{API}/observability/consistency")
    assert response.status_code == 200
    body = response.json()

    assert "mismatches" in body
    assert isinstance(body["mismatches"], list)
    for mismatch in body["mismatches"]:
        assert_keys(
            mismatch, {"id", "txId", "store", "durationMinutes"}, "/observability/consistency"
        )
        assert isinstance(mismatch["durationMinutes"], int)
        assert "tx_id" not in mismatch, "must be camelCase txId, not snake_case"


@pytest.mark.asyncio
async def test_quality_metrics_contract(client: AsyncClient) -> None:
    """ObservabilityScreen quality tab, lines 244-256."""
    response = await client.get(f"{API}/observability/quality-metrics")
    assert response.status_code == 200
    body = response.json()

    assert_keys(
        body,
        {
            "source_freshness",
            "cdc_skip_rate",
            "parse_failure_rate",
            "temporal_consistency",
            "active_edge_conflict_rate",
            "review_rate",
            # R2 and R7 additions.
            "stub_fallback_rate",
            "prefilter_drop_rate",
        },
        "/observability/quality-metrics",
    )

    # `((review_rate || 0) * 100).toFixed(1)` — a 0-1 fraction, unlike the rest.
    assert isinstance(body["review_rate"], (int, float))
    assert 0.0 <= body["review_rate"] <= 1.0

    for key in ("cdc_skip_rate", "parse_failure_rate", "temporal_consistency"):
        assert isinstance(body[key], str) and body[key].endswith("%")


@pytest.mark.asyncio
async def test_export_metrics_contract(client: AsyncClient) -> None:
    response = await client.get(f"{API}/observability/export-metrics")
    assert response.status_code == 200
    assert_keys(
        response.json(),
        {"last_generation", "last_downstream_fetch", "size_trend", "total_snapshots"},
        "/observability/export-metrics",
    )


# --- Graph -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_topology_always_has_both_keys(client: AsyncClient) -> None:
    """ViewerScreen drops the payload entirely unless nodes AND links are present."""
    response = await client.get(f"{API}/graph/topology")
    assert response.status_code == 200
    body = response.json()

    assert "nodes" in body and "links" in body
    assert isinstance(body["nodes"], list) and isinstance(body["links"], list)
    for node in body["nodes"]:
        assert_keys(node, {"id", "name", "val"}, "/graph/topology node")
        assert isinstance(node["val"], (int, float))
    for link in body["links"]:
        assert_keys(link, {"source", "target"}, "/graph/topology link")


@pytest.mark.asyncio
async def test_entity_facts_contract(client: AsyncClient) -> None:
    """ViewerScreen entity fetch, lines 70-95."""
    response = await client.get(f"{API}/entities/does-not-exist")
    assert response.status_code == 200
    body = response.json()
    assert "facts" in body and isinstance(body["facts"], list)


# --- Ontology --------------------------------------------------------------


@pytest.mark.asyncio
async def test_entity_types_contract(client: AsyncClient) -> None:
    response = await client.get(f"{API}/ontology/entity-types")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list) and body
    for item in body:
        assert_keys(item, {"id", "name", "description"}, "/ontology/entity-types")


@pytest.mark.asyncio
async def test_relation_types_contract(client: AsyncClient) -> None:
    """OntologyScreen maps these field-by-field, lines 65-79."""
    response = await client.get(f"{API}/ontology/relation-types")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list) and body

    for item in body:
        assert_keys(
            item,
            {
                "relation_type",
                "version",
                "source_types",
                "target_types",
                "max_active_outgoing",
                "temporal",
                "overlap_allowed",
            },
            "/ontology/relation-types",
        )
        # `.join(', ')` is called on both, so they must be arrays.
        assert isinstance(item["source_types"], list)
        assert isinstance(item["target_types"], list)
        assert isinstance(item["temporal"], bool)
        assert isinstance(item["overlap_allowed"], bool)
        # The screen renders `v${r.version}`, so a "v1" here would display "vv1".
        assert isinstance(item["version"], int)
        assert not str(item["version"]).startswith("v")


@pytest.mark.asyncio
async def test_migrations_and_change_proposal(client: AsyncClient) -> None:
    """OntologyScreen splits one array on `status === 'pending'`, lines 81-106."""
    response = await client.post(
        f"{API}/ontology/changes",
        json={
            "relation_type": "REPORTS_TO",
            "field": "max_active_outgoing",
            "old_value": None,
            "new_value": 2,
            "justification": "Matrix reporting is now supported.",
        },
    )
    assert response.status_code == 200

    listing = await client.get(f"{API}/ontology/migrations")
    assert listing.status_code == 200
    body = listing.json()
    assert isinstance(body, list) and body

    pending = [m for m in body if m["status"] == "pending"]
    assert pending, "a freshly proposed change must appear as pending"
    for item in body:
        assert_keys(
            item,
            {"id", "status", "field_to_change", "proposed_by", "new_value", "impact_report"},
            "/ontology/migrations",
        )


@pytest.mark.asyncio
async def test_change_proposal_accepts_unknown_relation(client: AsyncClient) -> None:
    """OntologyScreen posts a hardcoded 'FOUNDED_BY'; rejecting it would break the screen."""
    response = await client.post(
        f"{API}/ontology/changes",
        json={
            "relation_type": "FOUNDED_BY",
            "field": "temporal",
            "old_value": None,
            "new_value": True,
            "justification": "Test.",
        },
    )
    assert response.status_code == 200


# --- Sources ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_sources_contract(client: AsyncClient) -> None:
    """SourceItem in DataSourcesScreen, lines 8-15."""
    response = await client.get(f"{API}/sources")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list) and body

    for item in body:
        assert_keys(
            item,
            {"id", "type", "tenant", "last_cdc_run", "status", "doc_count"},
            "/sources",
        )
        # Badge only understands these three.
        assert item["status"] in {"confirmed", "signal", "alert"}
        # Rendered verbatim, so it must be pre-formatted rather than an ISO timestamp.
        assert isinstance(item["last_cdc_run"], str)
        assert isinstance(item["doc_count"], int)


@pytest.mark.asyncio
async def test_activity_contract(client: AsyncClient) -> None:
    """ActivityItem in DataSourcesScreen, lines 17-23."""
    for path in (f"{API}/activity", f"{API}/sources/upload/activity"):
        response = await client.get(path)
        assert response.status_code == 200
        body = response.json()
        assert isinstance(body, list)
        for item in body:
            assert_keys(
                item, {"id", "timestamp", "event", "source_id", "details"}, path
            )


@pytest.mark.asyncio
async def test_upload_rejects_unsupported_type(client: AsyncClient) -> None:
    """The client enforces nothing, so the advertised limits are enforced here."""
    response = await client.post(
        f"{API}/uploads", files={"file": ("payload.exe", b"MZ\x00binary", "application/exe")}
    )
    assert response.status_code == 400
    assert "Supported types" in response.json()["detail"]


@pytest.mark.asyncio
async def test_replay_unknown_source_is_404(client: AsyncClient) -> None:
    response = await client.post(f"{API}/sources/nope/replay")
    assert response.status_code == 404


# --- Batch B ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_badges_contract(client: AsyncClient) -> None:
    """AppLayout curationBadgeCount / snapshotBadgeCount, lines 36-37."""
    response = await client.get(f"{API}/summary/badges")
    assert response.status_code == 200
    body = response.json()
    assert_keys(body, {"curation", "snapshots"}, "/summary/badges")
    assert isinstance(body["curation"], int)
    assert isinstance(body["snapshots"], int)


@pytest.mark.asyncio
async def test_curation_endpoints_return_lists(client: AsyncClient) -> None:
    for path in (
        "/curation/queue",
        "/curation/quarantine",
        "/curation/contradictions",
        "/curation/conflicts",
        "/curation/acl",
        "/curation/calibration",
    ):
        response = await client.get(f"{API}{path}")
        assert response.status_code == 200, f"{path} returned {response.status_code}"
        assert isinstance(response.json(), list), f"{path} must return an array"


@pytest.mark.asyncio
async def test_snapshot_requires_confirmation_phrase(client: AsyncClient) -> None:
    """R5: the Zod `superRefine` check is re-validated server-side."""
    response = await client.post(
        f"{API}/snapshot/generate",
        json={
            "tenants": ["tenant-a"],
            "factScope": "current",
            "reportScope": "flagged",
            "accessControlHandling": "export_unrestricted",
            "confirmationPhrase": "whatever",
        },
    )
    assert response.status_code == 422
    assert "I understand the risk" in response.json()["detail"]


@pytest.mark.asyncio
async def test_snapshot_rejects_unknown_acl_policy(client: AsyncClient) -> None:
    response = await client.post(
        f"{API}/snapshot/generate",
        json={
            "tenants": ["tenant-a"],
            "factScope": "current",
            "reportScope": "flagged",
            "accessControlHandling": "make_it_public",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_settings_endpoints(client: AsyncClient) -> None:
    tenant = await client.get(f"{API}/settings/tenant")
    assert tenant.status_code == 200
    assert_keys(tenant.json(), {"tenant_id", "display_name"}, "/settings/tenant")

    keys = await client.post(f"{API}/settings/api-keys", json={"name": "ci", "environment": "staging"})
    assert keys.status_code == 200
    created = keys.json()
    assert "maskedKey" in created and "•" in created["maskedKey"]

    listed = await client.get(f"{API}/settings/api-keys")
    assert listed.status_code == 200
    for item in listed.json():
        # The plaintext key must never be retrievable after creation.
        assert "key" not in item
        assert "maskedKey" in item


@pytest.mark.asyncio
async def test_reports_and_simulate(client: AsyncClient) -> None:
    reports = await client.get(f"{API}/reports")
    assert reports.status_code == 200
    assert isinstance(reports.json(), list)

    simulate = await client.post(
        f"{API}/query/simulate", json={"query": "who reports to whom", "access_level": "internal"}
    )
    assert simulate.status_code == 200
    assert_keys(
        simulate.json(),
        {"query", "retrieved_facts", "withheld_facts", "context", "fact_count"},
        "/query/simulate",
    )


@pytest.mark.asyncio
async def test_contradictions_use_subject_object_naming(client: AsyncClient) -> None:
    """CurationScreen reads `subject`/`object`, matching entity history."""
    response = await client.get(f"{API}/curation/contradictions")
    assert response.status_code == 200
    for item in response.json():
        assert_keys(
            item,
            {"id", "subject", "relation_type", "object", "fact", "valid_at", "invalid_at", "status"},
            "/curation/contradictions",
        )


@pytest.mark.asyncio
async def test_export_metrics_are_null_or_objects(client: AsyncClient) -> None:
    """React throws on an object child, so these must be null or structured, never mixed."""
    body = (await client.get(f"{API}/observability/export-metrics")).json()

    assert body["last_generation"] is None or isinstance(body["last_generation"], dict)
    assert body["last_downstream_fetch"] is None or isinstance(body["last_downstream_fetch"], dict)
    assert isinstance(body["size_trend"], list)
    for point in body["size_trend"]:
        assert_keys(point, {"version", "byte_size"}, "size_trend")


@pytest.mark.asyncio
async def test_latest_snapshot_alias_404s_before_any_export(client: AsyncClient) -> None:
    """The delivery tab publishes this URL, so it must fail clearly, not 500."""
    response = await client.get(f"{API}/snapshots/latest/download")
    assert response.status_code == 404
    assert "No validated snapshot" in response.json()["detail"]


@pytest.mark.asyncio
async def test_notify_path_matches_the_client(client: AsyncClient) -> None:
    response = await client.post(f"{API}/snapshots/does-not-exist/notify")
    assert response.status_code == 404, "the client calls /notify, not /notify-downstream"


@pytest.mark.asyncio
async def test_health(client: AsyncClient) -> None:
    response = await client.get(f"{API}/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
