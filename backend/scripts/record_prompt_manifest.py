"""Re-record the prompt-model manifest after a graphiti-core upgrade (R4).

Runs a representative episode through the real Graphiti pipeline, collects every
`response_model` it asked the LLM client for, and rewrites
`tests/fixtures/prompt_models.json`.

    python scripts/record_prompt_manifest.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings  # noqa: E402
from kg_contracts import EpisodePayload  # noqa: E402
from kg_graphiti.ontology import EDGE_TYPES, ENTITY_TYPES  # noqa: E402
from kg_graphiti.service import GraphService  # noqa: E402

MANIFEST_PATH = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "prompt_models.json"

SAMPLES = [
    (
        "Alice Johnson is the engineering manager for the Platform team. "
        "Alice Johnson reports to Robert Smith. "
        "The billing-service depends on auth-library and uses PostgreSQL.",
        2,
    ),
    ("Alice Johnson now reports to Carol Danvers.", 0),
    ("Bob Smith uses PostgreSQL for the reporting workload.", 0),
]


async def main() -> None:
    logging.basicConfig(level=logging.ERROR)
    settings = Settings(
        data_dir=Path(tempfile.mkdtemp(prefix="synapse-manifest-")),
        # Not strict: the whole point is to discover unmapped models, not crash on them.
        strict_prompts=False,
        llm_provider="stub",
    )
    service = GraphService(settings=settings)
    await service.start()

    now = datetime.now(UTC)
    for index, (body, days_ago) in enumerate(SAMPLES):
        await service.add_episode(
            EpisodePayload(
                episode_id=f"manifest-{index}",
                name=f"manifest-{index}",
                body=body,
                source_description="manifest/sample.txt",
                reference_time=now - timedelta(days=days_ago),
                document_id=f"doc-{index}",
                chunk_ids=[f"doc-{index}::c0"],
            )
        )

    observed = set(service.graphiti.llm_client.observed_models)
    ontology = set(ENTITY_TYPES) | set(EDGE_TYPES)

    manifest = {
        "_comment": (
            "R4: response models graphiti-core requests during a real add_episode run. "
            "Regenerate with `python scripts/record_prompt_manifest.py` after bumping the "
            "graphiti-core pin, and read the diff before extending the dispatch table."
        ),
        "graphiti_core_version": version("graphiti-core"),
        "expected_models": sorted(observed - ontology),
        "ontology_models": sorted(ontology),
    }

    previous = json.loads(MANIFEST_PATH.read_text()) if MANIFEST_PATH.exists() else {}
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")

    added = sorted(set(manifest["expected_models"]) - set(previous.get("expected_models", [])))
    removed = sorted(set(previous.get("expected_models", [])) - set(manifest["expected_models"]))
    print(f"graphiti-core {manifest['graphiti_core_version']}")
    print(f"observed prompt models: {manifest['expected_models']}")
    if added:
        print(f"ADDED (need handlers): {added}")
    if removed:
        print(f"REMOVED: {removed}")
    if not added and not removed:
        print("no change")
    print(f"fallbacks hit during recording: {service.counters.get('stub_fallback', 0)}")

    await service.close()


if __name__ == "__main__":
    asyncio.run(main())
