"""GitHub Connector synchronization logic."""

from __future__ import annotations

import json
import hashlib
import logging
import mimetypes
import uuid
import hmac
from datetime import UTC, datetime
from pathlib import Path

from app.state import AppState
from github.client import GitHubAppClient
from kg_contracts.models import CanonicalDocument, SourceKind

logger = logging.getLogger("synapse.github.connector")


from github.file_classifier import should_include_file, get_file_significance, FileSignificance


async def _push_to_agent_webhook(
    db, repo_id: str, event: str, payload: dict
) -> bool:
    """Push an event to the configured external agent webhook."""
    row = await db.fetch_one(
        "SELECT webhook_url, secret, events_json FROM github_agent_webhooks WHERE repo_id = ?",
        (repo_id,)
    )
    if not row:
        return False
    
    import json
    events = json.loads(row["events_json"] or "[]")
    if event not in events:
        return False
    
    webhook_url = row["webhook_url"]
    secret = row["secret"]
    
    payload["event"] = event
    payload["repo_id"] = repo_id
    payload["timestamp"] = datetime.now(UTC).isoformat()
    
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    
    if secret:
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        headers["X-Synapse-Signature"] = f"sha256={sig}"
    
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(webhook_url, content=body, headers=headers)
        if resp.status_code >= 400:
            logger.warning("Agent webhook returned %d: %s", resp.status_code, resp.text)
        return True
    except Exception as e:
        logger.warning("Failed to push to agent webhook: %s", e)
        return False


class GitHubSyncJob:
    """A single execution of a repository synchronization."""

    def __init__(self, state: AppState, repo_id: str):
        self.state = state
        self.repo_id = repo_id

    async def run(self) -> None:
        # Load repo
        repo_row = await self.state.db.fetch_one("SELECT * FROM github_repositories WHERE id = ?", (self.repo_id,))
        if not repo_row:
            logger.error("Repository %s not found.", self.repo_id)
            return

        run_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()

        await self.state.db.execute(
            """
            INSERT INTO github_sync_runs (
                id, repo_id, installation_id, started_at, status, trigger
            ) VALUES (?, ?, ?, ?, 'SYNCING', 'initial')
            """,
            (run_id, self.repo_id, repo_row["installation_id"], now)
        )

        try:
            await self._run_sync(repo_row, run_id)
            
            webhook_url = self.state.settings.github_external_agent_webhook_url
            if webhook_url:
                import httpx
                payload = {
                    "event": "sync_completed",
                    "repo_id": self.repo_id,
                    "run_id": run_id,
                    "repo_name": repo_row["full_name"],
                    "timestamp": datetime.now(UTC).isoformat()
                }
                async with httpx.AsyncClient() as hc:
                    try:
                        await hc.post(webhook_url, json=payload, timeout=5.0)
                    except Exception:
                        logger.warning("Failed to dispatch webhook to %s", webhook_url)
                        
        except Exception as e:
            logger.exception("GitHub sync failed for %s", self.repo_id)
            await self.state.db.execute(
                "UPDATE github_sync_runs SET status = 'ERROR', error = ?, completed_at = ? WHERE id = ?",
                (str(e), datetime.now(UTC).isoformat(), run_id)
            )
            await self.state.db.execute("UPDATE github_repositories SET status = 'ERROR' WHERE id = ?", (self.repo_id,))

    async def _ensure_project_exists(self, repo_row: dict) -> str:
        project_id = f"project-{repo_row['name']}"
        existing = await self.state.db.fetch_one(
            "SELECT id FROM projects WHERE id = ?", (project_id,)
        )
        now = datetime.now(UTC).isoformat()
        if not existing:
            await self.state.db.execute(
                """INSERT INTO projects (id, name, type, source_type, source_identifier, 
                   display_name, description, repo_url, repo_full_name, repo_id, default_branch, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (project_id, repo_row["name"], "github_repo", "github",
                 f"github:repo:{repo_row['github_repo_id']}",
                 repo_row["name"], repo_row.get("description"),
                 f"https://github.com/{repo_row['full_name']}",
                 repo_row["full_name"], repo_row["github_repo_id"],
                 repo_row.get("default_branch", "main"),
                 now, now)
            )
        await self.state.db.execute(
            "INSERT OR IGNORE INTO project_repositories (project_id, repo_id) VALUES (?, ?)",
            (project_id, str(self.repo_id))
        )
        return project_id

    def _build_rich_metadata(self, repo_row: dict, project_id: str, file_path: str = None, sha: str = None, is_code: bool = True) -> dict:
        repo_full_name = repo_row["full_name"]
        meta = {
            # REQUIRED FLAT ATTRIBUTES (Spec §6, §10)
            "source_type": "github",
            "domain": "engineering",
            "category": "code" if is_code else "document",
            "type": "github_file" if file_path else "github_repo",
            "owner": repo_row["owner"],
            "repository": repo_row["name"],
            "branch": repo_row.get("default_branch", "main"),
            "source_ids": [f"github:repo:{repo_row['github_repo_id']}"],
            "document_ids": [],
            "episode_ids": [],
            
            # Classification
            "source_category": "code" if is_code else "document",
            "source_subtype": "source_file" if is_code else "document",
            "is_code": is_code,
            "is_document": not is_code,
            "is_github": True,
            "is_upload": False,
            "is_email": False,
            
            # Context
            "github_repo_id": repo_row["github_repo_id"],
            "full_name": repo_full_name,
            "url": f"https://github.com/{repo_full_name}",
            "is_private": bool(repo_row.get("private", True)),
            "project_identifier": project_id,
            "project_name": repo_row["name"],
            "project_type": "github_repo"
        }
        
        if file_path:
            meta["path"] = file_path
        if sha:
            meta["sha"] = sha
            
        return meta

    async def _process_code_intelligence(self, path: str, text: str, repo_id: str, group_id: str, rich_meta: dict = None) -> None:
        try:
            from github.ast_parsers import parse_code_file
            import asyncio
            nodes, edges = await asyncio.to_thread(parse_code_file, text, path, str(repo_id), group_id)
            
            if rich_meta and nodes:
                for n in nodes:
                    merged = rich_meta.copy()
                    merged.update(n.get("attributes", {}))
                    n["attributes"] = merged
                    
            if nodes or edges:
                await self.state.graph.add_deterministic_nodes_and_edges(group_id, nodes, edges)
        except Exception:
            logger.exception("Failed to run code intelligence on %s", path)

    async def _run_sync(self, repo_row: dict, run_id: str) -> None:
        client = GitHubAppClient(
            app_id=self.state.settings.github_app_id or "",
            private_key=self.state.settings.github_app_private_key or ""
        )

        owner = repo_row["owner"]
        name = repo_row["name"]
        installation_id = repo_row["installation_id"]
        last_synced_commit_sha = repo_row.get("last_synced_commit_sha")

        # 1. Get HEAD
        head_sha = await client.get_branch_head(installation_id, owner, name, repo_row["default_branch"])

        if head_sha == last_synced_commit_sha:
            now = datetime.now(UTC).isoformat()
            await self.state.db.execute(
                """
                UPDATE github_sync_runs 
                SET status = 'SUCCESS', completed_at = ?, files_added = 0, commits_processed = 0
                WHERE id = ?
                """,
                (now, run_id)
            )
            return

        files_added = 0
        commits_processed = 0
        source_id = f"github:repo:{repo_row['github_repo_id']}"
        
        group_id = "synapse"
        project_id = await self._ensure_project_exists(repo_row)
        rich_meta = self._build_rich_metadata(repo_row, project_id, is_code=True)

        from github.batch_helper import process_file_batch

        batch_files = []  # shared across both sync paths

        if last_synced_commit_sha:
            # INCREMENTAL SYNC
            comparison = await client.compare_commits(installation_id, owner, name, last_synced_commit_sha, head_sha)
            for f in comparison.get("files", []):
                path = f["filename"]
                if not should_include_file(path):
                    continue
                    
                status = f.get("status")
                if status == "removed":
                    doc_id = f"{source_id}:file:{path}"
                    doc = CanonicalDocument(
                        document_id=doc_id,
                        source_id=source_id,
                        source_kind=SourceKind.GITHUB,
                        name=path,
                        media_type="text/plain",
                        text="[DELETED]",
                        content_hash=hashlib.sha256(b"").hexdigest(),
                        byte_size=0,
                        access_tag="internal"
                    )
                    assert self.state.pipeline is not None
                    await self.state.pipeline.submit(doc)
                    
                    now = datetime.now(UTC).isoformat()
                    await self.state.db.execute(
                        "UPDATE github_file_state SET status = 'DELETED', last_synced_at = ? WHERE repo_id = ? AND file_path = ?",
                        (now, self.repo_id, path)
                    )
                    continue

                if status == "renamed":
                    prev_path = f.get("previous_filename")
                    if prev_path:
                        # Fetch the original entity_uuid
                        row = await self.state.db.fetch_one(
                            "SELECT entity_uuid FROM github_file_state WHERE repo_id = ? AND file_path = ?",
                            (self.repo_id, prev_path)
                        )
                        if row and row["entity_uuid"]:
                            entity_uuid = row["entity_uuid"]
                            # Update metadata in graph without changing UUID
                            try:
                                await self.state.graph.query(
                                    "MATCH (n:Entity {uuid: $uuid}) WHERE n.group_id = $group_id SET n.name = $new_name, n.path = $new_path, n.previous_filename = $old_name",
                                    uuid=entity_uuid, new_name=Path(path).name, new_path=path, old_name=prev_path, group_id=group_id
                                )
                            except Exception:
                                logger.warning(f"Failed to update metadata for renamed node {entity_uuid} in graph")
                        
                        # Rename in db, preserving entity_uuid
                        await self.state.db.execute(
                            "UPDATE github_file_state SET file_path = ? WHERE repo_id = ? AND file_path = ?",
                            (path, self.repo_id, prev_path)
                        )
                
                content_bytes = await client.get_blob(installation_id, owner, name, f["sha"])
                try:
                    text = content_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                        
                content_hash = hashlib.sha256(content_bytes).hexdigest()
                media_type, _ = mimetypes.guess_type(path)
                
                batch_files.append((path, text, content_hash, media_type, f["sha"]))
                
                if len(batch_files) >= 20:
                    files_added += await process_file_batch(self, batch_files, repo_row, head_sha, group_id, source_id, rich_meta)
                    batch_files = []
                    
            if batch_files:
                files_added += await process_file_batch(self, batch_files, repo_row, head_sha, group_id, source_id, rich_meta)
                    
            commits = comparison.get("commits", [])
        else:
            # INITIAL SYNC
            tree = await client.get_tree(installation_id, owner, name, head_sha, recursive=True)
            for item in tree:
                if item["type"] != "blob":
                    continue
                path = item["path"]
                if not should_include_file(path):
                    continue
                
                content_bytes = await client.get_blob(installation_id, owner, name, item["sha"])
                try:
                    text = content_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                
                content_hash = hashlib.sha256(content_bytes).hexdigest()
                media_type, _ = mimetypes.guess_type(path)
                
                batch_files.append((path, text, content_hash, media_type, item["sha"]))
                
                if len(batch_files) >= 20:
                    files_added += await process_file_batch(self, batch_files, repo_row, head_sha, group_id, source_id, rich_meta)
                    batch_files = []
                    
            if batch_files:
                files_added += await process_file_batch(self, batch_files, repo_row, head_sha, group_id, source_id, rich_meta)

            commits = await client.get_commits(installation_id, owner, name)
            
        nodes_to_add = []
        edges_to_add = []
        repo_uuid = f"repo-{self.repo_id}"

        for commit_ref in commits:
            sha = commit_ref["sha"]
            row = await self.state.db.fetch_one("SELECT 1 FROM github_commits WHERE repo_id = ? AND commit_sha = ?", (self.repo_id, sha))
            if row:
                continue
                
            full_commit = await client.get_commit(installation_id, owner, name, sha)
            
            commit_data = full_commit.get("commit", {})
            author_data = commit_data.get("author", {})
            author_login = full_commit.get("author", {}).get("login") if full_commit.get("author") else None
            
            message = commit_data.get("message", "")
            author_name = author_data.get("name", "")
            author_email = author_data.get("email", "")
            parents = [p["sha"] for p in full_commit.get("parents", [])]
            
            is_merge = len(parents) > 1
            has_conflict_marker = "conflict" in message.lower()
            
            commit_uuid = f"commit-{self.repo_id}-{sha}"
            commit_label = "GitMerge" if is_merge else "GitCommit"
            commit_name = f"Merge Commit {sha[:7]}" if is_merge else f"Commit {sha[:7]}"
            nodes_to_add.append({
                "uuid": commit_uuid,
                "name": commit_name,
                "labels": [commit_label, "GitCommit"],
                "summary": f"{commit_name} by {author_login or author_name}:\n{message}",
                "attributes": {
                    **rich_meta,
                    "sha": sha,
                    "type": "commit",
                    "classification_subtype": "source_control",
                    "need_attention": has_conflict_marker,
                    "commit_sha": sha,
                    "commit_message": message,
                    "author": author_login or author_name,
                    "external_id": sha,
                    "external_url": f"https://github.com/{owner}/{name}/commit/{sha}"
                }
            })
            edges_to_add.append({
                "source_uuid": repo_uuid,
                "target_uuid": commit_uuid,
                "fact": f"Repository contains commit {sha}",
                "labels": ["CONTAINS"]
            })
            
            if author_login:
                user_uuid = f"dev-{author_login}"
                nodes_to_add.append({
                    "uuid": user_uuid,
                    "name": author_login,
                    "labels": ["Developer"],
                    "summary": f"GitHub developer {author_login}",
                    "attributes": {
                        **rich_meta,
                        "login": author_login, 
                        "type": "developer",
                        "classification_subtype": "human",
                        "external_id": author_login,
                        "external_url": f"https://github.com/{author_login}"
                    }
                })
                edges_to_add.append({
                    "source_uuid": commit_uuid,
                    "target_uuid": user_uuid,
                    "fact": f"Commit authored by {author_login}",
                    "labels": ["AUTHORED_BY"]
                })
            
            # Push merge commit event to external agent webhook
            if nodes_to_add or edges_to_add:
                await self.state.graph.add_deterministic_nodes_and_edges(group_id, nodes_to_add, edges_to_add)
                nodes_to_add = []
                edges_to_add = []
            
            if has_conflict_marker:
                await _push_to_agent_webhook(
                        self.state.db,
                        self.repo_id,
                        "merge_commit",
                        {
                            "repo_id": self.repo_id,
                            "merge_commit_sha": sha,
                            "author": author_login or author_name,
                            "message": message,
                            "conflict_detected": True,
                            "detected_at": datetime.now(UTC).isoformat()
                        }
                    )
            
            changed_files = [f["filename"] for f in full_commit.get("files", []) if should_include_file(f["filename"])]
            files_str = "\n".join(f"- {f}" for f in changed_files)
            
            text = f"Git Commit {sha}\nAuthor: {author_name} ({author_login or author_email})\nMessage: {message}\nModified Files:\n{files_str}"
            
            content_bytes = text.encode("utf-8")
            content_hash = hashlib.sha256(content_bytes).hexdigest()
            doc_id = f"{source_id}:commit:{sha}"
            
            doc = CanonicalDocument(
                document_id=doc_id,
                source_id=source_id,
                source_kind=SourceKind.GITHUB,
                name=f"Commit {sha[:7]}",
                media_type="text/plain",
                text=text,
                tenant="synapse",
                content_hash=content_hash,
                byte_size=len(content_bytes),
                access_tag="internal"
            )
            
            assert self.state.pipeline is not None
            await self.state.pipeline.submit(doc)
            
            now = datetime.now(UTC).isoformat()
            await self.state.db.execute(
                """
                INSERT INTO github_commits (
                    repo_id, commit_sha, parent_shas_json, author_login, author_name,
                    author_email, message, committed_at, synced_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'SYNCED')
                ON CONFLICT(repo_id, commit_sha) DO NOTHING
                """,
                (self.repo_id, sha, json.dumps(parents), author_login, author_name, author_email, message, author_data.get("date", now), now)
            )
            commits_processed += 1
            
        if nodes_to_add or edges_to_add:
            await self.state.graph.add_deterministic_nodes_and_edges(group_id, nodes_to_add, edges_to_add)

        project_rows = await self.state.db.fetch_all(
            "SELECT p.name, p.description FROM projects p JOIN project_repositories pr ON p.id = pr.project_id WHERE pr.repo_id = ?",
            (self.repo_id,)
        )
        project_context = None
        if project_rows:
            project_context = "\n".join([f"- {row['name']}: {row['description']}" for row in project_rows if row['description']])

        # Sync Pull Requests & Issues
        await self._sync_pull_requests(client, repo_row, installation_id, group_id, project_context)
        await self._sync_issues(client, repo_row, installation_id, group_id, project_context)

        # Drain pipeline queue to ensure chunks and graph writes are flushed before we mark success
        if self.state.pipeline:
            await self.state.pipeline.drain()

        now = datetime.now(UTC).isoformat()
        await self.state.db.execute(
            """
            UPDATE github_sync_runs 
            SET status = 'SUCCESS', completed_at = ?, files_added = ?, commits_processed = ?
            WHERE id = ?
            """,
            (now, files_added, commits_processed, run_id)
        )
        await self.state.db.execute(
            """
            UPDATE github_repositories 
            SET last_synced_commit_sha = ?, last_synced_at = ?, status = 'READY'
            WHERE id = ?
            """,
            (head_sha, now, self.repo_id)
        )

        # Push sync completed event to external agent webhook
        await _push_to_agent_webhook(
            self.state.db,
            self.repo_id,
            "sync_completed",
            {
                "repo_id": self.repo_id,
                "files_added": files_added,
                "commits_processed": commits_processed,
                "head_sha": head_sha,
                "completed_at": now
            }
        )

        # Check for any need_attention items created during this sync
        need_attention_prs = await self.state.graph.query(
            """
            MATCH (n:Entity) 
            WHERE (n.labels = ['GitPullRequest'] OR n.labels = ['GitIssue']) 
            AND n.attributes.need_attention = true
            AND n.uuid IN $pr_uuids
            RETURN n.uuid AS uuid, n.name AS name, n.attributes.need_attention AS need_attention
            """,
            group_id=group_id,
            pr_uuids=[f"pr-{self.repo_id}-{pr['number']}" for pr in pulls] if 'pulls' in locals() else []
        )

        need_attention_issues = await self.state.graph.query(
            """
            MATCH (n:Entity) 
            WHERE n.labels = ['GitIssue'] 
            AND n.attributes.need_attention = true
            AND n.uuid IN $issue_uuids
            RETURN n.uuid AS uuid, n.name AS name, n.attributes.need_attention AS need_attention
            """,
            group_id=group_id,
            issue_uuids=[f"issue-{self.repo_id}-{issue['number']}" for issue in issues] if 'issues' in locals() else []
        )

        if need_attention_prs or need_attention_issues:
            await _push_to_agent_webhook(
                self.state.db,
                self.repo_id,
                "need_attention_detected",
                {
                    "repo_id": self.repo_id,
                    "pull_requests": [{"uuid": r["uuid"], "name": r["name"]} for r in need_attention_prs],
                    "issues": [{"uuid": r["uuid"], "name": r["name"]} for r in need_attention_issues],
                    "detected_at": now
                }
            )

    async def _sync_pull_requests(self, client: GitHubAppClient, repo_row: dict, installation_id: str, group_id: str, project_context: str | None = None) -> None:
        owner = repo_row["owner"]
        name = repo_row["name"]
        
        try:
            pulls = await client.get_pulls(installation_id, owner, name, state="all")
        except Exception:
            logger.exception("Failed to fetch Pull Requests")
            return
            
        from github.llm import analyze_pr_for_attention
        
        nodes = []
        edges = []
        repo_uuid = f"repo-{self.repo_id}"
        
        for pr in pulls:
            pr_num = pr["number"]
            pr_uuid = f"pr-{self.repo_id}-{pr_num}"
            
            try:
                pr_files = await client.get_pull_files(installation_id, owner, name, pr_num)
                diff_files = [f["filename"] for f in pr_files]
            except Exception:
                diff_files = []
                
            pr_title = pr.get("title", "")
            pr_body = pr.get("body", "") or ""
            
            pr_head_sha = pr.get("head", {}).get("sha", "")
            cached = await self.state.db.fetch_one(
                "SELECT need_attention, affected_files_json, attention_json FROM pr_analysis_cache WHERE repo_id = ? AND pr_number = ? AND commit_sha = ?",
                (self.repo_id, pr_num, pr_head_sha)
            )
            
            if cached:
                need_attention = bool(cached["need_attention"])
                pr_affected_files = json.loads(cached["affected_files_json"])
                attention_dict = json.loads(cached["attention_json"]) if cached.get("attention_json") else {}
            else:
                analysis = await analyze_pr_for_attention(self.state, pr_title, pr_body, diff_files, project_context)
                attention_dict = analysis.get("attention", {})
                need_attention = attention_dict.get("required", False)
                pr_affected_files = analysis.get("affected_files", [])
                
                prev_state = await self.state.db.fetch_one(
                    "SELECT need_attention FROM github_pr_state WHERE repo_id = ? AND pr_number = ?",
                    (self.repo_id, pr_num)
                )
                if prev_state and prev_state["need_attention"]:
                    need_attention = True
                    
                if pr.get("state") == "closed":
                    need_attention = False
                    
                now = datetime.now(UTC).isoformat()
                await self.state.db.execute(
                    "INSERT INTO pr_analysis_cache (repo_id, pr_number, commit_sha, need_attention, affected_files_json, attention_json, analyzed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (self.repo_id, pr_num, pr_head_sha, int(need_attention), json.dumps(pr_affected_files), json.dumps(attention_dict), now)
                )
                await self.state.db.execute(
                    """
                    INSERT INTO github_pr_state (repo_id, pr_number, last_updated_at, need_attention, title, state) 
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(repo_id, pr_number) DO UPDATE SET last_updated_at=excluded.last_updated_at, need_attention=excluded.need_attention, title=excluded.title, state=excluded.state
                    """,
                    (self.repo_id, pr_num, pr.get("updated_at", now), int(need_attention), pr_title, pr.get("state", "open"))
                )
            
            rich_meta = self._build_rich_metadata(repo_row, project_context, is_code=False)
            
            pr_node = {
                "uuid": pr_uuid,
                "name": f"PR #{pr_num}: {pr_title}",
                "labels": ["GitPullRequest"],
                "summary": pr_body[:200] if pr_body else "No description",
                "attributes": {
                    **rich_meta,
                    "number": pr_num,
                    "title": pr_title,
                    "state": pr.get("state", "open"),
                    "html_url": pr.get("html_url", ""),
                    "need_attention": need_attention,
                    "attention_type": attention_dict.get("type"),
                    "attention_priority": attention_dict.get("priority"),
                    "attention_reason": attention_dict.get("reason"),
                    "type": "pull_request",
                    "classification_subtype": "collaboration",
                    "pr_number": pr_num,
                    "target_branch": pr.get("base", {}).get("ref", ""),
                    "author": pr.get("user", {}).get("login", ""),
                    "external_id": str(pr_num),
                    "external_url": pr.get("html_url", "")
                }
            }
            nodes.append(pr_node)
            
            edges.append({
                "source_uuid": repo_uuid,
                "target_uuid": pr_uuid,
                "fact": f"Repository contains pull request {pr_num}",
                "labels": ["CONTAINS"]
            })
            
            user_data = pr.get("user")
            if user_data:
                user_login = user_data.get("login")
                user_uuid = f"dev-{user_login}"
                nodes.append({
                    "uuid": user_uuid,
                    "name": user_login,
                    "labels": ["Developer"],
                    "summary": f"GitHub developer {user_login}",
                    "attributes": {
                        **rich_meta,
                        "login": user_login,
                        "type": "developer",
                        "classification_subtype": "human",
                        "external_id": user_login,
                        "external_url": f"https://github.com/{user_login}"
                    }
                })
                edges.append({
                    "source_uuid": pr_uuid,
                    "target_uuid": user_uuid,
                    "fact": f"Pull Request authored by {user_login}",
                    "labels": ["AUTHORED_BY"]
                })
                
            for filepath in diff_files:
                file_uuid = f"file-{self.repo_id}-{filepath}"
                edges.append({
                    "source_uuid": pr_uuid,
                    "target_uuid": file_uuid,
                    "fact": f"Pull Request modifies file {filepath}",
                    "labels": ["MODIFIES"]
                })
                
            if need_attention:
                for filepath in pr_affected_files:
                    affected_file_uuid = f"file-{self.repo_id}-{filepath}"
                    
                    # UPSERT: Update existing file node's need_attention instead of creating duplicate
                    # First, check if file node exists
                    existing = await self.state.graph.query(
                        "MATCH (n:Entity) WHERE n.uuid = $uuid RETURN n.uuid AS uuid",
                        group_id=group_id, uuid=affected_file_uuid
                    )
                    
                    if existing:
                        # Update existing node's need_attention attribute via MERGE-like pattern
                        # We'll add an AFFECTS edge and update the node attribute
                        edges.append({
                            "source_uuid": pr_uuid,
                            "target_uuid": affected_file_uuid,
                            "fact": f"Pull Request requires attention for file {filepath}",
                            "labels": ["AFFECTS"]
                        })
                        # Update the existing node's need_attention attribute
                        nodes.append({
                            "uuid": affected_file_uuid,
                            "name": Path(filepath).name,
                            "labels": ["GitFile"],
                            "summary": "",  # Will be preserved by add_deterministic_nodes_and_edges
                            "attributes": {
                                "path": filepath,
                                "type": "GitFile",
                                "need_attention": True
                            }
                        })
                    else:
                        # File node doesn't exist yet, create it
                        file_summary = f"Source code file at {filepath}"
                        nodes.append({
                            "uuid": affected_file_uuid,
                            "name": Path(filepath).name,
                            "labels": ["GitFile"],
                            "summary": file_summary,
                            "attributes": {
                                "path": filepath,
                                "type": "GitFile",
                                "need_attention": True
                            }
                        })
                        edges.append({
                            "source_uuid": pr_uuid,
                            "target_uuid": affected_file_uuid,
                            "fact": f"Pull Request requires attention for file {filepath}",
                            "labels": ["AFFECTS"]
                        })
                    
        if nodes or edges:
            await self.state.graph.add_deterministic_nodes_and_edges(group_id, nodes, edges)

    async def _sync_issues(self, client: GitHubAppClient, repo_row: dict, installation_id: str, group_id: str, project_context: str | None = None) -> None:
        owner = repo_row["owner"]
        name = repo_row["name"]
        
        try:
            issues_raw = await client.get_issues(installation_id, owner, name, state="open")
        except Exception:
            logger.exception("Failed to fetch Issues")
            return
            
        issues = [i for i in issues_raw if "pull_request" not in i]
        
        from github.llm import analyze_issue_for_attention
        
        rows = await self.state.db.fetch_all(
            "SELECT file_path FROM github_file_state WHERE repo_id = ? AND status != 'DELETED'",
            (self.repo_id,)
        )
        repo_files = [row["file_path"] for row in rows]
        
        nodes = []
        edges = []
        repo_uuid = f"repo-{self.repo_id}"
        
        for issue in issues:
            issue_num = issue["number"]
            issue_uuid = f"issue-{self.repo_id}-{issue_num}"
            
            try:
                comments_raw = await client.get_issue_comments(installation_id, owner, name, issue_num)
                comments = [c.get("body", "") for c in comments_raw]
            except Exception:
                comments = []
                
            issue_title = issue.get("title", "")
            issue_body = issue.get("body", "") or ""
            
            comment_count = issue.get("comments", 0)
            cached = await self.state.db.fetch_one(
                "SELECT need_attention, affected_files_json, affected_classes_json, attention_json FROM issue_analysis_cache WHERE repo_id = ? AND issue_number = ? AND comment_count = ?",
                (self.repo_id, issue_num, comment_count)
            )
            
            if cached:
                need_attention = bool(cached["need_attention"])
                issue_affected_files = json.loads(cached["affected_files_json"])
                issue_affected_classes = json.loads(cached["affected_classes_json"])
                attention_dict = json.loads(cached["attention_json"]) if cached.get("attention_json") else {}
            else:
                analysis = await analyze_issue_for_attention(
                    self.state, issue_title, issue_body, comments, repo_files, project_context
                )
                attention_dict = analysis.get("attention", {})
                need_attention = attention_dict.get("required", False)
                issue_affected_files = analysis.get("affected_files", [])
                issue_affected_classes = analysis.get("affected_classes", [])
                
                prev_state = await self.state.db.fetch_one(
                    "SELECT need_attention FROM github_issue_state WHERE repo_id = ? AND issue_number = ?",
                    (self.repo_id, issue_num)
                )
                if prev_state and prev_state["need_attention"]:
                    need_attention = True
                    
                if issue.get("state") == "closed":
                    need_attention = False
                    
                now = datetime.now(UTC).isoformat()
                await self.state.db.execute(
                    "INSERT INTO issue_analysis_cache (repo_id, issue_number, comment_count, need_attention, affected_files_json, affected_classes_json, attention_json, analyzed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (self.repo_id, issue_num, comment_count, int(need_attention), json.dumps(issue_affected_files), json.dumps(issue_affected_classes), json.dumps(attention_dict), now)
                )
                await self.state.db.execute(
                    """
                    INSERT INTO github_issue_state (repo_id, issue_number, last_updated_at, need_attention, title, state) 
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(repo_id, issue_number) DO UPDATE SET last_updated_at=excluded.last_updated_at, need_attention=excluded.need_attention, title=excluded.title, state=excluded.state
                    """,
                    (self.repo_id, issue_num, issue.get("updated_at", now), int(need_attention), issue_title, issue.get("state", "open"))
                )
            
            rich_meta = self._build_rich_metadata(repo_row, project_context, is_code=False)
            
            issue_node = {
                "uuid": issue_uuid,
                "name": f"Issue #{issue_num}: {issue_title}",
                "labels": ["GitIssue"],
                "summary": issue_body[:200] if issue_body else "No description",
                "attributes": {
                    **rich_meta,
                    "number": issue_num,
                    "title": issue_title,
                    "state": issue.get("state", "open"),
                    "html_url": issue.get("html_url", ""),
                    "need_attention": need_attention,
                    "attention_type": attention_dict.get("type"),
                    "attention_priority": attention_dict.get("priority"),
                    "attention_reason": attention_dict.get("reason"),
                    "type": "issue",
                    "classification_subtype": "collaboration",
                    "author": issue.get("user", {}).get("login", ""),
                    "external_id": str(issue_num),
                    "external_url": issue.get("html_url", "")
                }
            }
            nodes.append(issue_node)
            
            edges.append({
                "source_uuid": repo_uuid,
                "target_uuid": issue_uuid,
                "fact": f"Repository has issue {issue_num}",
                "labels": ["CONTAINS"]
            })
            
            user_data = issue.get("user")
            if user_data:
                user_login = user_data.get("login")
                user_uuid = f"dev-{user_login}"
                nodes.append({
                    "uuid": user_uuid,
                    "name": user_login,
                    "labels": ["Developer"],
                    "summary": f"GitHub developer {user_login}",
                    "attributes": {
                        **rich_meta,
                        "login": user_login,
                        "type": "developer",
                        "classification_subtype": "human",
                        "external_id": user_login,
                        "external_url": f"https://github.com/{user_login}"
                    }
                })
                edges.append({
                    "source_uuid": issue_uuid,
                    "target_uuid": user_uuid,
                    "fact": f"Issue reported by {user_login}",
                    "labels": ["REPORTED_BY"]
                })
                
            if need_attention:
                for filepath in issue_affected_files:
                    affected_file_uuid = f"file-{self.repo_id}-{filepath}"
                    
                    # UPSERT: Update existing file node's need_attention instead of creating duplicate
                    existing = await self.state.graph.query(
                        "MATCH (n:Entity) WHERE n.uuid = $uuid RETURN n.uuid AS uuid",
                        group_id=group_id, uuid=affected_file_uuid
                    )
                    
                    if existing:
                        edges.append({
                            "source_uuid": issue_uuid,
                            "target_uuid": affected_file_uuid,
                            "fact": f"Issue affects file {filepath}",
                            "labels": ["AFFECTS"]
                        })
                        nodes.append({
                            "uuid": affected_file_uuid,
                            "name": Path(filepath).name,
                            "labels": ["GitFile"],
                            "summary": "",
                            "attributes": {
                                "path": filepath,
                                "type": "GitFile",
                                "need_attention": True
                            }
                        })
                    else:
                        file_summary = f"Source code file at {filepath}"
                        nodes.append({
                            "uuid": affected_file_uuid,
                            "name": Path(filepath).name,
                            "labels": ["GitFile"],
                            "summary": file_summary,
                            "attributes": {
                                "path": filepath,
                                "type": "GitFile",
                                "need_attention": True
                            }
                        })
                        edges.append({
                            "source_uuid": issue_uuid,
                            "target_uuid": affected_file_uuid,
                            "fact": f"Issue affects file {filepath}",
                            "labels": ["AFFECTS"]
                        })
                    
                for class_name in issue_affected_classes:
                    for filepath in issue_affected_files:
                        class_uuid = f"class-{self.repo_id}-{filepath}-{class_name}"
                        nodes.append({
                            "uuid": class_uuid,
                            "name": class_name,
                            "labels": ["CodeClass"],
                            "summary": f"Class {class_name} in {filepath}",
                            "attributes": {
                                "name": class_name,
                                "type": "CodeClass",
                                "need_attention": True
                            }
                        })
                        edges.append({
                            "source_uuid": issue_uuid,
                            "target_uuid": class_uuid,
                            "fact": f"Issue affects class {class_name} in {filepath}",
                            "labels": ["AFFECTS"]
                        })
                        
        if nodes or edges:
            await self.state.graph.add_deterministic_nodes_and_edges(group_id, nodes, edges)
