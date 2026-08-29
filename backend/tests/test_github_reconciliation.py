import pytest
import asyncio
from unittest.mock import AsyncMock, patch
from datetime import UTC, datetime, timedelta

from github.reconciliation import GitHubReconciliationTask

@pytest.mark.asyncio
async def test_reconcile_stale_repos():
    mock_db = AsyncMock()
    now = datetime.now(UTC)
    old_time = (now - timedelta(hours=2)).isoformat()
    
    mock_db.fetch_all.return_value = [
        {"id": "repo1", "last_synced_at": old_time}
    ]
    
    mock_db.fetch_one.side_effect = [
        None, # Not currently syncing
    ]
    
    mock_state = AsyncMock()
    mock_state.db = mock_db
    mock_state.settings.github_default_poll_interval_hours = 1
    
    task = GitHubReconciliationTask(mock_state)
    
    with patch("github.reconciliation.GitHubSyncJob") as mock_job_cls:
        mock_job = mock_job_cls.return_value
        mock_job.run = AsyncMock()
        
        await task.reconcile()
        
        mock_job_cls.assert_called_once_with(mock_state, "repo1")
        
        # Allow async task to run
        await asyncio.sleep(0.01)
        mock_job.run.assert_called_once()
