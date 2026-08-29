"""Pydantic models for GitHub Connector domain."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class GitHubInstallation(BaseModel):
    installation_id: str
    account_login: str
    account_type: str
    permissions: dict[str, str] = Field(default_factory=dict)
    repository_selection: str
    suspended_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @property
    def permissions_json(self) -> str:
        return json.dumps(self.permissions)


class GitHubRepository(BaseModel):
    id: str
    installation_id: str
    tenant_id: str
    github_repo_id: str
    owner: str
    name: str
    full_name: str
    private: bool = False
    default_branch: str
    language: str | None = None
    description: str | None = None
    poll_interval_hours: int
    last_synced_commit_sha: str | None = None
    last_synced_at: datetime | None = None
    status: str = "READY"
    sync_enabled: bool = True
    created_at: datetime
    updated_at: datetime
