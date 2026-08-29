"""Tenant configuration, ACL groups, API keys and delivery settings."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.state import AppState, get_state

router = APIRouter()

DEFAULT_TENANT_CONFIG = {
    "tenant_id": "tenant-a",
    "display_name": "Tenant A",
    "isolation_mode": "group_id",
    "retention_days": 365,
    "default_access_tag": "internal",
}

import os

DEFAULT_DELIVERY = {
    "endpoint": (
        os.getenv("AGENTSUITE_SYNC_URL")
        or os.getenv("SYNAPSE_AGENTSUITE_SYNC_URL")
        or os.getenv("SYNAPSE_DELIVERY_ENDPOINT")
        or "http://localhost:8001/api/v1/graph/sync"
    ),
    "format": "json.gz",
    "notify_on_publish": True,
    "auth_header": "",
}


class TenantConfig(BaseModel):
    tenant_id: str
    display_name: str
    isolation_mode: str = "group_id"
    retention_days: int = 365
    default_access_tag: str = "internal"


class DeliveryConfig(BaseModel):
    endpoint: str = ""
    format: str = "json.gz"
    notify_on_publish: bool = True
    auth_header: str = ""


class AclGroupRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""


class ApiKeyRequest(BaseModel):
    name: str = Field(min_length=1)
    environment: str = "staging"


@router.get("/settings/tenant")
async def get_tenant(state: AppState = Depends(get_state)) -> dict:
    stored = await state.db.get_setting("tenant", None)
    if stored:
        return stored
    return {**DEFAULT_TENANT_CONFIG, "tenant_id": state.settings.default_tenant}


@router.put("/settings/tenant")
async def put_tenant(config: TenantConfig, state: AppState = Depends(get_state)) -> dict:
    payload = config.model_dump()
    await state.db.set_setting("tenant", payload)
    return payload


@router.get("/settings/delivery")
async def get_delivery(state: AppState = Depends(get_state)) -> dict:
    return await state.db.get_setting("delivery", DEFAULT_DELIVERY)


@router.put("/settings/delivery")
async def put_delivery(config: DeliveryConfig, state: AppState = Depends(get_state)) -> dict:
    payload = config.model_dump()
    await state.db.set_setting("delivery", payload)
    return payload


@router.get("/settings/acl-groups")
async def list_acl_groups(state: AppState = Depends(get_state)) -> list[dict]:
    rows = await state.db.fetch_all("SELECT * FROM acl_groups ORDER BY name")
    return [{"id": r["id"], "name": r["name"], "description": r["description"]} for r in rows]


@router.post("/settings/acl-groups")
async def create_acl_group(
    request: AclGroupRequest, state: AppState = Depends(get_state)
) -> dict:
    group_id = f"acl-{uuid.uuid4().hex[:8]}"
    await state.db.execute(
        "INSERT INTO acl_groups(id, name, description) VALUES(?, ?, ?)",
        (group_id, request.name, request.description),
    )
    return {"id": group_id, "name": request.name, "description": request.description}


@router.delete("/settings/acl-groups/{group_id}")
async def delete_acl_group(group_id: str, state: AppState = Depends(get_state)) -> dict:
    row = await state.db.fetch_one("SELECT id FROM acl_groups WHERE id = ?", (group_id,))
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown ACL group: {group_id}")
    await state.db.execute("DELETE FROM acl_groups WHERE id = ?", (group_id,))
    return {"id": group_id, "status": "deleted"}


@router.get("/settings/api-keys")
async def list_api_keys(state: AppState = Depends(get_state)) -> list[dict]:
    """Only the masked form is ever returned; the plaintext key is never stored."""
    rows = await state.db.fetch_all("SELECT * FROM api_keys ORDER BY created_at DESC")
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "maskedKey": row["masked_key"],
            "environment": row["environment"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


@router.post("/settings/api-keys")
async def create_api_key(request: ApiKeyRequest, state: AppState = Depends(get_state)) -> dict:
    """Return the plaintext key exactly once, at creation."""
    key_id = f"key-{uuid.uuid4().hex[:8]}"
    plaintext = f"syn_{secrets.token_urlsafe(32)}"
    masked = f"{plaintext[:8]}{'•' * 16}{plaintext[-4:]}"

    await state.db.execute(
        "INSERT INTO api_keys(id, name, masked_key, key_hash, environment, created_at) "
        "VALUES(?, ?, ?, ?, ?, ?)",
        (
            key_id,
            request.name,
            masked,
            hashlib.sha256(plaintext.encode()).hexdigest(),
            request.environment,
            datetime.now(UTC).isoformat(),
        ),
    )
    return {
        "id": key_id,
        "name": request.name,
        "maskedKey": masked,
        "environment": request.environment,
        # Shown once and never retrievable again.
        "key": plaintext,
    }


@router.delete("/settings/api-keys/{key_id}")
async def revoke_api_key(key_id: str, state: AppState = Depends(get_state)) -> dict:
    row = await state.db.fetch_one("SELECT id FROM api_keys WHERE id = ?", (key_id,))
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown API key: {key_id}")
    await state.db.execute("DELETE FROM api_keys WHERE id = ?", (key_id,))
    return {"id": key_id, "status": "revoked"}
