"""Graph driver factory.

`falkordblite` runs an embedded FalkorDB (a bundled redis-server plus the
FalkorDB module) inside this process — no Docker, no external service. It drives
Graphiti's actively maintained `FalkorDriver`, so switching to a hosted FalkorDB
or Neo4j is a construction change and nothing else.

Kuzu is deliberately not offered: graphiti-core marks its Kuzu backend as
deprecated because the upstream project is unmaintained.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("synapse.graph")


def build_driver(
    backend: str,
    *,
    database: str,
    db_path: Path | None = None,
    falkordb_host: str = "localhost",
    falkordb_port: int = 6379,
    neo4j_uri: str = "bolt://localhost:7687",
    neo4j_user: str = "neo4j",
    neo4j_password: str = "password",
) -> tuple[Any, Any | None]:
    """Return `(graph_driver, embedded_server)`.

    `embedded_server` is the process-owned FalkorDB instance when running
    embedded, and None otherwise; the caller is responsible for closing it.
    """
    if backend == "falkordblite":
        from graphiti_core.driver.falkordb_driver import FalkorDriver
        from redislite.async_falkordb_client import AsyncFalkorDB

        if db_path is None:
            raise ValueError("db_path is required for the embedded falkordblite backend")
        db_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info("Starting embedded FalkorDB at %s", db_path)
        server = AsyncFalkorDB(dbfilename=str(db_path))
        return FalkorDriver(falkor_db=server, database=database), server

    if backend == "falkordb":
        from graphiti_core.driver.falkordb_driver import FalkorDriver

        logger.info("Connecting to FalkorDB at %s:%s", falkordb_host, falkordb_port)
        return FalkorDriver(host=falkordb_host, port=falkordb_port, database=database), None

    if backend == "neo4j":
        from graphiti_core.driver.neo4j_driver import Neo4jDriver

        logger.info("Connecting to Neo4j at %s", neo4j_uri)
        # If database is provided and is not 'neo4j', pass it. If None or empty, pass None so Neo4j driver uses default.
        # Aura databases are sometimes not named 'neo4j', so we need to pass the explicit name or None.
        neo4j_db = database if database else None
        return Neo4jDriver(uri=neo4j_uri, user=neo4j_user, password=neo4j_password, database=neo4j_db), None

    raise ValueError(f"Unsupported graph backend: {backend!r}")
