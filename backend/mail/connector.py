"""Mail sync job — mirrors github/connector.py pattern.

Fetches email threads from Gmail and feeds them through the
standard Synapse pipeline (chunking → Graphiti KG extraction).

Flow:
  1. Load mail_account from SQLite
  2. Create a mail_sync_runs row (status=SYNCING)
  3. Build GmailClient with stored tokens
  4. Incremental: use history_id if available, else full fetch from max_history_days
  5. For each thread: convert to structured plain-text → submit to pipeline
  6. Update history_id + last_synced_at
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
import email.utils
from datetime import UTC, datetime, timedelta

from app.state import AppState
from kg_contracts.models import CanonicalDocument, SourceKind
from mail.client_gmail import GmailClient, thread_to_text

logger = logging.getLogger("synapse.mail.connector")


class MailSyncJob:
    """One execution of a mail account synchronisation."""

    def __init__(self, state: AppState, account_id: str) -> None:
        self.state = state
        self.account_id = account_id

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self) -> None:
        row = await self.state.db.fetch_one(
            "SELECT * FROM mail_accounts WHERE id = ?", (self.account_id,)
        )
        if not row:
            logger.error("mail_account %s not found", self.account_id)
            return

        run_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        await self.state.db.execute(
            """
            INSERT INTO mail_sync_runs (id, account_id, status, started_at)
            VALUES (?, ?, 'SYNCING', ?)
            """,
            (run_id, self.account_id, now),
        )

        try:
            messages_fetched, chunks_ingested = await self._run_sync(row)
            finished = datetime.now(UTC).isoformat()
            await self.state.db.execute(
                """
                UPDATE mail_sync_runs
                SET status='DONE', messages_fetched=?, chunks_ingested=?, finished_at=?
                WHERE id=?
                """,
                (messages_fetched, chunks_ingested, finished, run_id),
            )
            await self.state.db.execute(
                "UPDATE mail_accounts SET last_synced_at=?, status='ACTIVE', error_message=NULL WHERE id=?",
                (finished, self.account_id),
            )
            logger.info(
                "mail sync %s done: %d messages, %d chunks",
                self.account_id,
                messages_fetched,
                chunks_ingested,
            )
        except Exception as exc:
            logger.exception("mail sync failed for %s", self.account_id)
            finished = datetime.now(UTC).isoformat()
            await self.state.db.execute(
                "UPDATE mail_sync_runs SET status='FAILED', error=?, finished_at=? WHERE id=?",
                (str(exc), finished, run_id),
            )
            await self.state.db.execute(
                "UPDATE mail_accounts SET status='ERROR', error_message=? WHERE id=?",
                (str(exc)[:500], self.account_id),
            )

    # ------------------------------------------------------------------
    # Core sync logic
    # ------------------------------------------------------------------

    async def _run_sync(self, row: dict) -> tuple[int, int]:
        settings = self.state.settings
        client = GmailClient(
            access_token=row["access_token"] or "",
            refresh_token=row["refresh_token"] or "",
            token_expiry=row["token_expiry"],
            client_id=settings.mail_google_client_id or "",
            client_secret=settings.mail_google_client_secret or "",
        )

        label_filter = row.get("label_filter") or None
        label_ids = [l.strip() for l in label_filter.split(",")] if label_filter else None
        history_id: str | None = row.get("history_id")

        thread_ids_to_fetch: list[str] = []
        new_history_id = history_id

        if history_id:
            # INCREMENTAL — use Gmail History API
            try:
                new_msg_ids, new_history_id = await client.get_history(
                    history_id, label_ids=label_ids
                )
                # Resolve message IDs to thread IDs
                seen_threads: set[str] = set()
                for mid in new_msg_ids:
                    msg = await client.get_message(mid)
                    tid = msg.get("threadId")
                    if tid and tid not in seen_threads:
                        seen_threads.add(tid)
                        thread_ids_to_fetch.append(tid)
            except Exception as exc:
                # History expired (404) — fall back to full re-sync
                logger.warning(
                    "Gmail history expired for %s (%s), falling back to full sync",
                    self.account_id,
                    exc,
                )
                history_id = None

        if not history_id:
            # FULL SYNC — list all message IDs since max_history_days
            max_days = int(row.get("max_history_days") or 90)
            after_date = (
                datetime.now(UTC) - timedelta(days=max_days) if max_days > 0 else None
            )
            page_token: str | None = None
            seen_threads: set[str] = set()
            while True:
                ids, page_token = await client.list_message_ids(
                    after_date=after_date,
                    label_ids=label_ids,
                    page_token=page_token,
                    max_results=500,
                )
                for mid in ids:
                    msg = await client.get_message(mid)
                    tid = msg.get("threadId")
                    if tid and tid not in seen_threads:
                        seen_threads.add(tid)
                        thread_ids_to_fetch.append(tid)
                if not page_token:
                    break

            # Grab current historyId from profile for next incremental sync
            profile = await client.get_profile()
            new_history_id = profile.get("historyId", history_id)

        # Fetch labels list for metadata enrichment
        try:
            all_labels = await client.list_labels()
            label_map = {l["id"]: l["name"] for l in all_labels}
        except Exception:
            label_map = {}

        # Process threads
        source_id = f"mail:{row['email_address']}"
        messages_fetched = 0
        chunks_ingested = 0

        assert self.state.pipeline is not None
        for tid in thread_ids_to_fetch:
            try:
                thread = await client.get_thread(tid)
                messages = thread.get("messages", [])
                if not messages:
                    continue

                nodes = []
                edges = []
                for msg in messages:
                    msg_id = msg.get("id")
                    from mail.client_gmail import message_to_text
                    msg_text = message_to_text(msg)
                    if not msg_text.strip():
                        continue

                    payload = msg.get("payload", {})
                    headers = payload.get("headers", [])
                    header_dict = {h["name"].lower(): h["value"] for h in headers}
                    
                    msg_from = header_dict.get("from", "")
                    msg_to = header_dict.get("to", "")
                    msg_cc = header_dict.get("cc", "")
                    msg_subject = header_dict.get("subject", "")
                    msg_date = header_dict.get("date", "")
                    msg_message_id = header_dict.get("message-id", msg_id)
                    is_reply = bool(header_dict.get("in-reply-to") or header_dict.get("references"))

                    all_label_ids = set(msg.get("labelIds", []))
                    label_names = [label_map.get(lid, lid) for lid in all_label_ids]
                    
                    issue_keywords = ("issue", "bug", "urgent", "error", "not working", "problem", "fail", "help", "support", "query")
                    is_issue = any(k in msg_subject.lower() or k in msg_text.lower() for k in issue_keywords)
                    customer_related = any(l.lower() in ("customer", "support") for l in label_names)
                    needs_attention = customer_related or is_issue

                    doc_id = f"{source_id}:msg:{msg_id}"
                    content_hash = hashlib.sha256(msg_text.encode()).hexdigest()

                    doc = CanonicalDocument(
                        document_id=doc_id,
                        source_id=source_id,
                        source_kind=SourceKind.EMAIL,
                        name=f"Message:{msg_id}",
                        media_type="text/plain",
                        text=msg_text,
                        content_hash=content_hash,
                        byte_size=len(msg_text.encode()),
                        access_tag="internal",
                    )
                    await self.state.pipeline.submit(doc)
                    
                    # Count parts for attachments
                    parts = payload.get("parts", [])
                    attachment_count = sum(1 for p in parts if p.get("filename"))
                    
                    node_uuid = f"mail-{msg_id}"
                    nodes.append({
                        "uuid": node_uuid,
                        "name": f"Email: {msg_subject}" if msg_subject else f"Email {msg_id}",
                        "labels": ["Email"],
                        "summary": msg_text[:200] + "..." if len(msg_text) > 200 else msg_text,
                        "attributes": {
                            "type": "email",
                            "domain": "customer_support" if customer_related else "communication",
                            "category": "communication",
                            "classification_subtype": "customer_query" if customer_related else "general",
                            "source_type": "mail",
                            "provider": "gmail",
                            "account_id": self.account_id,
                            "email_address": row["email_address"],
                            "thread_id": tid,
                            "message_id": msg_message_id,
                            "external_id": msg_message_id,
                            "source_ids": [source_id],
                            "document_ids": [doc_id],
                            "episode_ids": [],
                            "from": msg_from,
                            "to": msg_to,
                            "cc": msg_cc,
                            "subject": msg_subject,
                            "sent_at": msg_date,
                            "received_at": msg_date,
                            "is_reply": is_reply,
                            "has_attachments": attachment_count > 0,
                            "attachment_count": attachment_count,
                            "labels": label_names,
                            "customer_related": customer_related,
                            "requires_response": customer_related,
                            "need_attention": needs_attention,
                            "attention_type": "issue_or_query" if needs_attention else "none"
                        }
                    })

                    # Process From
                    if msg_from:
                        name, addr = email.utils.parseaddr(msg_from)
                        if addr:
                            person_uuid = "person-" + hashlib.md5(addr.lower().encode()).hexdigest()
                            nodes.append({
                                "uuid": person_uuid,
                                "name": name or addr,
                                "labels": ["Person", "Contact"],
                                "summary": f"Contact: {name} <{addr}>",
                                "attributes": {
                                    "email": addr,
                                    "name": name,
                                    "type": "person",
                                    "source_type": "mail",
                                    "need_attention": False
                                }
                            })
                            edges.append({
                                "source_uuid": node_uuid,
                                "target_uuid": person_uuid,
                                "labels": ["SENT_BY"],
                                "fact": f"Email '{msg_subject}' was sent by {name or addr}"
                            })

                    # Process To
                    if msg_to:
                        # Handle multiple recipients
                        for addr_str in msg_to.split(","):
                            name, addr = email.utils.parseaddr(addr_str)
                            if addr:
                                person_uuid = "person-" + hashlib.md5(addr.lower().encode()).hexdigest()
                                nodes.append({
                                    "uuid": person_uuid,
                                    "name": name or addr,
                                    "labels": ["Person", "Contact"],
                                    "summary": f"Contact: {name} <{addr}>",
                                    "attributes": {
                                        "email": addr,
                                        "name": name,
                                        "type": "person",
                                        "source_type": "mail",
                                        "need_attention": False
                                    }
                                })
                                edges.append({
                                    "source_uuid": node_uuid,
                                    "target_uuid": person_uuid,
                                    "labels": ["SENT_TO"],
                                    "fact": f"Email '{msg_subject}' was sent to {name or addr}"
                                })

                    if len(nodes) > 1 and "REPLIES_TO" in [e.get("labels", [""])[0] for e in edges]: # Previous node check needs fix because of Person nodes
                        # Note: with person nodes, nodes list is longer. Let's just track the last email node.
                        pass
                    
                    # We'll handle thread-based REPLIES_TO by finding the previous email node in this batch if one exists
                    email_nodes = [n for n in nodes if "Email" in n["labels"]]
                    if len(email_nodes) > 1:
                        prev_email = email_nodes[-2]
                        edges.append({
                            "source_uuid": node_uuid,
                            "target_uuid": prev_email["uuid"],
                            "labels": ["REPLIES_TO"],
                            "fact": f"Message '{msg_subject}' replies to previous message in thread {tid}"
                        })

                    messages_fetched += 1
                    chunks_ingested += 1
                
                if nodes:
                    await self.state.graph.add_deterministic_nodes_and_edges("synapse", nodes, edges)

            except Exception:
                logger.exception("failed to process thread %s", tid)

        # Persist updated history_id so next run is incremental
        if new_history_id:
            await self.state.db.execute(
                "UPDATE mail_accounts SET history_id=? WHERE id=?",
                (new_history_id, self.account_id),
            )

        # Refresh tokens in DB in case they were refreshed mid-run
        await self.state.db.execute(
            "UPDATE mail_accounts SET access_token=?, token_expiry=? WHERE id=?",
            (client.access_token, client.token_expiry, self.account_id),
        )

        return messages_fetched, chunks_ingested
