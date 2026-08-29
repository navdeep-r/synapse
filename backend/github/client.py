"""GitHub API Client for Synapse."""

from __future__ import annotations

import datetime
import logging
import time
from typing import Any

import httpx
import jwt

logger = logging.getLogger("synapse.github.client")


class GitHubAppClient:
    """Client for authenticating and interacting with GitHub via a GitHub App."""

    def __init__(self, app_id: str, private_key: str):
        self.app_id = app_id
        # Replace literal '\n' characters with actual newlines to support .env single-line strings
        self.private_key = private_key.replace("\\n", "\n") if private_key else private_key
        # Cache for installation tokens: dict[installation_id, {"token": str, "expires_at": float}]
        self._tokens: dict[str, dict[str, Any]] = {}

    def _generate_jwt(self) -> str:
        """Generate a short-lived JWT for authenticating as the GitHub App itself."""
        now = int(time.time())
        # GitHub requires iat to be no more than 60 seconds in the past and exp no more than 10 mins
        payload = {
            "iat": now - 60,
            "exp": now + (10 * 60),
            "iss": self.app_id,
        }
        return jwt.encode(payload, self.private_key, algorithm="RS256")

    async def get_installation_token(self, installation_id: str) -> str:
        """Get a valid installation access token, regenerating if necessary."""
        now = time.time()

        if installation_id in self._tokens:
            token_data = self._tokens[installation_id]
            # Add a 5 minute buffer before expiration
            if now < token_data["expires_at"] - 300:
                return token_data["token"]

        jwt_token = self._generate_jwt()

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.github.com/app/installations/{installation_id}/access_tokens",
                headers={
                    "Authorization": f"Bearer {jwt_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            resp.raise_for_status()
            data = resp.json()

            # expiration is in ISO 8601 format, e.g. "2023-01-01T12:00:00Z"
            dt = datetime.datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))

            self._tokens[installation_id] = {
                "token": data["token"],
                "expires_at": dt.timestamp(),
            }
            return data["token"]

    async def get_repository_installation(self, owner: str, name: str) -> dict[str, Any]:
        """Get the installation ID for a specific repository."""
        jwt_token = self._generate_jwt()
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://api.github.com/repos/{owner}/{name}/installation",
                headers={
                    "Authorization": f"Bearer {jwt_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            resp.raise_for_status()
            return resp.json()

    async def list_installation_repositories(self, installation_id: str) -> list[dict[str, Any]]:
        """List repositories accessible to the given installation."""
        token = await self.get_installation_token(installation_id)
        repos = []
        page = 1

        async with httpx.AsyncClient() as client:
            while True:
                resp = await client.get(
                    "https://api.github.com/installation/repositories",
                    params={"per_page": 100, "page": page},
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
                resp.raise_for_status()
                data = resp.json()

                repos.extend(data.get("repositories", []))

                if len(data.get("repositories", [])) < 100:
                    break
                page += 1

        return repos

    async def get_repository(self, installation_id: str, owner: str, name: str) -> dict[str, Any]:
        """Fetch repository details."""
        token = await self.get_installation_token(installation_id)
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://api.github.com/repos/{owner}/{name}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            resp.raise_for_status()
            return resp.json()

    async def get_branch_head(self, installation_id: str, owner: str, name: str, branch: str) -> str:
        """Fetch HEAD commit SHA for a branch."""
        token = await self.get_installation_token(installation_id)
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://api.github.com/repos/{owner}/{name}/git/ref/heads/{branch}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            resp.raise_for_status()
            return resp.json()["object"]["sha"]

    async def get_tree(self, installation_id: str, owner: str, name: str, tree_sha: str, recursive: bool = False) -> list[dict[str, Any]]:
        """Fetch a git tree."""
        token = await self.get_installation_token(installation_id)
        params = {"recursive": "1"} if recursive else {}
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://api.github.com/repos/{owner}/{name}/git/trees/{tree_sha}",
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            resp.raise_for_status()
            return resp.json().get("tree", [])

    async def get_blob(self, installation_id: str, owner: str, name: str, file_sha: str) -> bytes:
        """Download a git blob."""
        token = await self.get_installation_token(installation_id)
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://api.github.com/repos/{owner}/{name}/git/blobs/{file_sha}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github.v3.raw",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            resp.raise_for_status()
            return resp.content

    async def get_commits(self, installation_id: str, owner: str, name: str, since: str | None = None) -> list[dict[str, Any]]:
        """Fetch commits for a repository."""
        token = await self.get_installation_token(installation_id)
        params = {"per_page": 100}
        if since:
            params["since"] = since
            
        commits = []
        page = 1
        
        async with httpx.AsyncClient() as client:
            while True:
                params["page"] = page
                resp = await client.get(
                    f"https://api.github.com/repos/{owner}/{name}/commits",
                    params=params,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                
                commits.extend(data)
                
                if len(data) < 100:
                    break
                page += 1
                
        return commits

    async def get_commit(self, installation_id: str, owner: str, name: str, ref: str) -> dict[str, Any]:
        """Fetch a specific commit including changed files."""
        token = await self.get_installation_token(installation_id)
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://api.github.com/repos/{owner}/{name}/commits/{ref}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            resp.raise_for_status()
            return resp.json()

    async def compare_commits(self, installation_id: str, owner: str, name: str, base: str, head: str) -> dict[str, Any]:
        """Compare two commits to get changed files and commits between them."""
        token = await self.get_installation_token(installation_id)
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://api.github.com/repos/{owner}/{name}/compare/{base}...{head}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            resp.raise_for_status()
            return resp.json()

    async def get_pulls(self, installation_id: str, owner: str, name: str, state: str = "all") -> list[dict[str, Any]]:
        """Fetch pull requests for a repository."""
        token = await self.get_installation_token(installation_id)
        pulls = []
        page = 1
        async with httpx.AsyncClient() as client:
            while True:
                resp = await client.get(
                    f"https://api.github.com/repos/{owner}/{name}/pulls",
                    params={"state": state, "per_page": 100, "page": page},
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                pulls.extend(data)
                if len(data) < 100:
                    break
                page += 1
        return pulls

    async def get_pull_files(self, installation_id: str, owner: str, name: str, number: int) -> list[dict[str, Any]]:
        """Fetch files modified by a specific pull request."""
        token = await self.get_installation_token(installation_id)
        files = []
        page = 1
        async with httpx.AsyncClient() as client:
            while True:
                resp = await client.get(
                    f"https://api.github.com/repos/{owner}/{name}/pulls/{number}/files",
                    params={"per_page": 100, "page": page},
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                files.extend(data)
                if len(data) < 100:
                    break
                page += 1
        return files

    async def get_issues(self, installation_id: str, owner: str, name: str, state: str = "all") -> list[dict[str, Any]]:
        """Fetch issues for a repository (note: GitHub API includes PRs in issues endpoint)."""
        token = await self.get_installation_token(installation_id)
        issues = []
        page = 1
        async with httpx.AsyncClient() as client:
            while True:
                resp = await client.get(
                    f"https://api.github.com/repos/{owner}/{name}/issues",
                    params={"state": state, "per_page": 100, "page": page},
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                # GitHub's issues API returns PRs as issues too, but they have pull_request key.
                # We can filter out PRs here if we want issues only, or keep them.
                # Let's keep them and we'll filter on the pipeline side if needed.
                issues.extend(data)
                if len(data) < 100:
                    break
                page += 1
        return issues

    async def get_issue_comments(self, installation_id: str, owner: str, name: str, number: int) -> list[dict[str, Any]]:
        """Fetch comments for a specific issue."""
        token = await self.get_installation_token(installation_id)
        comments = []
        page = 1
        async with httpx.AsyncClient() as client:
            while True:
                resp = await client.get(
                    f"https://api.github.com/repos/{owner}/{name}/issues/{number}/comments",
                    params={"per_page": 100, "page": page},
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                comments.extend(data)
                if len(data) < 100:
                    break
                page += 1
        return comments
