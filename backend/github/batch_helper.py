from datetime import UTC, datetime
from pathlib import Path
import logging
from app.state import AppState
from kg_contracts.models import CanonicalDocument, SourceKind
from github.llm import summarize_files_batch, analyze_file_for_attention
import hashlib

logger = logging.getLogger("synapse.github.batch")

async def process_file_batch(job, batch, repo_row, head_sha, group_id, source_id, rich_meta=None):
    if not batch: return 0
    
    if rich_meta is None:
        rich_meta = {}
    
    # 1. Pipeline submission and AST parsing
    for b in batch:
        path, text, content_hash, media_type, file_sha = b
        doc_id = f"{source_id}:file:{path}"
        doc = CanonicalDocument(
            document_id=doc_id,
            source_id=source_id,
            source_kind=SourceKind.GITHUB,
            name=path,
            media_type=media_type or "text/plain",
            text=text,
            content_hash=content_hash,
            byte_size=len(text.encode('utf-8')),
            access_tag="internal"
        )
        assert job.state.pipeline is not None
        await job.state.pipeline.submit(doc)
        await job._process_code_intelligence(path, text, job.repo_id, group_id, rich_meta=rich_meta)

    # 2. Batch Summarization
    from github.file_classifier import should_process_semantically, detect_architectural_role, detect_file_type, detect_language
    files_to_summarize = [(b[0], b[1]) for b in batch if should_process_semantically(b[0])]
    summaries = await summarize_files_batch(job.state, files_to_summarize)
    
    nodes = []
    edges = []
    repo_uuid = f"repo-{job.repo_id}"
    
    # 3. Fetch existing UUIDs
    path_to_uuid = {}
    paths = [b[0] for b in batch]
    if paths:
        placeholders = ",".join("?" for _ in paths)
        rows = await job.state.db.fetch_all(
            f"SELECT file_path, entity_uuid FROM github_file_state WHERE repo_id = ? AND file_path IN ({placeholders})",
            [job.repo_id] + paths
        )
        for r in rows:
            if r["entity_uuid"]:
                path_to_uuid[r["file_path"]] = r["entity_uuid"]

    # 4. Graph Injection
    for b in batch:
        path, text, content_hash, media_type, file_sha = b
        file_summary = summaries.get(path, f"Source code file at {path}")
        needs_attention = await analyze_file_for_attention(path, text)
        
        file_uuid = path_to_uuid.get(path)
        if not file_uuid:
            import uuid
            file_uuid = f"file-{job.repo_id}-{uuid.uuid4()}"
            path_to_uuid[path] = file_uuid
        
        # Determine meaningful directory
        p = Path(path)
        parent_dir = str(p.parent)
        
        dir_uuid = None
        if parent_dir != "." and parent_dir != "/":
            # Filter out meaningless directories
            if not any(excluded in p.parts for excluded in {".git", "node_modules", "dist", "build", "coverage", "venv", "__pycache__"}):
                dir_uuid = f"dir-{job.repo_id}-{parent_dir}"
                nodes.append({
                    "uuid": dir_uuid,
                    "name": parent_dir,
                    "labels": ["GitDirectory"],
                    "summary": f"Directory {parent_dir}",
                    "attributes": {
                        **rich_meta,
                        "path": parent_dir,
                        "type": "directory",
                        "classification_subtype": "source_code",
                        "external_id": parent_dir,
                        "external_url": f"{rich_meta.get('url')}/tree/{head_sha}/{parent_dir}"
                    }
                })
                edges.append({
                    "source_uuid": repo_uuid,
                    "target_uuid": dir_uuid,
                    "fact": f"Repository contains directory {parent_dir}",
                    "labels": ["CONTAINS"]
                })
                edges.append({
                    "source_uuid": dir_uuid,
                    "target_uuid": file_uuid,
                    "fact": f"Directory contains file {path}",
                    "labels": ["CONTAINS"]
                })

        if not dir_uuid:
            edges.append({
                "source_uuid": repo_uuid,
                "target_uuid": file_uuid,
                "fact": f"Repository contains file {path}",
                "labels": ["CONTAINS"]
            })

        nodes.extend([
            {
                "uuid": repo_uuid,
                "name": repo_row["name"],
                "labels": ["GitRepository"],
                "summary": f"GitHub Repository {repo_row['full_name']}",
                "attributes": {
                    **rich_meta,
                    "full_name": repo_row["full_name"],
                    "type": "repository",
                    "classification_subtype": "codebase",
                    "external_id": str(repo_row["github_repo_id"]),
                    "external_url": f"https://github.com/{repo_row['full_name']}"
                }
            },
            {
                "uuid": file_uuid,
                "name": Path(path).name,
                "labels": ["GitFile"],
                "summary": file_summary,
                "attributes": {
                    **rich_meta,
                    "path": path,
                    "type": "file",
                    "classification_subtype": "source_code",
                    "need_attention": needs_attention.get("required", False) if isinstance(needs_attention, dict) else needs_attention,
                    "attention_type": needs_attention.get("type") if isinstance(needs_attention, dict) else None,
                    "attention_priority": needs_attention.get("priority") if isinstance(needs_attention, dict) else None,
                    "attention_reason": needs_attention.get("reason") if isinstance(needs_attention, dict) else None,
                    "architectural_role": detect_architectural_role(path),
                    "file_type": detect_file_type(path),
                    "language": detect_language(path),
                    "external_id": path,
                    "external_url": f"{rich_meta.get('url')}/blob/{head_sha}/{path}"
                }
            }
        ])
        
    if nodes or edges:
        await job.state.graph.add_deterministic_nodes_and_edges(group_id, nodes, edges)
        
    # 4. Update DB state
    now = datetime.now(UTC).isoformat()
    for b in batch:
        path, text, content_hash, media_type, file_sha = b
        await job.state.db.execute(
            """
            INSERT INTO github_file_state (
                repo_id, file_path, entity_uuid, current_blob_sha, current_commit_sha,
                last_content_hash, last_synced_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ADDED')
            ON CONFLICT(repo_id, file_path) DO UPDATE SET
                entity_uuid = excluded.entity_uuid,
                current_blob_sha = excluded.current_blob_sha,
                current_commit_sha = excluded.current_commit_sha,
                last_content_hash = excluded.last_content_hash,
                last_synced_at = excluded.last_synced_at,
                status = 'MODIFIED'
            """,
            (job.repo_id, path, path_to_uuid[path], file_sha, head_sha, content_hash, now)
        )
    return len(batch)
