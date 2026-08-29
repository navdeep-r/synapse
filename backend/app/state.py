"""Shared application state and FastAPI dependencies."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from fastapi import Request

from app.config import Settings, get_settings
from app.db import Database
from app.pipeline import Pipeline
from kg_graphiti.service import GraphService
from kg_observability.stages import StageTracker

logger = logging.getLogger("synapse.state")


@dataclass
class AppState:
    settings: Settings
    db: Database
    graph: GraphService
    tracker: StageTracker = field(default_factory=StageTracker)
    pipeline: Pipeline | None = None
    _graph_task: asyncio.Task | None = None

    @classmethod
    async def create(cls, settings: Settings) -> "AppState":
        db = Database(settings.sqlite_path)
        await db.connect()
        graph = GraphService(settings=settings, db=db)
        tracker = StageTracker()
        
        state = cls(settings=settings, db=db, graph=graph, tracker=tracker)
        
        # Pipeline is only fully initialized here because it depends on state components
        state.pipeline = Pipeline(
            db=db,
            graph=graph,
            tracker=tracker,
            prefilter_min_chars=settings.prefilter_min_chars,
            batch_target_chars=settings.prefilter_batch_target_chars,
        )
        
        # Initialize GitHub reconciliation
        from github.reconciliation import GitHubReconciliationTask
        state.github_reconciler = GitHubReconciliationTask(state)
        
        return state

    def start_graph_in_background(self) -> None:
        """Boot the embedded graph without blocking startup."""
        async def _boot() -> None:
            try:
                await self.graph.start()
            except Exception:
                logger.exception("background graph startup failed")

        self._graph_task = asyncio.create_task(_boot())

    def start_github_reconciler(self) -> None:
        if hasattr(self, "github_reconciler"):
            self.github_reconciler.start()

    async def close(self) -> None:
        if hasattr(self, "github_reconciler"):
            await self.github_reconciler.stop()
        if self.pipeline is not None:
            await self.pipeline.persist_stages()
            await self.pipeline.stop()
        if self._graph_task is not None and not self._graph_task.done():
            self._graph_task.cancel()
        await self.graph.close()
        await self.db.close()

    async def group_ids(self) -> list[str]:
        return ["synapse"]


def get_state(request: Request) -> AppState:
    state: AppState | None = getattr(request.app.state, "synapse", None)
    if state is None:
        raise RuntimeError("Application state is not initialised")
    return state
