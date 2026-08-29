"""Shared fixtures.

The embedded FalkorDB server takes a few seconds to boot, so the graph service is
session-scoped and tests share one instance with distinct group_ids.
"""

from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio

from app.config import Settings
from kg_contracts import EpisodePayload
from kg_graphiti.service import GraphService


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(
        data_dir=Path(tempfile.mkdtemp(prefix="synapse-test-")),
        # R2: any unmapped response model must fail the suite, not degrade quietly.
        strict_prompts=True,
        llm_provider="stub",
        graph_backend="falkordblite",
    )


@pytest_asyncio.fixture(scope="session")
async def graph(settings: Settings) -> AsyncIterator[GraphService]:
    service = GraphService(settings=settings)
    await service.start()
    yield service
    await service.close()


def episode(
    body: str,
    *,
    group_id: str,
    name: str = "chunk",
    days_ago: int = 0,
    document_id: str = "doc",
) -> EpisodePayload:
    reference = datetime.now(UTC) - timedelta(days=days_ago)
    return EpisodePayload(
        episode_id=f"{group_id}-{name}",
        name=name,
        body=body,
        source_description="upload/test.txt",
        reference_time=reference,
        group_id=group_id,
        document_id=document_id,
        chunk_ids=[f"{document_id}::c0"],
    )
