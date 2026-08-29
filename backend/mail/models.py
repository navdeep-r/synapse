"""Mail integration models."""
from __future__ import annotations
from pydantic import BaseModel
from typing import Optional


class MailAccount(BaseModel):
    id: str
    tenant_id: str
    user_id: Optional[str] = None
    provider: str  # 'gmail' | 'imap'
    email_address: str
    display_name: Optional[str] = None
    poll_interval_minutes: int = 15
    max_history_days: int = 90
    label_filter: Optional[str] = None
    status: str = "ACTIVE"
    last_synced_at: Optional[str] = None
    error_message: Optional[str] = None
    created_at: str
    updated_at: str


class MailSyncRun(BaseModel):
    id: str
    account_id: str
    status: str
    messages_fetched: int = 0
    chunks_ingested: int = 0
    started_at: str
    finished_at: Optional[str] = None
    error: Optional[str] = None
