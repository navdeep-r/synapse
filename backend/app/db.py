"""SQLite metadata store: connection management and schema.

Holds everything that is not the graph itself — provenance, receipts, activity,
ontology, curation decisions, snapshots and settings. The graph is the source of
truth for entities and facts; this database is the source of truth for how they
got there.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    document_id      TEXT PRIMARY KEY,
    source_id        TEXT NOT NULL,
    source_kind      TEXT NOT NULL,
    name             TEXT NOT NULL,
    media_type       TEXT NOT NULL,
    tenant           TEXT NOT NULL DEFAULT 'tenant-a',
    content_hash     TEXT NOT NULL,
    byte_size        INTEGER NOT NULL DEFAULT 0,
    access_tag       TEXT NOT NULL DEFAULT 'internal',
    text             TEXT NOT NULL DEFAULT '',
    ingested_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(content_hash);
CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source_id);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id         TEXT PRIMARY KEY,
    document_id      TEXT NOT NULL,
    ordinal          INTEGER NOT NULL,
    text             TEXT NOT NULL,
    offset           INTEGER NOT NULL,
    char_length      INTEGER NOT NULL,
    content_hash     TEXT NOT NULL,
    provenance_json  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_hash ON chunks(content_hash);

-- R7: dropped content is retained so thresholds can be reviewed without re-ingesting.
CREATE TABLE IF NOT EXISTS prefilter_drops (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_id         TEXT NOT NULL,
    document_id      TEXT NOT NULL,
    outcome          TEXT NOT NULL,
    rule             TEXT,
    dropped_text     TEXT,
    created_at       TEXT NOT NULL
);

-- R3: intent-then-commit. A row is PENDING before Graphiti is called.
CREATE TABLE IF NOT EXISTS episode_receipts (
    episode_id       TEXT PRIMARY KEY,
    document_id      TEXT NOT NULL,
    group_id         TEXT NOT NULL DEFAULT 'synapse',
    status           TEXT NOT NULL,
    chunk_ids_json   TEXT NOT NULL DEFAULT '[]',
    created_at       TEXT NOT NULL,
    committed_at     TEXT,
    graph_uuid       TEXT,
    nodes_created    INTEGER NOT NULL DEFAULT 0,
    edges_created    INTEGER NOT NULL DEFAULT 0,
    llm_calls        INTEGER NOT NULL DEFAULT 0,
    latency_ms       INTEGER NOT NULL DEFAULT 0,
    error            TEXT
);
CREATE INDEX IF NOT EXISTS idx_receipts_status ON episode_receipts(status);
CREATE INDEX IF NOT EXISTS idx_receipts_document ON episode_receipts(document_id);

-- Ingestion-pipeline dedup: the same intent-then-commit shape as episode_receipts,
-- one level further out. Keyed by idempotency_key rather than episode_id because
-- the question here is "have we already applied this logical change?" under
-- at-least-once delivery, not "did this graph write land?".
CREATE TABLE IF NOT EXISTS event_receipts (
    idempotency_key  TEXT PRIMARY KEY,
    event_id         TEXT NOT NULL,
    tenant_id        TEXT NOT NULL DEFAULT 'tenant-a',
    group_id         TEXT NOT NULL DEFAULT 'synapse',
    entity_key       TEXT NOT NULL,
    partition_key    TEXT NOT NULL,
    sequence         INTEGER NOT NULL DEFAULT 0,
    trace_id         TEXT NOT NULL,
    status           TEXT NOT NULL,
    attempts         INTEGER NOT NULL DEFAULT 0,
    stage            TEXT,
    claimed_at       TEXT NOT NULL,
    committed_at     TEXT,
    error            TEXT
);
CREATE INDEX IF NOT EXISTS idx_event_receipts_status ON event_receipts(status);
CREATE INDEX IF NOT EXISTS idx_event_receipts_entity ON event_receipts(entity_key);

CREATE TABLE IF NOT EXISTS pipeline_jobs (
    job_id           TEXT PRIMARY KEY,
    status           TEXT NOT NULL,
    total_chunks     INTEGER NOT NULL DEFAULT 0,
    processed_chunks INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    error            TEXT
);

CREATE TABLE IF NOT EXISTS pipeline_metrics (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id           TEXT NOT NULL,
    metric_name      TEXT NOT NULL,
    metric_value     REAL NOT NULL,
    recorded_at      TEXT NOT NULL,
    FOREIGN KEY(job_id) REFERENCES pipeline_jobs(job_id)
);

CREATE INDEX IF NOT EXISTS idx_event_receipts_trace ON event_receipts(trace_id);

-- Highest source sequence already applied per entity. Partition ordering keeps
-- deltas in order within one consumer run; this survives connector restarts and
-- rebalances, where an older delta can legitimately arrive after a newer one.
CREATE TABLE IF NOT EXISTS entity_watermarks (
    entity_key       TEXT NOT NULL,
    last_sequence    INTEGER NOT NULL DEFAULT 0,
    updated_at       TEXT NOT NULL,
    PRIMARY KEY (entity_key)
);

-- Poison messages and stage failures. Retained with the full frame so a fixed
-- connector can replay them rather than the data being lost.
CREATE TABLE IF NOT EXISTS dead_letters (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    topic            TEXT NOT NULL,
    stage            TEXT NOT NULL,
    idempotency_key  TEXT,
    event_id         TEXT,
    trace_id         TEXT,
    partition_key    TEXT,
    error_type       TEXT NOT NULL,
    error            TEXT NOT NULL,
    payload_b64      TEXT NOT NULL,
    attempts         INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    resolved_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_dead_letters_stage ON dead_letters(stage);
CREATE INDEX IF NOT EXISTS idx_dead_letters_unresolved ON dead_letters(resolved_at);

CREATE TABLE IF NOT EXISTS sources (
    id               TEXT PRIMARY KEY,
    type             TEXT NOT NULL,
    tenant           TEXT NOT NULL DEFAULT 'default',
    status           TEXT NOT NULL DEFAULT 'confirmed',
    last_cdc_run     TEXT,
    doc_count        INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity (
    id               TEXT PRIMARY KEY,
    source_id        TEXT NOT NULL,
    event            TEXT NOT NULL,
    details          TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activity_source ON activity(source_id);
CREATE INDEX IF NOT EXISTS idx_activity_created ON activity(created_at DESC);

CREATE TABLE IF NOT EXISTS ontology_entity_types (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    description      TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS ontology_relation_types (
    relation_type       TEXT PRIMARY KEY,
    version             INTEGER NOT NULL DEFAULT 1,
    source_types_json   TEXT NOT NULL DEFAULT '[]',
    target_types_json   TEXT NOT NULL DEFAULT '[]',
    max_active_outgoing INTEGER NOT NULL DEFAULT 1,
    temporal            INTEGER NOT NULL DEFAULT 1,
    overlap_allowed     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ontology_changes (
    id               TEXT PRIMARY KEY,
    relation_type    TEXT NOT NULL,
    field_to_change  TEXT NOT NULL,
    old_value_json   TEXT,
    new_value_json   TEXT,
    justification    TEXT NOT NULL DEFAULT '',
    proposed_by      TEXT NOT NULL DEFAULT 'console',
    status           TEXT NOT NULL DEFAULT 'pending',
    impact           TEXT NOT NULL DEFAULT 'Medium',
    impact_report    TEXT NOT NULL DEFAULT '',
    affected_edges_json TEXT NOT NULL DEFAULT '[]',
    created_at       TEXT NOT NULL,
    resolved_at      TEXT
);

-- Merge candidates surfaced by the audit layer (v2 doc 3.8) plus steward decisions.
CREATE TABLE IF NOT EXISTS curation_candidates (
    id               TEXT PRIMARY KEY,
    group_id         TEXT NOT NULL,
    entity_type      TEXT NOT NULL DEFAULT 'Entity',
    primary_uuid     TEXT NOT NULL,
    primary_name     TEXT NOT NULL,
    secondary_uuid   TEXT NOT NULL,
    secondary_name   TEXT NOT NULL,
    confidence       REAL NOT NULL DEFAULT 0.0,
    reason           TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'open',
    decided_at       TEXT,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_candidates_status ON curation_candidates(status);

CREATE TABLE IF NOT EXISTS quarantine (
    id               TEXT PRIMARY KEY,
    kind             TEXT NOT NULL,
    subject          TEXT NOT NULL,
    detail           TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quarantine_kind ON quarantine(kind);

CREATE TABLE IF NOT EXISTS calibration (
    id               TEXT PRIMARY KEY,
    title            TEXT NOT NULL,
    current_val      REAL NOT NULL,
    recommended_val  REAL NOT NULL,
    recall_impact    TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'open',
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
    version          TEXT PRIMARY KEY,
    fact_scope       TEXT NOT NULL,
    report_scope     TEXT NOT NULL,
    acl_policy       TEXT NOT NULL,
    entity_count     INTEGER NOT NULL DEFAULT 0,
    edge_count       INTEGER NOT NULL DEFAULT 0,
    removed_edges    INTEGER NOT NULL DEFAULT 0,
    byte_size        INTEGER NOT NULL DEFAULT 0,
    validation_json  TEXT NOT NULL DEFAULT '[]',
    passed           INTEGER NOT NULL DEFAULT 0,
    path             TEXT,
    created_at       TEXT NOT NULL,
    fetched_at       TEXT,
    notified_at      TEXT
);

CREATE TABLE IF NOT EXISTS consistency_escalations (
    mismatch_id      TEXT PRIMARY KEY,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS acl_groups (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    description      TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS llm_telemetry (
    id               TEXT PRIMARY KEY,
    episode_id       TEXT NOT NULL,
    prompt_name      TEXT NOT NULL,
    model_name       TEXT NOT NULL,
    latency_ms       INTEGER NOT NULL,
    input_tokens     INTEGER NOT NULL,
    output_tokens    INTEGER NOT NULL,
    total_tokens     INTEGER NOT NULL,
    cost_usd         REAL NOT NULL,
    input_chars      INTEGER NOT NULL,
    nodes_yield      INTEGER NOT NULL,
    edges_yield      INTEGER NOT NULL,
    error            TEXT,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_telemetry_episode ON llm_telemetry(episode_id);
CREATE INDEX IF NOT EXISTS idx_llm_telemetry_created ON llm_telemetry(created_at);

CREATE TABLE IF NOT EXISTS api_keys (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    masked_key       TEXT NOT NULL,
    key_hash         TEXT NOT NULL,
    environment      TEXT NOT NULL DEFAULT 'staging',
    user_id          TEXT,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
    key              TEXT PRIMARY KEY,
    value_json       TEXT NOT NULL
);

-- =========================================================================
-- RBAC Schema (Phases 1-6, created up-front for forward compatibility)
-- =========================================================================

-- Phase 1: Identity & Organizations
CREATE TABLE IF NOT EXISTS organizations (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    slug             TEXT NOT NULL UNIQUE,
    status           TEXT NOT NULL DEFAULT 'active',
    created_by       TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id               TEXT PRIMARY KEY,
    email            TEXT NOT NULL UNIQUE,
    display_name     TEXT,
    password_hash    TEXT,
    auth_provider    TEXT NOT NULL DEFAULT 'local',
    external_subject TEXT,
    status           TEXT NOT NULL DEFAULT 'pending_invitation',
    email_verified   INTEGER NOT NULL DEFAULT 0,
    invited_by       TEXT,
    invited_at       TEXT,
    accepted_at      TEXT,
    last_login_at    TEXT,
    invitation_token_hash TEXT,
    invitation_token_expires TEXT,
    password_reset_token_hash TEXT,
    password_reset_token_expires TEXT,
    is_active        INTEGER NOT NULL DEFAULT 1,
    token_version    INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);
CREATE INDEX IF NOT EXISTS idx_users_invitation_token ON users(invitation_token_hash);

CREATE TABLE IF NOT EXISTS organization_members (
    user_id          TEXT NOT NULL,
    membership_status TEXT NOT NULL DEFAULT 'active',
    joined_at        TEXT NOT NULL,
    invited_by       TEXT,
    updated_at       TEXT NOT NULL,
    PRIMARY KEY (user_id)
);

CREATE TABLE IF NOT EXISTS sessions (
    id               TEXT PRIMARY KEY,
    user_id          TEXT NOT NULL,
    refresh_token_hash TEXT NOT NULL,
    issued_at        TEXT NOT NULL,
    expires_at       TEXT NOT NULL,
    revoked_at       TEXT,
    replaced_by_session_id TEXT,
    user_agent       TEXT NOT NULL DEFAULT '',
    ip_hash          TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(refresh_token_hash);

CREATE TABLE IF NOT EXISTS roles (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    description      TEXT NOT NULL DEFAULT '',
    clearance        TEXT NOT NULL DEFAULT 'internal',
    is_system_role   INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    UNIQUE(name)
);

CREATE TABLE IF NOT EXISTS user_roles (
    id               TEXT PRIMARY KEY,
    user_id          TEXT NOT NULL,
    role_id          TEXT NOT NULL,
    granted_at       TEXT NOT NULL,
    granted_by       TEXT,
    valid_from       TEXT NOT NULL,
    valid_to         TEXT,
    revoked_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_user_roles_user ON user_roles(user_id);
CREATE INDEX IF NOT EXISTS idx_user_roles_role ON user_roles(role_id);

CREATE TABLE IF NOT EXISTS authorization_versions (
    id               TEXT PRIMARY KEY,
    version          INTEGER NOT NULL DEFAULT 1,
    updated_at       TEXT NOT NULL
);

-- Phase 2: Knowledge Scopes & Versioning
CREATE TABLE IF NOT EXISTS knowledge_scopes (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    description      TEXT NOT NULL DEFAULT '',
    owner_user_id    TEXT,
    status           TEXT NOT NULL DEFAULT 'proposed',
    sensitivity      TEXT NOT NULL DEFAULT 'internal',
    active_version_id TEXT,
    review_status    TEXT NOT NULL DEFAULT 'current',
    discovery_origin TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    deprecated_at    TEXT,
    UNIQUE(name)
);

CREATE TABLE IF NOT EXISTS knowledge_scope_versions (
    id               TEXT PRIMARY KEY,
    scope_id         TEXT NOT NULL,
    version_number   INTEGER NOT NULL,
    status           TEXT NOT NULL DEFAULT 'candidate',
    boundary_kind    TEXT,
    boundary_spec_json TEXT,
    member_entity_count INTEGER NOT NULL DEFAULT 0,
    member_edge_count INTEGER NOT NULL DEFAULT 0,
    proposed_by      TEXT,
    approved_by      TEXT,
    created_at       TEXT NOT NULL,
    materialized_at  TEXT,
    approved_at      TEXT,
    rejected_at      TEXT,
    change_summary   TEXT NOT NULL DEFAULT '',
    entity_drift_pct REAL NOT NULL DEFAULT 0,
    edge_drift_pct   REAL NOT NULL DEFAULT 0,
    previous_version_id TEXT,
    UNIQUE(scope_id, version_number)
);

CREATE TABLE IF NOT EXISTS scope_entities (
    scope_version_id TEXT NOT NULL,
    entity_uuid      TEXT NOT NULL,
    source_reason    TEXT NOT NULL DEFAULT '',
    included_at      TEXT NOT NULL,
    PRIMARY KEY (scope_version_id, entity_uuid)
);
CREATE INDEX IF NOT EXISTS idx_scope_entities_uuid ON scope_entities(entity_uuid);
CREATE INDEX IF NOT EXISTS idx_scope_entities_version ON scope_entities(scope_version_id);

CREATE TABLE IF NOT EXISTS scope_edges (
    scope_version_id TEXT NOT NULL,
    edge_uuid        TEXT NOT NULL,
    source_entity_uuid TEXT,
    target_entity_uuid TEXT,
    access_tag       TEXT NOT NULL DEFAULT 'internal',
    source_reason    TEXT NOT NULL DEFAULT '',
    included_at      TEXT NOT NULL,
    PRIMARY KEY (scope_version_id, edge_uuid)
);
CREATE INDEX IF NOT EXISTS idx_scope_edges_uuid ON scope_edges(edge_uuid);
CREATE INDEX IF NOT EXISTS idx_scope_edges_version ON scope_edges(scope_version_id);

-- Phase 3: Teams & Scope Assignments
CREATE TABLE IF NOT EXISTS teams (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    description      TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    UNIQUE(name)
);

CREATE TABLE IF NOT EXISTS team_members (
    id               TEXT PRIMARY KEY,
    team_id          TEXT NOT NULL,
    user_id          TEXT NOT NULL,
    joined_at        TEXT NOT NULL,
    removed_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_team_members_user ON team_members(user_id);
CREATE INDEX IF NOT EXISTS idx_team_members_team ON team_members(team_id);

CREATE TABLE IF NOT EXISTS scope_assignees (
    id               TEXT PRIMARY KEY,
    scope_id         TEXT NOT NULL,
    assignee_kind    TEXT NOT NULL,
    assignee_id      TEXT NOT NULL,
    granted_by       TEXT,
    valid_from       TEXT NOT NULL,
    valid_to         TEXT,
    revoked_at       TEXT,
    created_at       TEXT NOT NULL,
    reason           TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_scope_assignees_scope ON scope_assignees(scope_id);
CREATE INDEX IF NOT EXISTS idx_scope_assignees_kind ON scope_assignees(assignee_kind, assignee_id);

-- Phase 6: Discovery Proposals
CREATE TABLE IF NOT EXISTS scope_proposals (
    id               TEXT PRIMARY KEY,
    suggested_name   TEXT NOT NULL,
    description      TEXT NOT NULL DEFAULT '',
    discovery_method TEXT NOT NULL,
    confidence       REAL NOT NULL DEFAULT 0.0,
    boundary_spec_json TEXT,
    sample_entities_json TEXT,
    source_distribution_json TEXT,
    estimated_entity_count INTEGER NOT NULL DEFAULT 0,
    estimated_edge_count INTEGER NOT NULL DEFAULT 0,
    suggested_sensitivity TEXT NOT NULL DEFAULT 'internal',
    status           TEXT NOT NULL DEFAULT 'open',
    created_at       TEXT NOT NULL,
    decided_at       TEXT,
    decided_by       TEXT,
    rejection_reason TEXT,
    merged_into_proposal_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_scope_proposals_status ON scope_proposals(status);

-- Monotonic counters backing the observability metrics (R2, R7).
CREATE TABLE IF NOT EXISTS counters (
    name             TEXT PRIMARY KEY,
    value            INTEGER NOT NULL DEFAULT 0
);

-- GitHub Connector Tables
CREATE TABLE IF NOT EXISTS github_installations (
    installation_id       TEXT PRIMARY KEY,
    account_login         TEXT NOT NULL,
    account_type          TEXT NOT NULL,
    permissions_json      TEXT NOT NULL DEFAULT '{}',
    repository_selection  TEXT NOT NULL,
    suspended_at          TEXT,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS github_repositories (
    id                     TEXT PRIMARY KEY,
    installation_id        TEXT NOT NULL,
    tenant_id              TEXT NOT NULL,
    github_repo_id         TEXT NOT NULL,
    owner                  TEXT NOT NULL,
    name                   TEXT NOT NULL,
    full_name              TEXT NOT NULL,
    private                INTEGER NOT NULL DEFAULT 0,
    default_branch         TEXT NOT NULL,
    language               TEXT,
    description            TEXT,
    poll_interval_hours    INTEGER NOT NULL,
    last_synced_commit_sha TEXT,
    last_synced_at         TEXT,
    status                 TEXT NOT NULL DEFAULT 'READY',
    sync_enabled           INTEGER NOT NULL DEFAULT 1,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL,
    UNIQUE(github_repo_id)
);
CREATE INDEX IF NOT EXISTS idx_github_repos_installation ON github_repositories(installation_id);
CREATE INDEX IF NOT EXISTS idx_github_repos_tenant ON github_repositories(tenant_id);

CREATE TABLE IF NOT EXISTS github_file_state (
    repo_id            TEXT NOT NULL,
    file_path          TEXT NOT NULL,
    entity_uuid        TEXT,
    current_blob_sha   TEXT NOT NULL,
    current_commit_sha TEXT NOT NULL,
    last_content_hash  TEXT NOT NULL,
    last_synced_at     TEXT NOT NULL,
    status             TEXT NOT NULL,
    last_error         TEXT,
    PRIMARY KEY (repo_id, file_path)
);

CREATE TABLE IF NOT EXISTS github_commits (
    repo_id          TEXT NOT NULL,
    commit_sha       TEXT NOT NULL,
    parent_shas_json TEXT NOT NULL DEFAULT '[]',
    author_login     TEXT,
    author_name      TEXT,
    author_email     TEXT,
    message          TEXT,
    committed_at     TEXT NOT NULL,
    synced_at        TEXT NOT NULL,
    status           TEXT NOT NULL,
    PRIMARY KEY (repo_id, commit_sha)
);

CREATE TABLE IF NOT EXISTS github_sync_runs (
    id                TEXT PRIMARY KEY,
    repo_id           TEXT NOT NULL,
    installation_id   TEXT NOT NULL,
    started_at        TEXT NOT NULL,
    completed_at      TEXT,
    status            TEXT NOT NULL,
    commits_processed INTEGER NOT NULL DEFAULT 0,
    files_added       INTEGER NOT NULL DEFAULT 0,
    files_modified    INTEGER NOT NULL DEFAULT 0,
    files_deleted     INTEGER NOT NULL DEFAULT 0,
    files_renamed     INTEGER NOT NULL DEFAULT 0,
    files_skipped     INTEGER NOT NULL DEFAULT 0,
    episodes_created  INTEGER NOT NULL DEFAULT 0,
    nodes_created     INTEGER NOT NULL DEFAULT 0,
    edges_created     INTEGER NOT NULL DEFAULT 0,
    error             TEXT,
    trigger           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_github_sync_runs_repo ON github_sync_runs(repo_id);

CREATE TABLE IF NOT EXISTS github_webhook_events (
    id                 TEXT PRIMARY KEY,
    github_delivery_id TEXT NOT NULL,
    event_type         TEXT NOT NULL,
    repo_id            TEXT,
    received_at        TEXT NOT NULL,
    processed_at       TEXT,
    status             TEXT NOT NULL,
    payload_hash       TEXT NOT NULL,
    error              TEXT,
    UNIQUE(github_delivery_id)
);

CREATE TABLE IF NOT EXISTS github_agent_webhooks (
    repo_id        TEXT PRIMARY KEY,
    webhook_url    TEXT NOT NULL,
    secret         TEXT,
    events_json    TEXT NOT NULL DEFAULT '[]',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    type              TEXT NOT NULL DEFAULT 'github_repo',
    source_type       TEXT NOT NULL DEFAULT 'github',
    source_identifier TEXT,
    display_name      TEXT,
    description       TEXT,
    repo_url          TEXT,
    repo_full_name    TEXT,
    repo_id           TEXT,
    default_branch    TEXT NOT NULL DEFAULT 'main',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_repositories (
    project_id        TEXT NOT NULL,
    repo_id           TEXT NOT NULL,
    PRIMARY KEY (project_id, repo_id)
);

-- ── Mail Integration ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS mail_accounts (
    id                      TEXT PRIMARY KEY,
    tenant_id               TEXT NOT NULL,
    user_id                 TEXT,
    provider                TEXT NOT NULL DEFAULT 'gmail',
    email_address           TEXT NOT NULL,
    display_name            TEXT,
    -- OAuth tokens
    access_token            TEXT,
    refresh_token           TEXT,
    token_expiry            TEXT,
    -- Sync config
    poll_interval_minutes   INTEGER NOT NULL DEFAULT 15,
    max_history_days        INTEGER NOT NULL DEFAULT 90,
    label_filter            TEXT,
    -- State
    status                  TEXT NOT NULL DEFAULT 'ACTIVE',
    last_synced_at          TEXT,
    history_id              TEXT,
    error_message           TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    UNIQUE(tenant_id, email_address)
);
CREATE INDEX IF NOT EXISTS idx_mail_accounts_tenant ON mail_accounts(tenant_id);
CREATE INDEX IF NOT EXISTS idx_mail_accounts_status ON mail_accounts(status);

CREATE TABLE IF NOT EXISTS mail_sync_runs (
    id               TEXT PRIMARY KEY,
    account_id       TEXT NOT NULL REFERENCES mail_accounts(id),
    status           TEXT NOT NULL,
    messages_fetched INTEGER NOT NULL DEFAULT 0,
    chunks_ingested  INTEGER NOT NULL DEFAULT 0,
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    error            TEXT
);
CREATE INDEX IF NOT EXISTS idx_mail_sync_runs_account ON mail_sync_runs(account_id);
"""


class Database:
    """Owns a single aiosqlite connection with WAL enabled."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        if self._conn is not None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self._path, isolation_level=None)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.executescript(SCHEMA)
        try:
            await conn.execute("ALTER TABLE github_file_state ADD COLUMN entity_uuid TEXT;")
        except Exception:
            pass
        self._conn = conn

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database.connect() has not been awaited")
        return self._conn

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> None:
        await self.conn.execute(sql, tuple(params))

    async def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> None:
        await self.conn.executemany(sql, [tuple(r) for r in rows])

    async def fetch_all(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        async with self.conn.execute(sql, tuple(params)) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def fetch_one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        async with self.conn.execute(sql, tuple(params)) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def fetch_value(self, sql: str, params: Iterable[Any] = (), default: Any = None) -> Any:
        row = await self.fetch_one(sql, params)
        if not row:
            return default
        return next(iter(row.values()), default)

    async def increment(self, name: str, amount: int = 1) -> None:
        await self.execute(
            "INSERT INTO counters(name, value) VALUES(?, ?) "
            "ON CONFLICT(name) DO UPDATE SET value = value + excluded.value",
            (name, amount),
        )

    async def counter(self, name: str) -> int:
        return int(await self.fetch_value("SELECT value FROM counters WHERE name = ?", (name,), 0) or 0)

    async def counters(self) -> dict[str, int]:
        rows = await self.fetch_all("SELECT name, value FROM counters")
        return {row["name"]: int(row["value"]) for row in rows}

    async def get_setting(self, key: str, default: Any = None) -> Any:
        raw = await self.fetch_value("SELECT value_json FROM app_settings WHERE key = ?", (key,))
        return json.loads(raw) if raw is not None else default

    async def set_setting(self, key: str, value: Any) -> None:
        await self.execute(
            "INSERT INTO app_settings(key, value_json) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
            (key, json.dumps(value)),
        )


@asynccontextmanager
async def open_database(path: Path) -> AsyncIterator[Database]:
    db = Database(path)
    await db.connect()
    try:
        yield db
    finally:
        await db.close()
