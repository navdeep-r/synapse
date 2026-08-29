"""Synapse v2 API — application factory and lifespan wiring."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings, get_settings
from app.security import require_principal, warn_if_exposed
from app.state import AppState
from kg_ontology import OntologyRegistry

logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(name)s: %(message)s")
logger = logging.getLogger("synapse")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        state = await AppState.create(settings)
        app.state.synapse = state

        await OntologyRegistry(state.db).seed()
        await _seed_default_sources(state)

        # RBAC bootstrap: create default org, admin user, supervisor role
        from kg_acl.bootstrap import run_bootstrap

        await run_bootstrap(state.db, settings)

        warn_if_exposed(settings)
        # The embedded graph server takes a few seconds; the console starts
        # polling immediately, so boot it without blocking startup.
        state.start_graph_in_background()
        state.start_github_reconciler()
        assert state.pipeline is not None
        state.pipeline.start()

        logger.info(
            "Synapse v2 ready on %s:%s (graph=%s, llm=%s, rbac_transition=%s)",
            settings.host,
            settings.port,
            settings.graph_backend,
            settings.llm_provider,
            settings.rbac_transition_mode,
        )
        try:
            yield
        finally:
            await state.close()

    app = FastAPI(
        title="Synapse Enterprise Intelligence API",
        description=(
            "Graphiti-backed temporal knowledge graph platform. "
            "Ingests enterprise documents, extracts a closed-vocabulary ontology, "
            "resolves entities, and maintains bi-temporal fact validity."
        ),
        version="2.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    from app.security_middleware import SecurityHeadersMiddleware, RateLimitMiddleware
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RateLimitMiddleware, requests_per_minute=100)

    from app.routers import (
        chat,
        curation,
        export,
        graph,
        observability,
        ontology,
        reports,
        sources,
        summary,
        github,
        mail as mail_module,
    )
    from app.routers import (
        settings as settings_router,
    )
    from app.routers import auth as auth_router
    from app.routers import scopes as scopes_router
    from app.routers import roles as roles_router
    from app.routers import teams as teams_router
    from app.routers import assignments as assignments_router
    from app.routers import users as users_router

    # Auth endpoints are unguarded (login/refresh/logout need to work without auth).
    app.include_router(auth_router.router, prefix="/api/v1")

    # GitHub webhook endpoint is carved out — it authenticates via HMAC, not JWT.
    app.include_router(github.webhook_router, prefix="/api/v1")

    # All other routers are guarded by require_principal.
    guarded = [Depends(require_principal)]
    for module in (
        chat,
        graph,
        ontology,
        sources,
        observability,
        curation,
        export,
        reports,
        settings_router,
        summary,
        github,
        mail_module,
        scopes_router,
        roles_router,
        teams_router,
        assignments_router,
        users_router,
    ):
        app.include_router(module.router, prefix="/api/v1", dependencies=guarded)

    @app.get("/api/v1/health")
    async def health() -> dict:
        state: AppState = app.state.synapse
        return {
            "status": "ok",
            "graph_backend": settings.graph_backend,
            "graph_ready": state.graph.is_ready,
            "graph_error": state.graph.init_error,
            "llm_provider": settings.llm_provider,
            "rbac_transition_mode": settings.rbac_transition_mode,
            "version": "2.0.0",
        }

    return app


async def _seed_default_sources(state: AppState) -> None:
    """Give the console non-empty source rows on first boot."""
    from kg_ingest import SourceRepository

    sources = SourceRepository(state.db)
    for source_id, source_type in (
        ("upload", "upload"),
        ("slack-eng", "slack"),
        ("github-platform", "github"),
        ("notion-handbook", "notion"),
    ):
        await sources.ensure(source_id, source_type)


app = create_app()
