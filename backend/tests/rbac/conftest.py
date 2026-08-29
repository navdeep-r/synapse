"""Shared fixtures for RBAC tests.

Uses an in-memory SQLite database and a FastAPI test client that mirrors
the real application but skips graph/pipeline initialization.

Since ``httpx.ASGITransport`` does not run ASGI lifespan events, we set
``app.state.synapse`` manually before creating the client.
"""

from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.db import Database


@pytest.fixture(scope="session")
def rbac_settings() -> Settings:
    """Settings tuned for RBAC tests: transition mode off, real JWT secret."""
    return Settings(
        data_dir=Path(tempfile.mkdtemp(prefix="synapse-rbac-test-")),
        llm_provider="stub",
        graph_backend="falkordblite",
        strict_prompts=True,
        jwt_secret="test-secret-for-rbac",
        access_token_ttl_minutes=5,
        refresh_token_ttl_days=1,
        rbac_transition_mode=False,  # strict mode for tests
        bootstrap_admin_email="admin@test.local",
        bootstrap_admin_password="Test1234!",
        bootstrap_admin_display_name="Test Admin",
        bootstrap_admin_api_key="syn_test_bootstrap_api_key_12345",
    )


@pytest_asyncio.fixture(scope="session")
async def rbac_db(rbac_settings: Settings) -> AsyncIterator[Database]:
    """Session-scoped DB with RBAC schema."""
    db = Database(rbac_settings.sqlite_path)
    await db.connect()
    yield db
    await db.close()


@pytest_asyncio.fixture(scope="session")
async def bootstrapped_db(rbac_db: Database, rbac_settings: Settings) -> Database:
    """DB with bootstrap already run."""
    from kg_acl.bootstrap import run_bootstrap

    await run_bootstrap(rbac_db, rbac_settings)
    return rbac_db


def _build_test_app(settings: Settings, db: Database) -> "FastAPI":
    """Create a FastAPI app wired for auth testing.

    State is injected directly (no lifespan needed), because httpx's
    ASGI transport does not trigger lifespan events.
    """
    from fastapi import APIRouter, Depends, FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    from app.security import require_principal

    app = FastAPI()

    # Inject state manually — mirrors what lifespan would do
    from unittest.mock import AsyncMock
    state = SimpleNamespace(settings=settings, db=db)
    state.graph = AsyncMock()
    state.graph.query = AsyncMock(return_value=[])
    app.state.synapse = state
    app.state.graph = state.graph

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from app.routers import auth as auth_router
    from app.routers import scopes as scopes_router
    from app.routers import roles as roles_router
    from app.routers import teams as teams_router
    from app.routers import assignments as assignments_router

    app.include_router(auth_router.router, prefix="/api/v1")
    app.include_router(scopes_router.router, prefix="/api/v1", dependencies=[Depends(require_principal)])
    app.include_router(roles_router.router, prefix="/api/v1", dependencies=[Depends(require_principal)])
    app.include_router(teams_router.router, prefix="/api/v1", dependencies=[Depends(require_principal)])
    app.include_router(assignments_router.router, prefix="/api/v1", dependencies=[Depends(require_principal)])

    # A guarded test endpoint
    guarded_router = APIRouter()

    @guarded_router.get("/test/guarded")
    async def guarded_endpoint():
        return {"message": "you are authorized"}

    app.include_router(
        guarded_router,
        prefix="/api/v1",
        dependencies=[Depends(require_principal)],
    )

    return app


@pytest_asyncio.fixture(scope="session")
async def test_app(rbac_settings: Settings, bootstrapped_db: Database):
    """Test app with RBAC wiring and bootstrapped DB."""
    return _build_test_app(rbac_settings, bootstrapped_db)


@pytest_asyncio.fixture(scope="session")
async def client(test_app) -> AsyncIterator[AsyncClient]:
    """Async HTTP client against the test app.

    Uses ``raise_app_exceptions=True`` so HTTP error codes propagate
    as response status codes instead of re-raising as Python exceptions.
    """
    transport = ASGITransport(app=test_app, raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def create_transition_client(
    settings_overrides: dict | None = None,
) -> tuple[AsyncClient, Database]:
    """Create a fresh test client with custom settings for transition-mode tests.

    Returns (client, db) — caller must close both.
    """
    import tempfile
    from pathlib import Path

    from kg_acl.bootstrap import run_bootstrap

    overrides = {
        "data_dir": Path(tempfile.mkdtemp(prefix="synapse-transition-")),
        "llm_provider": "stub",
        "graph_backend": "falkordblite",
        "jwt_secret": "test-secret",
        "bootstrap_admin_email": "admin@test.local",
        "bootstrap_admin_password": "Test1234!",
        **(settings_overrides or {}),
    }
    settings = Settings(**overrides)

    db = Database(settings.sqlite_path)
    await db.connect()
    await run_bootstrap(db, settings)

    app = _build_test_app(settings, db)
    transport = ASGITransport(app=app, raise_app_exceptions=True)
    client = AsyncClient(transport=transport, base_url="http://test")
    return client, db
