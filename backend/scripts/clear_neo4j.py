import asyncio
import warnings

# Suppress warnings from dangling background tasks in graphiti-core
warnings.filterwarnings("ignore", category=RuntimeWarning, module="asyncio")
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from kg_graphiti.drivers import build_driver

async def main():
    settings = get_settings()
    print(f"Connecting to Neo4j at {settings.neo4j_uri}...")
    driver, server = build_driver(
        settings.graph_backend,
        database=settings.graph_database,
        neo4j_uri=settings.neo4j_uri,
        neo4j_user=settings.neo4j_user,
        neo4j_password=settings.neo4j_password,
    )
    
    print("Executing DETACH DELETE...")
    await driver.execute_query("MATCH (n) DETACH DELETE n")
    print("Successfully cleared Neo4j database.")
    
    await driver.close()

if __name__ == "__main__":
    asyncio.run(main())
