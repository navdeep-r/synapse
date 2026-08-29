import asyncio
import tempfile
from pathlib import Path
from app.config import Settings
from app.main import create_app
from httpx import ASGITransport, AsyncClient

HANDBOOK = b"""Engineering Handbook
Alice Johnson is the engineering manager for the Platform team.
Alice Johnson reports to Robert Smith.
"""

async def main():
    settings = Settings(
        data_dir=Path(tempfile.mkdtemp(prefix="synapse-e2e-")),
        strict_prompts=True,
        llm_provider="stub",
    )
    app_obj = create_app(settings)
    transport = ASGITransport(app=app_obj)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with app_obj.router.lifespan_context(app_obj):
            state = app_obj.state.synapse
            await state.graph.ensure_ready()
            
            if state.settings.graph_backend == "neo4j":
                await state.graph.query("MATCH (n) DETACH DELETE n", group_id="synapse", read_only=False)

            response = await client.post(
                "/api/v1/uploads", files={"file": ("handbook.txt", HANDBOOK, "text/plain")}
            )
            await state.pipeline.drain()
            
            topology = (await client.get("/api/v1/graph/topology")).json()
            print("Topology nodes:", len(topology.get("nodes", [])))

if __name__ == "__main__":
    asyncio.run(main())
