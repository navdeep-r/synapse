"""Mail connector API endpoints.

Handles:
- Gmail OAuth2 flow (URL generation + callback token exchange)
- CRUD for mail_accounts
- Manual sync trigger
- Sync run history
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from app.state import AppState, get_state

router = APIRouter(prefix="/mail", tags=["mail"])
logger = logging.getLogger("synapse.api.mail")

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.metadata",
    "openid",
    "email",
    "profile",
]


# ── Request / response shapes ────────────────────────────────────────────────

class MailAccountResponse(BaseModel):
    id: str
    provider: str
    email_address: str
    display_name: str | None
    status: str
    last_synced_at: str | None
    poll_interval_minutes: int
    max_history_days: int
    label_filter: str | None
    error_message: str | None
    created_at: str


class UpdateMailAccountRequest(BaseModel):
    poll_interval_minutes: int | None = None
    max_history_days: int | None = None
    label_filter: str | None = None
    status: str | None = None  # 'ACTIVE' | 'PAUSED'


# ── OAuth flow ───────────────────────────────────────────────────────────────

@router.get("/oauth/gmail/url")
async def gmail_oauth_url(state: AppState = Depends(get_state)) -> dict[str, str]:
    """Return the Google OAuth2 consent screen URL."""
    client_id = state.settings.mail_google_client_id
    if not client_id:
        raise HTTPException(
            status_code=503,
            detail="SYNAPSE_MAIL_GOOGLE_CLIENT_ID not configured. Add it to .env",
        )
    import urllib.parse
    params = {
        "client_id": client_id,
        "redirect_uri": state.settings.mail_google_redirect_uri,
        "response_type": "code",
        "scope": " ".join(GMAIL_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    }
    url = f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"
    return {"url": url}


class OAuthCallbackRequest(BaseModel):
    code: str


@router.post("/oauth/gmail/callback")
async def gmail_oauth_callback(
    body: OAuthCallbackRequest,
    state: AppState = Depends(get_state),
) -> MailAccountResponse:
    """Exchange OAuth code for tokens and persist the mail account."""
    client_id = state.settings.mail_google_client_id
    client_secret = state.settings.mail_google_client_secret
    redirect_uri = state.settings.mail_google_redirect_uri

    if not client_id or not client_secret:
        raise HTTPException(status_code=503, detail="Gmail OAuth not configured")

    # Exchange code for tokens
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": body.code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if resp.status_code != 200:
            raise HTTPException(
                status_code=400,
                detail=f"Google token exchange failed: {resp.text}",
            )
        token_data = resp.json()

    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token", "")
    expires_in = token_data.get("expires_in", 3600)
    token_expiry = (
        datetime.now(UTC).replace(microsecond=0).isoformat()
    )

    # Get email address from Google
    async with httpx.AsyncClient(timeout=15.0) as client:
        info_resp = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        info_resp.raise_for_status()
        user_info = info_resp.json()

    email_address = user_info.get("email", "")
    display_name = user_info.get("name", email_address)

    tenant = state.settings.default_tenant

    # Upsert account
    existing = await state.db.fetch_one(
        "SELECT id FROM mail_accounts WHERE tenant_id=? AND email_address=?",
        (tenant, email_address),
    )
    now = datetime.now(UTC).isoformat()
    poll_minutes = state.settings.mail_default_poll_interval_minutes

    if existing:
        account_id = existing["id"]
        await state.db.execute(
            """
            UPDATE mail_accounts
            SET access_token=?, refresh_token=?, token_expiry=?,
                display_name=?, status='ACTIVE', error_message=NULL,
                updated_at=?
            WHERE id=?
            """,
            (access_token, refresh_token, token_expiry, display_name, now, account_id),
        )
    else:
        account_id = str(uuid.uuid4())
        await state.db.execute(
            """
            INSERT INTO mail_accounts (
                id, tenant_id, provider, email_address, display_name,
                access_token, refresh_token, token_expiry,
                poll_interval_minutes, max_history_days,
                status, created_at, updated_at
            ) VALUES (?, ?, 'gmail', ?, ?, ?, ?, ?, ?, 90, 'ACTIVE', ?, ?)
            """,
            (
                account_id, tenant, email_address, display_name,
                access_token, refresh_token, token_expiry,
                poll_minutes, now, now,
            ),
        )

    # Kick off an immediate full sync in the background
    from mail.connector import MailSyncJob
    asyncio.create_task(MailSyncJob(state, account_id).run())

    row = await state.db.fetch_one("SELECT * FROM mail_accounts WHERE id=?", (account_id,))
    return _row_to_response(row)


# ── Account CRUD ─────────────────────────────────────────────────────────────

@router.get("/accounts")
async def list_accounts(state: AppState = Depends(get_state)) -> list[MailAccountResponse]:
    tenant = state.settings.default_tenant
    rows = await state.db.fetch_all(
        "SELECT * FROM mail_accounts WHERE tenant_id=? ORDER BY created_at DESC",
        (tenant,),
    )
    return [_row_to_response(r) for r in rows]


@router.get("/accounts/{account_id}")
async def get_account(
    account_id: str, state: AppState = Depends(get_state)
) -> MailAccountResponse:
    row = await state.db.fetch_one(
        "SELECT * FROM mail_accounts WHERE id=?", (account_id,)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Account not found")
    return _row_to_response(row)


@router.patch("/accounts/{account_id}")
async def update_account(
    account_id: str,
    body: UpdateMailAccountRequest,
    state: AppState = Depends(get_state),
) -> MailAccountResponse:
    row = await state.db.fetch_one(
        "SELECT * FROM mail_accounts WHERE id=?", (account_id,)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Account not found")

    updates: list[str] = []
    params: list[Any] = []
    for field, value in body.model_dump(exclude_none=True).items():
        updates.append(f"{field}=?")
        params.append(value)

    if updates:
        updates.append("updated_at=?")
        params.append(datetime.now(UTC).isoformat())
        params.append(account_id)
        await state.db.execute(
            f"UPDATE mail_accounts SET {', '.join(updates)} WHERE id=?", params
        )

    updated = await state.db.fetch_one(
        "SELECT * FROM mail_accounts WHERE id=?", (account_id,)
    )
    return _row_to_response(updated)


@router.delete("/accounts/{account_id}", status_code=204)
async def delete_account(
    account_id: str, state: AppState = Depends(get_state)
) -> None:
    row = await state.db.fetch_one(
        "SELECT id FROM mail_accounts WHERE id=?", (account_id,)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Account not found")
    await state.db.execute("DELETE FROM mail_sync_runs WHERE account_id=?", (account_id,))
    await state.db.execute("DELETE FROM mail_accounts WHERE id=?", (account_id,))


# ── Sync control ─────────────────────────────────────────────────────────────

@router.post("/accounts/{account_id}/sync")
async def trigger_sync(
    account_id: str,
    background_tasks: BackgroundTasks,
    state: AppState = Depends(get_state),
) -> dict[str, str]:
    """Manually trigger an immediate sync for a mail account."""
    row = await state.db.fetch_one(
        "SELECT id FROM mail_accounts WHERE id=?", (account_id,)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Account not found")

    running = await state.db.fetch_one(
        "SELECT id FROM mail_sync_runs WHERE account_id=? AND status='SYNCING'",
        (account_id,),
    )
    if running:
        raise HTTPException(status_code=409, detail="Sync already in progress")

    from mail.connector import MailSyncJob
    background_tasks.add_task(MailSyncJob(state, account_id).run)
    return {"status": "queued", "account_id": account_id}


@router.get("/accounts/{account_id}/runs")
async def list_sync_runs(
    account_id: str,
    state: AppState = Depends(get_state),
    limit: int = 20,
) -> list[dict[str, Any]]:
    """List recent sync runs for an account."""
    rows = await state.db.fetch_all(
        "SELECT * FROM mail_sync_runs WHERE account_id=? ORDER BY started_at DESC LIMIT ?",
        (account_id, limit),
    )
    return [dict(r) for r in rows]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row_to_response(row: dict) -> MailAccountResponse:
    return MailAccountResponse(
        id=row["id"],
        provider=row["provider"],
        email_address=row["email_address"],
        display_name=row.get("display_name"),
        status=row["status"],
        last_synced_at=row.get("last_synced_at"),
        poll_interval_minutes=row.get("poll_interval_minutes", 15),
        max_history_days=row.get("max_history_days", 90),
        label_filter=row.get("label_filter"),
        error_message=row.get("error_message"),
        created_at=row["created_at"],
    )
