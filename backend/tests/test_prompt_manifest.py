"""R4 + R2: guard the coupling to graphiti-core's internal prompt set.

`StubLLMClient` dispatches on `response_model.__name__`, which graphiti-core does
not treat as public API. These tests make a version bump fail loudly with a
readable diff instead of degrading extraction quietly.
"""

from __future__ import annotations

import json
from importlib.metadata import version
from pathlib import Path

import pytest

from kg_graphiti.llm_stub import SUPPORTED_RESPONSE_MODELS, StubLLMClient
from kg_graphiti.service import GraphService
from tests.conftest import episode

MANIFEST_PATH = Path(__file__).parent / "fixtures" / "prompt_models.json"
MANIFEST = json.loads(MANIFEST_PATH.read_text())

GROUP = "manifest-group"


def test_pinned_graphiti_version_matches_manifest() -> None:
    """The manifest is only meaningful for the version it was recorded against."""
    assert version("graphiti-core") == MANIFEST["graphiti_core_version"], (
        "graphiti-core was upgraded. Re-record the prompt manifest "
        "(scripts/record_prompt_manifest.py), read the diff, and extend the "
        "StubLLMClient dispatch table before updating this pin."
    )


def test_every_manifest_model_has_a_handler() -> None:
    declared = set(MANIFEST["expected_models"]) | set(MANIFEST["conditional_models"])
    missing = sorted(declared - SUPPORTED_RESPONSE_MODELS)
    assert not missing, f"StubLLMClient is missing handlers for: {missing}"


@pytest.mark.asyncio
async def test_real_run_requests_only_known_models(graph: GraphService) -> None:
    """Drive a real episode and diff the models Graphiti actually asked for."""
    await graph.add_episode(
        episode(
            "Alice Johnson reports to Robert Smith. "
            "The billing-service depends on auth-library.",
            group_id=GROUP,
            name="manifest",
        )
    )

    client = graph.graphiti.llm_client
    assert isinstance(client, StubLLMClient)

    known = (
        set(MANIFEST["expected_models"])
        | set(MANIFEST["conditional_models"])
        | set(MANIFEST["ontology_models"])
    )
    unexpected = sorted(client.observed_models - known)

    assert not unexpected, (
        f"graphiti-core requested response models that are not in the manifest: "
        f"{unexpected}. Extend StubLLMClient._HANDLERS and re-record the manifest."
    )


@pytest.mark.asyncio
async def test_no_silent_fallbacks_during_a_real_run(graph: GraphService) -> None:
    """R2: the headline assertion — the stub never answered a prompt it did not understand."""
    fallbacks = graph.counters.get("stub_fallback", 0)
    assert fallbacks == 0, (
        f"{fallbacks} prompt(s) hit the unknown-response-model fallback. "
        "Resolution or invalidation silently did nothing for those calls."
    )
