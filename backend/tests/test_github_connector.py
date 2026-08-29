"""Tests for GitHub Connector."""

import pytest
from unittest.mock import AsyncMock, patch

from github.connector import GitHubSyncJob, should_include_file


def test_should_include_file():
    assert should_include_file("src/main.py") is True
    assert should_include_file("node_modules/lib.js") is False
    assert should_include_file("Dockerfile") is True
    assert should_include_file("src/image.png") is False


@pytest.mark.asyncio
async def test_github_sync_job():
    # Setup mock state
    mock_db = AsyncMock()
    async def mock_fetch_one(query, params=None):
        if "github_repositories" in query:
            return {
                "id": "repo1",
                "installation_id": "inst1",
                "tenant_id": "tenant-a",
                "github_repo_id": "123456",
                "owner": "testorg",
                "name": "testrepo",
                "default_branch": "main",
            }
        return None

    mock_db.fetch_one.side_effect = mock_fetch_one

    mock_pipeline = AsyncMock()
    mock_settings = AsyncMock()
    mock_settings.github_app_id = "testapp"
    mock_settings.github_app_private_key = "testkey"

    class MockAppState:
        def __init__(self):
            self.db = mock_db
            self.pipeline = mock_pipeline
            self.settings = mock_settings

    state = MockAppState()
    job = GitHubSyncJob(state, "repo1")

    with patch("github.connector.GitHubAppClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.get_branch_head = AsyncMock(return_value="abcde12345")
        mock_client.get_tree = AsyncMock(return_value=[
            {"path": "main.py", "type": "blob", "sha": "blobsha1"},
            {"path": "package.json", "type": "blob", "sha": "blobsha2"},
            {"path": "ignored.png", "type": "blob", "sha": "blobsha3"},
        ])
        mock_client.get_blob = AsyncMock(return_value=b"print('hello world')")
        mock_client.get_commits = AsyncMock(return_value=[{"sha": "commitsha1"}])
        mock_client.get_commit = AsyncMock(return_value={
            "sha": "commitsha1",
            "commit": {
                "message": "Initial commit",
                "author": {"name": "Test User", "email": "test@example.com", "date": "2023-01-01T00:00:00Z"}
            },
            "author": {"login": "testuser"},
            "parents": [],
            "files": [{"filename": "main.py"}]
        })

        await job.run()

        # Should have fetched branch head, tree, and commits
        mock_client.get_branch_head.assert_called_once()
        mock_client.get_tree.assert_called_once()
        mock_client.get_commits.assert_called_once()

        # Should have downloaded blob for main.py and package.json
        assert mock_client.get_blob.call_count == 2

        # Pipeline should be called 3 times (2 files, 1 commit)
        assert mock_pipeline.submit.call_count == 3

        # Verify document ID structure
        args, _ = mock_pipeline.submit.call_args_list[0]
        doc = args[0]
        assert doc.document_id == "github:repo:123456:file:main.py"
        assert doc.text == "print('hello world')"
        
        args, _ = mock_pipeline.submit.call_args_list[2]
        commit_doc = args[0]
        assert commit_doc.document_id == "github:repo:123456:commit:commitsha1"
        assert "Initial commit" in commit_doc.text


@pytest.mark.asyncio
async def test_github_sync_job_incremental():
    mock_db = AsyncMock()
    async def mock_fetch_one(query, params=None):
        if "github_repositories" in query:
            return {
                "id": "repo1",
                "installation_id": "inst1",
                "tenant_id": "tenant-a",
                "github_repo_id": "123456",
                "owner": "testorg",
                "name": "testrepo",
                "default_branch": "main",
                "last_synced_commit_sha": "old_sha"
            }
        return None
    mock_db.fetch_one.side_effect = mock_fetch_one

    mock_pipeline = AsyncMock()
    mock_settings = AsyncMock()
    mock_settings.github_app_id = "testapp"
    mock_settings.github_app_private_key = "testkey"

    class MockAppState:
        def __init__(self):
            self.db = mock_db
            self.pipeline = mock_pipeline
            self.settings = mock_settings

    state = MockAppState()
    job = GitHubSyncJob(state, "repo1")

    with patch("github.connector.GitHubAppClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.get_branch_head = AsyncMock(return_value="new_sha")
        mock_client.compare_commits = AsyncMock(return_value={
            "files": [
                {"filename": "main.py", "status": "modified", "sha": "blobsha1"},
                {"filename": "old.py", "status": "removed", "sha": "blobsha2"}
            ],
            "commits": [
                {
                    "sha": "new_sha",
                    "commit": {"message": "Update main.py"},
                    "author": {"login": "testuser"}
                }
            ]
        })
        mock_client.get_blob = AsyncMock(return_value=b"print('updated')")
        mock_client.get_commit = AsyncMock(return_value={
            "sha": "new_sha",
            "commit": {"message": "Update main.py"},
            "files": [{"filename": "main.py"}]
        })

        await job.run()

        mock_client.get_branch_head.assert_called_once()
        mock_client.compare_commits.assert_called_once_with("inst1", "testorg", "testrepo", "old_sha", "new_sha")
        
        # Pipeline should be called 3 times (1 modified file, 1 tombstone, 1 commit)
        assert mock_pipeline.submit.call_count == 3
        
        docs = [call.args[0] for call in mock_pipeline.submit.call_args_list]
        
        # Modified file
        assert docs[0].document_id == "github:repo:123456:file:main.py"
        assert docs[0].text == "print('updated')"
        
        # Removed file (tombstone)
        assert docs[1].document_id == "github:repo:123456:file:old.py"
        assert docs[1].text == "[DELETED]"
        assert docs[1].byte_size == 0
        
        # Commit
        assert docs[2].document_id == "github:repo:123456:commit:new_sha"
