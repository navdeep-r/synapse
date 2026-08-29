"""R3: prove the consistency endpoint detects real divergence.

The endpoint implies a consistency story, so these tests break the invariant on
purpose and assert the mismatch surfaces with a true age.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from kg_observability.reconcile import STORE_GRAPH, STORE_METADATA, reconcile

API = "/api/v1"

DOC = b"""Alice Johnson reports to Robert Smith.

The billing-service depends on auth-library.
"""


@pytest_asyncio.fixture(scope="module")
async def app_client() -> AsyncIterator[tuple[AsyncClient, object]]:
    settings = Settings(
        data_dir=Path(tempfile.mkdtemp(prefix="synapse-reconcile-")),
        strict_prompts=True,
        llm_provider="stub",
        reconcile_stuck_after_seconds=120,
    )
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        async with app.router.lifespan_context(app):
            state = app.state.synapse
            await state.graph.ensure_ready()
            await http.post(f"{API}/uploads", files={"file": ("doc.txt", DOC, "text/plain")})
            await state.pipeline.drain()
            yield http, state


@pytest.mark.asyncio
async def test_healthy_pipeline_reports_no_mismatches(app_client) -> None:
    client, _state = app_client
    body = (await client.get(f"{API}/observability/consistency")).json()
    assert body["mismatches"] == [], "a clean run must not invent mismatches"


@pytest.mark.asyncio
async def test_lost_graph_write_is_detected(app_client) -> None:
    """A committed receipt pointing at an episode the graph does not have."""
    _client, state = app_client

    await state.db.execute(
        "INSERT INTO episode_receipts(episode_id, document_id, status, chunk_ids_json, "
        "created_at, committed_at, graph_uuid) VALUES(?, ?, 'committed', ?, ?, ?, ?)",
        (
            "ep-lost-graph-write",
            "doc-phantom",
            json.dumps(["doc-phantom::c0000"]),
            (datetime.now(UTC) - timedelta(minutes=45)).isoformat(),
            (datetime.now(UTC) - timedelta(minutes=45)).isoformat(),
            "uuid-that-is-not-in-the-graph",
        ),
    )

    mismatches = await reconcile(state.db, state.graph)
    lost = next((m for m in mismatches if m.tx_id == "ep-lost-graph-write"), None)

    assert lost is not None, "a committed receipt with no graph episode must be reported"
    assert lost.kind == "missing_in_graph"
    assert lost.store == STORE_GRAPH
    # The age is real, not a placeholder.
    assert 44 <= lost.duration_minutes <= 46

    await state.db.execute(
        "DELETE FROM episode_receipts WHERE episode_id = 'ep-lost-graph-write'"
    )


@pytest.mark.asyncio
async def test_stuck_pending_receipt_is_detected(app_client) -> None:
    """The intent-then-commit gap: the process died before Graphiti returned."""
    _client, state = app_client

    await state.db.execute(
        "INSERT INTO episode_receipts(episode_id, document_id, status, chunk_ids_json, "
        "created_at) VALUES(?, ?, 'pending', ?, ?)",
        (
            "ep-stuck",
            "doc-stuck",
            json.dumps([]),
            (datetime.now(UTC) - timedelta(minutes=17)).isoformat(),
        ),
    )

    mismatches = await reconcile(state.db, state.graph, stuck_after_seconds=120)
    stuck = next((m for m in mismatches if m.tx_id == "ep-stuck"), None)

    assert stuck is not None
    assert stuck.kind == "stuck_pending"
    assert 16 <= stuck.duration_minutes <= 18

    await state.db.execute("DELETE FROM episode_receipts WHERE episode_id = 'ep-stuck'")


@pytest.mark.asyncio
async def test_recent_pending_receipt_is_not_a_mismatch(app_client) -> None:
    """An in-flight episode is normal and must not be reported as divergence."""
    _client, state = app_client

    await state.db.execute(
        "INSERT INTO episode_receipts(episode_id, document_id, status, chunk_ids_json, "
        "created_at) VALUES(?, ?, 'pending', ?, ?)",
        (
            "ep-inflight",
            "doc-inflight",
            json.dumps([]),
            datetime.now(UTC).isoformat(),
        ),
    )

    mismatches = await reconcile(state.db, state.graph, stuck_after_seconds=120)
    assert not any(m.tx_id == "ep-inflight" for m in mismatches)

    await state.db.execute("DELETE FROM episode_receipts WHERE episode_id = 'ep-inflight'")


@pytest.mark.asyncio
async def test_lost_metadata_write_is_detected(app_client) -> None:
    """The other direction: a graph episode whose receipt vanished."""
    _client, state = app_client

    row = await state.db.fetch_one(
        "SELECT episode_id, graph_uuid FROM episode_receipts WHERE graph_uuid IS NOT NULL LIMIT 1"
    )
    assert row is not None, "the fixture upload should have produced a receipt"

    saved = await state.db.fetch_one(
        "SELECT * FROM episode_receipts WHERE episode_id = ?", (row["episode_id"],)
    )
    await state.db.execute(
        "DELETE FROM episode_receipts WHERE episode_id = ?", (row["episode_id"],)
    )

    mismatches = await reconcile(state.db, state.graph)
    orphan = next((m for m in mismatches if m.tx_id == row["graph_uuid"]), None)

    assert orphan is not None, "a graph episode with no receipt must be reported"
    assert orphan.kind == "missing_in_metadata"
    assert orphan.store == STORE_METADATA

    await state.db.execute(
        "INSERT INTO episode_receipts(episode_id, document_id, status, chunk_ids_json, "
        "created_at, committed_at, graph_uuid) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
        (
            saved["episode_id"],
            saved["document_id"],
            saved["group_id"],
            saved["status"],
            saved["chunk_ids_json"],
            saved["created_at"],
            saved["committed_at"],
            saved["graph_uuid"],
        ),
    )


@pytest.mark.asyncio
async def test_escalate_acknowledges_a_mismatch(app_client) -> None:
    """The previously-dead Escalate button now does something."""
    client, state = app_client

    await state.db.execute(
        "INSERT INTO episode_receipts(episode_id, document_id, status, chunk_ids_json, "
        "created_at) VALUES(?, ?, ?, 'pending', ?, ?)",
        (
            "ep-escalate",
            "doc-escalate",
            state.settings.SYNAPSE_ORG_ID,
            json.dumps([]),
            (datetime.now(UTC) - timedelta(minutes=30)).isoformat(),
        ),
    )

    body = (await client.get(f"{API}/observability/consistency")).json()
    target = next(m for m in body["mismatches"] if m["txId"] == "ep-escalate")

    escalated = await client.post(f"{API}/observability/consistency/{target['id']}/escalate")
    assert escalated.status_code == 200
    assert escalated.json()["status"] == "escalated"

    after = (await client.get(f"{API}/observability/consistency")).json()
    assert not any(m["txId"] == "ep-escalate" for m in after["mismatches"])

    quarantine = (await client.get(f"{API}/curation/quarantine")).json()
    assert any(item["subject"] == "ep-escalate" for item in quarantine)

    await state.db.execute("DELETE FROM episode_receipts WHERE episode_id = 'ep-escalate'")
