"""Gmail REST API client using OAuth2 access tokens.

Uses plain httpx — no heavy Google SDK required.
Handles token refresh automatically before expiry.
"""
from __future__ import annotations

import base64
import email
import logging
from datetime import UTC, datetime, timedelta
from email import policy as email_policy
from typing import Any

import httpx

logger = logging.getLogger("synapse.mail.gmail")

GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"


class GmailClient:
    """Thin async Gmail REST client for a single OAuth2 account."""

    def __init__(
        self,
        access_token: str,
        refresh_token: str,
        token_expiry: str | None,
        client_id: str,
        client_secret: str,
    ) -> None:
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.token_expiry = token_expiry  # ISO string
        self.client_id = client_id
        self.client_secret = client_secret

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    def _is_expired(self) -> bool:
        if not self.token_expiry:
            return True
        try:
            expiry = datetime.fromisoformat(self.token_expiry)
            return datetime.now(UTC) >= expiry - timedelta(minutes=5)
        except ValueError:
            return True

    async def _ensure_token(self) -> None:
        if not self._is_expired():
            return
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                OAUTH_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            self.access_token = data["access_token"]
            expires_in = data.get("expires_in", 3600)
            self.token_expiry = (
                datetime.now(UTC) + timedelta(seconds=expires_in)
            ).isoformat()
            logger.debug("Gmail access token refreshed")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    # ------------------------------------------------------------------
    # Gmail API methods
    # ------------------------------------------------------------------

    async def get_profile(self) -> dict[str, Any]:
        """Get the authenticated user's Gmail profile."""
        await self._ensure_token()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{GMAIL_BASE}/users/me/profile", headers=self._headers()
            )
            resp.raise_for_status()
            return resp.json()

    async def list_message_ids(
        self,
        after_date: datetime | None = None,
        label_ids: list[str] | None = None,
        page_token: str | None = None,
        max_results: int = 500,
    ) -> tuple[list[str], str | None]:
        """Return (message_ids, next_page_token)."""
        await self._ensure_token()
        params: dict[str, Any] = {"maxResults": max_results}
        q_parts = []
        if after_date:
            epoch = int(after_date.timestamp())
            q_parts.append(f"after:{epoch}")
        if label_ids:
            params["labelIds"] = label_ids
        if q_parts:
            params["q"] = " ".join(q_parts)
        if page_token:
            params["pageToken"] = page_token

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{GMAIL_BASE}/users/me/messages",
                headers=self._headers(),
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
        ids = [m["id"] for m in data.get("messages", [])]
        next_token = data.get("nextPageToken")
        return ids, next_token

    async def get_message(self, message_id: str) -> dict[str, Any]:
        """Fetch a full message."""
        await self._ensure_token()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{GMAIL_BASE}/users/me/messages/{message_id}",
                headers=self._headers(),
                params={"format": "full"},
            )
            resp.raise_for_status()
            return resp.json()

    async def get_thread(self, thread_id: str) -> dict[str, Any]:
        """Fetch an entire thread with all messages."""
        await self._ensure_token()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{GMAIL_BASE}/users/me/threads/{thread_id}",
                headers=self._headers(),
                params={"format": "full"},
            )
            resp.raise_for_status()
            return resp.json()

    async def list_labels(self) -> list[dict[str, Any]]:
        """List all labels for the account."""
        await self._ensure_token()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{GMAIL_BASE}/users/me/labels", headers=self._headers()
            )
            resp.raise_for_status()
            return resp.json().get("labels", [])

    async def get_history(
        self, start_history_id: str, label_ids: list[str] | None = None
    ) -> tuple[list[str], str]:
        """
        Incremental fetch using the Gmail History API.
        Returns (list_of_new_message_ids, new_history_id).
        Raises httpx.HTTPStatusError with 404 if history is too old (full re-sync needed).
        """
        await self._ensure_token()
        params: dict[str, Any] = {
            "startHistoryId": start_history_id,
            "historyTypes": ["messageAdded"],
        }
        if label_ids:
            params["labelId"] = label_ids[0]

        message_ids: list[str] = []
        next_page_token: str | None = None
        new_history_id = start_history_id

        async with httpx.AsyncClient(timeout=30.0) as client:
            while True:
                if next_page_token:
                    params["pageToken"] = next_page_token
                resp = await client.get(
                    f"{GMAIL_BASE}/users/me/history",
                    headers=self._headers(),
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()
                new_history_id = data.get("historyId", new_history_id)
                for record in data.get("history", []):
                    for added in record.get("messagesAdded", []):
                        message_ids.append(added["message"]["id"])
                next_page_token = data.get("nextPageToken")
                if not next_page_token:
                    break

        return list(dict.fromkeys(message_ids)), new_history_id  # dedup, preserve order


# ------------------------------------------------------------------
# Message parsing helpers
# ------------------------------------------------------------------

def _get_header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _decode_body_part(part: dict) -> str:
    """Recursively extract plain-text body from a Gmail message part."""
    mime_type = part.get("mimeType", "")
    if mime_type == "text/plain":
        data = part.get("body", {}).get("data", "")
        if data:
            try:
                return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
            except Exception:
                return ""
    if mime_type.startswith("multipart/"):
        parts = []
        for sub in part.get("parts", []):
            text = _decode_body_part(sub)
            if text:
                parts.append(text)
        return "\n".join(parts)
    return ""


def message_to_text(msg: dict) -> str:
    """Convert a Gmail API message dict to a structured plain-text string."""
    headers = msg.get("payload", {}).get("headers", [])
    subject = _get_header(headers, "Subject") or "(no subject)"
    sender = _get_header(headers, "From") or "unknown"
    to = _get_header(headers, "To") or ""
    cc = _get_header(headers, "Cc") or ""
    date_str = _get_header(headers, "Date") or ""
    labels = ", ".join(msg.get("labelIds", []))

    body = _decode_body_part(msg.get("payload", {}))
    body = body.strip()[:8000]  # cap at 8k chars per message

    lines = [
        f"Subject: {subject}",
        f"From: {sender}",
        f"To: {to}",
    ]
    if cc:
        lines.append(f"Cc: {cc}")
    if date_str:
        lines.append(f"Date: {date_str}")
    if labels:
        lines.append(f"Labels: {labels}")
    lines.append("")
    lines.append(body)
    return "\n".join(lines)


def thread_to_text(thread: dict) -> str:
    """Combine all messages in a thread into a structured document."""
    messages = thread.get("messages", [])
    if not messages:
        return ""

    first_headers = messages[0].get("payload", {}).get("headers", [])
    subject = _get_header(first_headers, "Subject") or "(no subject)"

    parts = [f"=== Email Thread: {subject} ===\n"]
    for i, msg in enumerate(messages):
        parts.append(f"--- Message {i + 1} ---")
        parts.append(message_to_text(msg))
        parts.append("")

    return "\n".join(parts)
