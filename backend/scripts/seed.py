"""Seed initial configuration, default admin credentials, and system sources.

Does NOT populate demo/mock graph data.

    uv run python scripts/seed.py [--reset]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.state import AppState  # noqa: E402
from kg_acl.bootstrap import run_bootstrap  # noqa: E402
from kg_ingest import SourceRepository  # noqa: E402
from kg_ontology import OntologyRegistry  # noqa: E402

logger = logging.getLogger("synapse.seed")


async def seed(reset: bool = False) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = get_settings()

    if reset and settings.data_dir.exists():
        logger.info("Removing %s", settings.data_dir)
        shutil.rmtree(settings.data_dir)

    state = await AppState.create(settings)

    # 1. Seed ontology registry
    logger.info("Seeding ontology schema...")
    await OntologyRegistry(state.db).seed()

    # 2. Seed default data sources
    logger.info("Seeding data sources...")
    sources = SourceRepository(state.db)
    for source_id, source_type in (
        ("upload", "upload"),
        ("github", "github"),
        ("mail", "mail"),
    ):
        await sources.ensure(source_id, source_type)

    # 3. Seed bootstrap admin credentials, roles, and default scope
    logger.info("Seeding RBAC bootstrap admin credentials and roles...")
    await run_bootstrap(state.db, settings)

    logger.info("")
    logger.info("Seed complete (credentials and schema only, no graph data).")
    logger.info("Admin Email: %s", settings.bootstrap_admin_email)
    logger.info("Admin Password: %s", settings.bootstrap_admin_password)

    await state.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="delete existing data first")
    args = parser.parse_args()
    asyncio.run(seed(reset=args.reset))
