"""GitHub background reconciliation polling."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from app.state import AppState
from github.connector import GitHubSyncJob

logger = logging.getLogger("synapse.github.reconciliation")

class GitHubReconciliationTask:
    def __init__(self, state: AppState, check_interval_seconds: int = 300):
        self.state = state
        self.check_interval_seconds = check_interval_seconds
        self._task: asyncio.Task | None = None
        self._cancel = False

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._cancel = False
            self._task = asyncio.create_task(self.run_forever())

    async def stop(self) -> None:
        self._cancel = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def run_forever(self) -> None:
        while not self._cancel:
            try:
                await self.reconcile()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("GitHub reconciliation loop failed")
            
            if not self._cancel:
                await asyncio.sleep(self.check_interval_seconds)

    async def reconcile(self) -> None:
        interval_hours = self.state.settings.github_default_poll_interval_hours
        if interval_hours <= 0:
            return

        cutoff = (datetime.now(UTC) - timedelta(hours=interval_hours)).isoformat()
        
        # Find repositories that need syncing
        query = """
            SELECT id FROM github_repositories 
            WHERE status != 'DISABLED'
            AND (last_synced_at IS NULL OR last_synced_at < ?)
        """
        stale_repos = await self.state.db.fetch_all(query, (cutoff,))
        
        for row in stale_repos:
            repo_id = row["id"]
            
            # Ensure not already syncing
            running = await self.state.db.fetch_one(
                "SELECT id FROM github_sync_runs WHERE repo_id = ? AND status = 'SYNCING'",
                (repo_id,)
            )
            
            if not running:
                logger.info("Triggering reconciliation sync for repo %s", repo_id)
                job = GitHubSyncJob(self.state, repo_id)
                asyncio.create_task(job.run())
