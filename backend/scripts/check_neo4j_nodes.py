"""Verify properties of Entity nodes in Neo4j."""
import asyncio
import sys
sys.path.insert(0, ".")
from app.config import get_settings
from app.state import AppState


async def main() -> None:
    settings = get_settings()
    state = await AppState.create(settings)
    await state.graph.start()

    print("Querying all Entity nodes in Neo4j...")
    query = (
        "MATCH (n:Entity) "
        "RETURN n.uuid AS uuid, n.name AS name, n.source_id AS source_id, n.source_type AS source_type "
        "LIMIT 20"
    )
    rows = await state.graph.query(query, read_only=True)
    
    if not rows:
        print("No Entity nodes found in Neo4j.")
    else:
        print(f"Found {len(rows)} nodes:")
        for r in rows:
            print(f"  Name: {r['name']:<25} | ID: {r['uuid'][:10]}... | source_id: {r['source_id']} | source_type: {r['source_type']}")

    await state.close()


if __name__ == "__main__":
    asyncio.run(main())
