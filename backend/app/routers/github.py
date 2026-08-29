"""GitHub Connector API endpoints."""

from __future__ import annotations

import logging
from typing import Any
import hmac
import hashlib
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Header

from app.state import AppState, get_state
from github.models import GitHubRepository

router = APIRouter(prefix="/github", tags=["github"])
logger = logging.getLogger("synapse.api.github")


@router.get("/repos")
async def list_repositories(request: Request, state: AppState = Depends(get_state)) -> list[dict[str, Any]]:
    """List all GitHub repositories configured for synchronization."""
    # For now, default to the default_tenant. Future phases will extract tenant from auth token.
    tenant = state.settings.default_tenant
    rows = await state.db.fetch_all(
        "SELECT * FROM github_repositories WHERE tenant_id = ?",
        (tenant,)
    )
    return [GitHubRepository(**row).model_dump() for row in rows]


from pydantic import BaseModel
import uuid

class AddRepositoryRequest(BaseModel):
    owner: str
    name: str

@router.post("/repos")
async def add_repository(req: AddRepositoryRequest, request: Request, state: AppState = Depends(get_state)) -> dict[str, Any]:
    """Add a GitHub repository."""
    try:
        tenant = state.settings.default_tenant
        
        from github.client import GitHubAppClient
        import httpx
        
        client = GitHubAppClient(
            app_id=state.settings.github_app_id or "",
            private_key=state.settings.github_app_private_key or ""
        )
        
        try:
            install_data = await client.get_repository_installation(req.owner, req.name)
            installation_id = str(install_data["id"])
            
            repo_data = await client.get_repository(installation_id, req.owner, req.name)
            github_repo_id = str(repo_data["id"])
            default_branch = repo_data.get("default_branch", "main")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise HTTPException(status_code=404, detail="Repository not found or GitHub App is not installed on it")
            raise HTTPException(status_code=400, detail=str(e))
            
        repo_uuid = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        
        # Check if exists
        row = await state.db.fetch_one("SELECT id FROM github_repositories WHERE github_repo_id = ?", (github_repo_id,))
        if row:
            raise HTTPException(status_code=409, detail="Repository already added")
            
        await state.db.execute(
            """
            INSERT INTO github_repositories (
                id, tenant_id, installation_id, owner, name, full_name, github_repo_id,
                default_branch, poll_interval_hours, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'READY', ?, ?)
            """,
            (repo_uuid, tenant, installation_id, req.owner, req.name, f"{req.owner}/{req.name}", github_repo_id, default_branch, state.settings.github_default_poll_interval_hours, now, now)
        )
        
        row = await state.db.fetch_one("SELECT * FROM github_repositories WHERE id = ?", (repo_uuid,))
        return GitHubRepository(**row).model_dump()
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=traceback.format_exc())


@router.get("/repos/{repo_id}")
async def get_repository(repo_id: str, request: Request, state: AppState = Depends(get_state)) -> dict[str, Any]:
    """Get a specific GitHub repository configuration."""
    tenant = state.settings.default_tenant
    row = await state.db.fetch_one(
        "SELECT * FROM github_repositories WHERE id = ? AND tenant_id = ?",
        (repo_id, tenant)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Repository not found")
    return GitHubRepository(**row).model_dump()


@router.post("/repos/{repo_id}/sync", status_code=202)
async def trigger_sync(repo_id: str, background_tasks: BackgroundTasks, request: Request, state: AppState = Depends(get_state)):
    """Trigger an asynchronous synchronization for a repository."""
    tenant = state.settings.default_tenant
    row = await state.db.fetch_one(
        "SELECT id FROM github_repositories WHERE id = ? AND tenant_id = ?",
        (repo_id, tenant)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Repository not found")
        
    # Check if currently syncing
    running = await state.db.fetch_one(
        "SELECT id FROM github_sync_runs WHERE repo_id = ? AND status = 'SYNCING'",
        (repo_id,)
    )
    if running:
        raise HTTPException(status_code=409, detail="Sync already in progress")
        
    from github.connector import GitHubSyncJob
    job = GitHubSyncJob(state, repo_id)
    background_tasks.add_task(job.run)
    return {"message": "Sync job enqueued"}


@router.get("/repos")
async def list_repositories(request: Request, state: AppState = Depends(get_state)) -> list[dict[str, Any]]:
    """List all GitHub repositories configured for synchronization."""
    # For now, default to the default_tenant. Future phases will extract tenant from auth token.
    tenant = state.settings.default_tenant
    rows = await state.db.fetch_all(
        "SELECT * FROM github_repositories WHERE tenant_id = ?",
        (tenant,)
    )
    return [GitHubRepository(**row).model_dump() for row in rows]


from pydantic import BaseModel
import uuid

class AddRepositoryRequest(BaseModel):
    owner: str
    name: str

class AgentWebhookConfig(BaseModel):
    repo_id: str
    webhook_url: str
    secret: str | None = None
    events: list[str] = ["sync_completed", "need_attention_detected", "merge_commit"]

@router.post("/repos/{repo_id}/agent-webhook")
async def configure_agent_webhook(
    repo_id: str,
    config: AgentWebhookConfig,
    state: AppState = Depends(get_state)
) -> dict[str, Any]:
    """Configure an external agent webhook for a repository."""
    tenant = state.settings.default_tenant
    repo = await state.db.fetch_one(
        "SELECT * FROM github_repositories WHERE id = ? AND tenant_id = ?",
        (repo_id, tenant)
    )
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    
    import json
    await state.db.execute(
        """
        INSERT INTO github_agent_webhooks (repo_id, webhook_url, secret, events_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(repo_id) DO UPDATE SET
            webhook_url = excluded.webhook_url,
            secret = excluded.secret,
            events_json = excluded.events_json,
            updated_at = excluded.updated_at
        """,
        (repo_id, config.webhook_url, config.secret, json.dumps(config.events), datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat())
    )
    return {"status": "configured"}

@router.get("/repos/{repo_id}/agent-webhook")
async def get_agent_webhook(repo_id: str, state: AppState = Depends(get_state)) -> dict[str, Any]:
    """Get the agent webhook configuration for a repository."""
    tenant = state.settings.default_tenant
    row = await state.db.fetch_one(
        "SELECT * FROM github_agent_webhooks WHERE repo_id = ?",
        (repo_id,)
    )
    if not row:
        return {"configured": False}
    return {
        "configured": True,
        "webhook_url": row["webhook_url"],
        "events": json.loads(row["events_json"])
    }

@router.delete("/repos/{repo_id}/agent-webhook")
async def delete_agent_webhook(repo_id: str, state: AppState = Depends(get_state)) -> dict[str, Any]:
    """Remove the agent webhook configuration for a repository."""
    await state.db.execute("DELETE FROM github_agent_webhooks WHERE repo_id = ?", (repo_id,))
    return {"status": "deleted"}

@router.post("/repos/{repo_id}/agent-webhook/test")
async def test_agent_webhook(repo_id: str, state: AppState = Depends(get_state)) -> dict[str, Any]:
    """Send a test payload to the configured agent webhook."""
    row = await state.db.fetch_one(
        "SELECT * FROM github_agent_webhooks WHERE repo_id = ?",
        (repo_id,)
    )
    if not row:
        raise HTTPException(status_code=404, detail="No webhook configured for this repository")
    
    import httpx
    import hmac
    import hashlib
    import json
    
    test_payload = {
        "event": "test",
        "repo_id": repo_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "message": "Test payload from Synapse GitHub connector"
    }
    
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    
    secret = row["secret"]
    if secret:
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        headers["X-Synapse-Signature"] = f"sha256={sig}"
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(row["webhook_url"], content=body, headers=headers)
        return {"status": "sent", "response_code": resp.status_code}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to send test payload: {e}")


webhook_router = APIRouter(prefix="/github", tags=["github"])

@webhook_router.post("/webhook", status_code=202)
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_github_event: str = Header(None),
    x_github_delivery: str = Header(None),
    x_hub_signature_256: str = Header(None),
    state: AppState = Depends(get_state)
):
    """Handle incoming GitHub webhooks."""
    if not x_hub_signature_256:
        raise HTTPException(status_code=401, detail="Missing signature")

    payload = await request.body()
    secret = state.settings.github_webhook_secret or ""
    
    # Validate signature
    mac = hmac.new(secret.encode("utf-8"), msg=payload, digestmod=hashlib.sha256)
    expected_sig = f"sha256={mac.hexdigest()}"
    if not hmac.compare_digest(expected_sig, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid signature")

    data = await request.json()
    
    # Idempotency check
    now = datetime.now(UTC).isoformat()
    try:
        await state.db.execute(
            """
            INSERT INTO github_webhook_events (delivery_id, event_type, payload_json, received_at)
            VALUES (?, ?, ?, ?)
            """,
            (x_github_delivery, x_github_event, payload.decode("utf-8"), now)
        )
    except Exception as e:
        # Check for constraint violations (duplicate delivery_id)
        if "UNIQUE" in str(e) or "constraint" in str(e).lower():
            return {"message": "Already processed"}
        raise

    if x_github_event in ("push", "pull_request", "issues", "issue_comment", "pull_request_review"):
        repo_data = data.get("repository", {})
        github_repo_id = str(repo_data.get("id"))
        
        row = await state.db.fetch_one(
            "SELECT id FROM github_repositories WHERE github_repo_id = ?",
            (github_repo_id,)
        )
        if row:
            repo_id = row["id"]
            
            # Enqueue sync if not already running
            running = await state.db.fetch_one(
                "SELECT id FROM github_sync_runs WHERE repo_id = ? AND status = 'SYNCING'",
                (repo_id,)
            )
            if not running:
                from github.connector import GitHubSyncJob
                job = GitHubSyncJob(state, repo_id)
                background_tasks.add_task(job.run)

    return {"message": "Accepted"}
