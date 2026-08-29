"""Quick end-to-end extraction test — does one add_episode call with the live NVIDIA config."""
from __future__ import annotations
import asyncio
import sys
sys.path.insert(0, ".")
from app.config import get_settings
from app.state import AppState
from kg_contracts.models import EpisodePayload
import uuid
from datetime import UTC, datetime


async def main() -> None:
    settings = get_settings()
    print(f"LLM provider:  {settings.llm_provider}")
    print(f"Chat model:    {settings.nvidia_model}")
    print(f"Embed model:   {settings.nvidia_embedding_model}")
    print()

    state = await AppState.create(settings)
    print("Starting graph...")
    await state.graph.start()

    payload = EpisodePayload(
        episode_id=str(uuid.uuid4()),
        document_id="test-doc",
        source_id="upload",
        source_type="upload",
        name="test",
        body="Alice Johnson is the engineering manager at Acme Corp.",
        source_description="Test document",
        reference_time=datetime.now(UTC),
        group_id=settings.default_tenant + "__upload",
    )

    print("Calling add_episode...")
    result = await state.graph.add_episode(payload)

    if result.error:
        print(f"FAIL: {result.error}")
    else:
        print(f"PASS: nodes={result.nodes_created}, edges={result.edges_created}, llm_calls={result.llm_calls}")

    await state.close()


if __name__ == "__main__":
    asyncio.run(main())
