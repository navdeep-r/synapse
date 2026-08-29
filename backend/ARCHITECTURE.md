# Synapse v2 Backend Architecture

## Overview

Synapse v2 is a **Graphiti-backed temporal knowledge graph platform** that ingests enterprise documents (uploads, GitHub repos, Gmail threads, Slack, Notion), extracts a **closed-vocabulary ontology**, resolves entities, and maintains **bi-temporal fact validity**. The backend is built as a FastAPI application with an in-process asyncio pipeline, SQLite for metadata/observability, and FalkorDB/Neo4j for the graph layer.

---

## System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              FASTAPI APPLICATION                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐    │
│  │   Auth       │  │   Chat       │  │   Graph      │  │   Sources    │    │
│  │   Router     │  │   Router     │  │   Router     │  │   Router     │    │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘    │
│         │                 │                 │                 │             │
│         └─────────────────┼─────────────────┼─────────────────┘             │
│                           ▼                                               │
│              ┌────────────────────────┐                                  │
│              │   require_principal    │  ← JWT/API Key/Legacy resolution │
│              │   (kg_acl/principal)   │                                  │
│              └────────────┬───────────┘                                  │
│                           │                                               │
│              ┌────────────┴───────────┐                                  │
│              │   AuthorizedGraph      │  ← RBAC filtering (kg_acl)       │
│              │   Repository           │                                  │
│              └────────────┬───────────┘                                  │
│                           │                                               │
└───────────────────────────┼───────────────────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           APPLICATION STATE                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│  │  Database   │  │  Graph      │  │  Pipeline   │  │  Stage      │        │
│  │  (SQLite)   │  │  Service    │  │  (asyncio)  │  │  Tracker    │        │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘        │
└───────────────────────────┬─────────────────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│   GitHub      │   │   Mail        │   │   Upload      │
│   Connector   │   │   Connector   │   │   Pipeline    │
│   (Polling +  │   │   (Gmail API) │   │   (Direct)    │
│    Webhooks)  │   │               │   │               │
└───────┬───────┘   └───────┬───────┘   └───────┬───────┘
        │                   │                   │
        └───────────────────┼───────────────────┘
                            ▼
              ┌─────────────────────────┐
              │  kg_parsing             │  ← PDF, DOCX, HTML, code extraction
              │  parse_document()       │
              └───────────┬─────────────┘
                          │
                          ▼
              ┌─────────────────────────┐
              │  kg_chunking            │  ← Paragraph chunks + provenance
              │  chunk_document()       │
              └───────────┬─────────────┘
                          │
                          ▼
              ┌─────────────────────────┐
              │  kg_prefilter           │  ← Dedup, boilerplate drop, batching
              │  prefilter_chunks()     │
              │  build_episodes()       │
              └───────────┬─────────────┘
                          │
                          ▼
              ┌─────────────────────────┐
              │  Graphiti Core          │  ← LLM extraction (expensive)
              │  add_episode()          │     Closed ontology enforced
              └───────────┬─────────────┘
                          │
                          ▼
              ┌─────────────────────────┐
              │  kg_observability       │  ← Receipts, telemetry, reconciliation
              │  ReceiptStore           │
              │  StageTracker           │
              │  TelemetryStore         │
              └───────────┬─────────────┘
                          │
                          ▼
              ┌─────────────────────────┐
              │  kg_acl (RBAC)          │  ← Scope filtering on reads
              │  AuthorizedGraph        │
              │  require_principal      │
              └───────────┬─────────────┘
                          │
                          ▼
              ┌─────────────────────────┐
              │  kg_export              │  ← Snapshots + ACL policies
              │  SnapshotBuilder        │
              │  apply_acl_policy()     │
              └─────────────────────────┘
```

---

## Core Modules

### 1. Application Entry & Configuration (`app/`)

| File | Responsibility |
|------|----------------|
| `main.py` | FastAPI factory, lifespan, middleware, router registration (18 routers), health endpoint |
| `config.py` | `Settings` class (Pydantic BaseSettings) — all config via `SYNAPSE_*` env vars |
| `db.py` | SQLite metadata store (50+ tables), WAL mode, foreign keys, schema migrations |
| `state.py` | `AppState` — shared singleton: Database, GraphService, Pipeline, StageTracker, GitHubReconciler |
| `pipeline.py` | 7-stage async ingestion pipeline (queue + single worker) |
| `security.py` | Legacy `require_auth` (deprecated), `warn_if_exposed` |
| `security_middleware.py` | `SecurityHeadersMiddleware` (CSP, HSTS), `RateLimitMiddleware` (100 req/min) |

**Lifespan Flow** (`main.py:25-56`):
```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    state = await AppState.create(settings)      # DB + Graph + Pipeline
    await OntologyRegistry(state.db).seed()      # Seed closed ontology
    await _seed_default_sources(state)           # Default sources
    await run_bootstrap(state.db, settings)      # RBAC bootstrap
    warn_if_exposed(settings)
    state.start_graph_in_background()            # Non-blocking graph boot
    state.start_github_reconciler()              # Background GitHub polling
    state.pipeline.start()                       # Ingestion worker
    yield
    await state.close()                          # Graceful shutdown
```

### 2. Ingestion Pipeline (`app/pipeline.py`)

**7-Stage Orchestrator** — asyncio queue + single worker with retry/backoff:

| Stage | Description | Key Operations |
|-------|-------------|----------------|
| **1. Ingestion** | Persist document, create deterministic `Document` node (except GitHub/Mail) | `documents.save()`, `sources.ensure()`, `add_deterministic_nodes_and_edges()` |
| **2. Chunking** | Paragraph-based chunking with provenance | `kg_chunking.chunk_document()`, `documents.save_chunks()` |
| **3. Extraction** | Pre-filter → batch episodes → Graphiti `add_episode()` | `kg_prefilter.prefilter_chunks()`, `build_episodes()`, `graph.add_episode()` |
| **4. Resolution** | Entity deduplication (inside Graphiti) | Handled by Graphiti's temporal write |
| **5. Consolidation** | Edge invalidation (newer facts supersede) | Handled by Graphiti, tracked via `invalidated_edges` |
| **6. Community Detection** | Graphiti community building | `graph.build_communities()` |
| **7. Export** | Snapshot persistence | `graph.persist()`, `persist_stages()` |

**Key Design Decisions**:
- **Single worker** — sequential processing guarantees ordering; `drain()` for tests
- **Retry with exponential backoff** (3 attempts, `2^attempt` seconds)
- **Job recovery** — crashed `processing` jobs reset to `pending` on startup
- **Per-document snapshot** — `graph.persist()` after each document limits blast radius

### 3. Graph Layer (`kg_graphiti/`)

| File | Responsibility |
|------|----------------|
| `service.py` | `GraphService` — Graphiti client assembly, LLM/embedder builders, episode writes, graph reads |
| `ontology.py` | **Closed vocabulary**: 30 entity types, 30 edge types, 70+ `EDGE_TYPE_MAP` constraints |
| `drivers.py` | FalkorDB/Neo4j driver factory with redislite support |
| `embedder.py` | `LocalSentenceTransformerEmbedder` (google/embeddinggemma-300m) |
| `llm_stub.py` | Stub LLM for testing / key-free operation |
| `extraction.py` / `resolution.py` | LLM prompt templates for entity/edge extraction & resolution |
| `falkor_sanitize.py` | Sanitizes property maps for FalkorDB compatibility |
| `llm_metrics.py` | LLM call instrumentation (latency, tokens, cost) |
| `reranker.py` | `LexicalCrossEncoder` for hybrid search re-ranking |

**GraphService Key Methods**:
```python
async def add_episode(self, payload: EpisodePayload) -> EpisodeWriteResult:
    # Enforces closed ontology unless dynamic_extraction=True
    # Records LLM calls, latency, nodes/edges created, invalidated edges

async def query(self, cypher: str, group_id: str | None = None, ...) -> list[dict]:
    # Multi-graph tenancy: FalkorDB clones driver per group_id; Neo4j uses property

async def add_deterministic_nodes_and_edges(self, group_id, nodes_data, edges_data):
    # Direct insertion without LLM (GitHub AST, Mail)
```

**Multi-Graph Tenancy** (`service.py:436-447`):
```python
def _driver_for(self, group_id: str | None) -> Any:
    if group_id is None or self.settings.graph_backend == "neo4j":
        return self._driver
    return self._driver.clone(group_id)  # FalkorDB: separate graph per tenant
```

### 4. Closed Ontology (`kg_graphiti/ontology.py`)

**30 Entity Types** — constrained extraction vocabulary:
- **People/Org**: `Employee`, `Organization`, `Developer`
- **Code**: `GitRepository`, `GitFile`, `GitCommit`, `GitPullRequest`, `GitIssue`, `CodeClass`, `CodeFunction`, `CodeMethod`, `CodeInterface`, `TypeDefinition`, `APIEndpoint`, `DataModel`, `PackageDependency`, `License`, `SecurityAdvisory`
- **CI/CD**: `CIWorkflow`, `CIJob`, `TestSuite`, `DeploymentTarget`
- **Git**: `GitBranch`, `GitRelease`
- **General**: `Repository`, `Product`, `Document`

**30 Edge Types** with `EDGE_TYPE_MAP` enforcing valid `(source_type, target_type)` pairs. Example:
```python
("GitRepository", "GitFile"): ["CONTAINS"],
("GitCommit", "GitFile"): ["MODIFIES"],
("GitPullRequest", "GitFile"): ["MODIFIES", "AFFECTS"],
("GitIssue", "CodeClass"): ["AFFECTS"],
("CodeFunction", "CodeFunction"): ["CALLS"],
```

**Entity/Edge Models** include `GraphitiElement` base with:
- `source_id`, `source_type`, `source_metadata`
- `source_classification` (agent routing)
- `project_context` (multi-project)
- `github_context` (GitHub-specific)

### 5. Observability (`kg_observability/`)

| Component | Purpose |
|-----------|---------|
| `ReceiptStore` | **Intent-then-commit (R3)**: PENDING receipt before Graphiti → COMMITTED/FAILED after |
| `StageTracker` | Funnel counters for 7 stages (persisted to `counters` table) |
| `TelemetryStore` | Per-LLM-call metrics in `llm_telemetry` (tokens, cost, latency, yield) |
| `reconcile.py` | Detects 3 divergence classes: `stuck_pending`, `missing_in_graph`, `missing_in_metadata` |

**Receipt Lifecycle**:
```sql
-- Before Graphiti call
INSERT INTO episode_receipts (episode_id, status='PENDING', ...)

-- After Graphiti returns
UPDATE episode_receipts SET status='COMMITTED', graph_uuid=?, nodes_created=?, ...
```

### 6. RBAC System (`kg_acl/`)

**Complete implementation** (Phases 1-6):

| File | Responsibility |
|------|----------------|
| `models.py` | `Sensitivity` enum (PUBLIC=0, INTERNAL=1, CONFIDENTIAL=2, RESTRICTED=3), `Principal`, `AccessContext` |
| `principal.py` | `require_principal` — JWT → API Key → Legacy → Bootstrap supervisor resolution |
| `resolver.py` | `resolve_effective_scopes()` — role grants + team grants + direct assignments |
| `authorized_graph.py` | `AuthorizedGraphRepository` — wraps GraphService, filters entities/edges by scope |
| `bootstrap.py` | Idempotent first-run: admin user, roles, bootstrap scope |
| `discovery.py` | Automated scope discovery (structural BFS + LLM-guided) |
| `materializer.py` | Materializes scope boundaries into `scope_entities`/`scope_edges` |
| `drift.py` | Detects scope drift, proposes new versions |
| `sessions.py` | Refresh token management (SHA-256 storage, rotation, revocation) |
| `schemas.py` | Pydantic models for 7 boundary kinds + Phase 3 schemas |

**Auth Resolution Order** (`principal.py:7-12`):
1. Bearer JWT access token → decode → load user + roles → Principal
2. Bearer API key → hash-lookup in `api_keys` → resolve user_id → Principal
3. Legacy `SYNAPSE_AUTH_TOKEN` shared secret (transition mode only)
4. No auth + transition mode → bootstrap supervisor
5. Otherwise → 401

**Scope Filtering** (`authorized_graph.py:50-79`):
- Non-supervisors: queries filtered to `allowed_entity_uuids` / `allowed_edge_uuids`
- Clearance check: `resource_sensitivity <= principal_clearance`
- Raw Cypher **denied** for non-supervisors (403)

### 7. GitHub Connector (`github/`)

| File | Responsibility |
|------|----------------|
| `client.py` | `GitHubAppClient` — JWT auth, installation token caching, REST methods |
| `ast_parsers.py` | `parse_code_file()` — Python AST + regex for 10+ languages |
| `file_classifier.py` | `FileSignificance` scoring, `should_include_file()`, architectural role detection |
| `llm.py` | Multi-provider LLM (OpenAI/Groq/NVIDIA/Ollama) + disk cache (`.llm_cache.json`) |
| `connector.py` | `GitHubSyncJob` — initial/incremental sync, commit/PR/issue processing |
| `batch_helper.py` | Batch file processing: pipeline + AST + LLM summaries + graph injection |
| `reconciliation.py` | Background polling for stale repos |
| `webhook.py` | GitHub webhook handler (HMAC verification) |

**Sync Flow** (`connector.py:202-530`):
```
Initial:   GET /tree (recursive) → filter files → batch process
Incremental: GET /compare (base...head) → process changed files
Per-file:    Pipeline submit + AST parse → deterministic nodes/edges
Commits:     GitCommit nodes + GitHub file MODIFIES edges
PRs/Issues:  LLM attention analysis → need_attention flag + AFFECTS edges
```

### 8. Mail Connector (`mail/`)

| File | Responsibility |
|------|----------------|
| `client_gmail.py` | `GmailClient` — OAuth2 auto-refresh, History API, message/thread parsing |
| `connector.py` | `MailSyncJob` — incremental (history_id) or full sync (max_history_days) |
| `models.py` | `MailAccount`, `MailSyncRun` |

**Email KG Extraction** (`connector.py:184-342`):
- `Email` nodes with metadata (from, to, subject, labels, attachments)
- `Person` nodes for senders/recipients (MD5-hashed email for UUID)
- Edges: `SENT_BY`, `SENT_TO`, `REPLIES_TO` (thread ordering)
- `need_attention` flag for customer-related / issue keywords

### 9. Document Processing (`kg_parsing/`, `kg_chunking/`, `kg_prefilter/`)

| Module | Key Function | Behavior |
|--------|-------------|----------|
| `kg_parsing.parsers` | `parse_document()` | PyMuPDF/pypdf (PDF), python-docx (DOCX), BeautifulSoup (HTML), native (text/code) |
| `kg_parsing.resume` | `normalize_resume_text()` | Name promotion, contact scrubbing, extraction brief injection |
| `kg_chunking.chunkers` | `chunk_document()` | Paragraph split → sentence break for oversized (target 900, max 1600 chars) |
| `kg_prefilter.filters` | `prefilter_chunks()` | Exact-hash dedup + boilerplate rules (footers, unsubscribe, TOC, etc.) |
| `kg_prefilter.batching` | `build_episodes()` | Consecutive chunks → episodes (target 1200 chars) |

### 10. Export & Snapshots (`kg_export/`)

| File | Responsibility |
|------|----------------|
| `policies.py` | ACL policies: `strict` (public), `balanced` (public+internal), `permissive` (all except restricted) |
| `snapshots.py` | `SnapshotBuilder` — gzipped JSON with 5 validation checks |

**Validation Checks**:
1. No orphan edges (both endpoints exist)
2. Ontology versions resolvable
3. ACL policy applied (provenance required)
4. Payload size within threshold
5. Non-empty graph

---

## Data Models

### Canonical Document (`kg_contracts/models.py`)
```python
class CanonicalDocument:
    document_id: str          # doc-{slug}-{uuid8}
    source_id: str            # upload, github:repo:123, mail:user@domain
    source_kind: SourceKind   # UPLOAD, SLACK, GITHUB, NOTION, EMAIL
    name: str                 # filename or subject
    media_type: str           # text/plain, application/pdf, etc.
    text: str                 # extracted + normalized
    content_hash: str         # SHA-256 of raw bytes
    byte_size: int
    access_tag: str           # public, internal, confidential, restricted
    tenant: str               # tenant-a (default)
    metadata: dict            # domain, category, type, etc.
```

### Episode Payload (`kg_contracts/models.py`)
```python
class EpisodePayload:
    episode_id: str
    name: str
    body: str                 # episode text content
    source_description: str
    reference_time: datetime
    source: EpisodeType       # json or text
    group_id: str             # "synapse" (tenant namespace)
    is_json: bool
```

---

## Key Architectural Patterns

### 1. Intent-Then-Commit (R3)
Every Graphiti write creates a `PENDING` receipt in SQLite **before** the call. On success → `COMMITTED`; on failure → `FAILED`. Reconciliation job detects mismatches.

### 2. Pre-Filtering (R7)
Chunks filtered by rules **before** expensive LLM calls:
- Exact-hash duplicates (seen in prior docs)
- Boilerplate: confidentiality footers, unsubscribe, page markers, copyright, TOC, separators
- Drops logged to `prefilter_drops` table with rule name
- `episodes_avoided` counter tracks LLM calls saved

### 3. Closed Ontology
Extraction constrained to 30 entity types + 30 edge types via `EDGE_TYPE_MAP`. Prevents hallucination, ensures schema compliance. `dynamic_extraction` flag for experimentation only.

### 4. Multi-Graph Tenancy
- **Neo4j**: Single database, `group_id` property filter
- All writes/reads scoped to `group_id="synapse"`

### 5. Deterministic Nodes (GitHub/Mail)
Code intelligence (AST) and email parsing create nodes **directly** via `add_deterministic_nodes_and_edges()` — no LLM extraction. Ensures precision for structured sources.

### 6. Background Graph Boot
`start_graph_in_background()` avoids blocking FastAPI startup on embedded FalkorDB init. Health endpoint reports `graph_ready` status.

### 7. Fallback LLMs
NVIDIA primary → Groq fallback in `GraphService._build_llm_client()`. Chat endpoint has its own candidate chain (NVIDIA → Groq → OpenAI → Ollama).

---

## Authentication & Authorization

### Authentication Methods
| Method | Header | Validation |
|--------|--------|------------|
| JWT Access Token | `Authorization: Bearer <jwt>` | HS256, `jwt_secret`, token_version check |
| API Key | `Authorization: Bearer <key>` | SHA-256 hash lookup in `api_keys` |
| Legacy Token | `Authorization: Bearer <token>` | `SYNAPSE_AUTH_TOKEN` match (transition mode) |
| None (Transition) | — | Bootstrap supervisor principal |

### Token Structure
```python
# Access Token (15 min default, configurable)
{
    "sub": user_id,
    "session_id": "...",
    "token_version": int,
    "purpose": "access",
    "iat": ...,
    "exp": ...
}

# Refresh Token (30 days, stored as SHA-256 in sessions table)
{
    "sub": user_id,
    "session_id": "...",
    "purpose": "refresh",
    "iat": ...,
    "exp": ...
}
```

### RBAC Data Model
```
User ──┬── UserRoles ── Role (clearance: PUBLIC|INTERNAL|CONFIDENTIAL|RESTRICTED)
       └── TeamMembers ── Team ── ScopeAssignees ── KnowledgeScope
                           └── (direct) ScopeAssignees
```

**Scope Resolution** (`resolver.py`):
1. Role grants → scope assignments
2. Team membership → team grants → scope assignments
3. Direct user assignments
4. Union all, filter by clearance ≤ scope sensitivity
5. Supervisors get all approved scopes

---

## API Endpoints (18 Routers)

| Router | Prefix | Auth | Purpose |
|--------|--------|------|---------|
| `auth.py` | `/api/v1/auth` | ❌ | Login, refresh, logout, me, sessions, password reset, invitations |
| `chat.py` | `/api/v1/chat` | ✅ | KG-grounded chat (hybrid Graphiti + Cypher retrieval) |
| `graph.py` | `/api/v1` | ✅ | Topology, entity search, details, history, merge |
| `sources.py` | `/api/v1` | ✅ | Source CRUD, document upload, chunk management |
| `ontology.py` | `/api/v1` | ✅ | Entity/relation types, changes, proposals |
| `curation.py` | `/api/v1` | ✅ | Merge candidates, decisions |
| `export.py` | `/api/v1` | ✅ | Graph export (JSON, GraphML), snapshots |
| `reports.py` | `/api/v1` | ✅ | Consistency reports, reconciliation |
| `summary.py` | `/api/v1` | ✅ | Pipeline metrics, stage counters |
| `observability.py` | `/api/v1` | ✅ | LLM telemetry, stage metrics, receipts |
| `github.py` | `/api/v1` | ✅ | Repos, sync, webhooks, file tree |
| `mail.py` | `/api/v1` | ✅ | Mail accounts, sync, OAuth callbacks |
| `settings.py` | `/api/v1` | ✅ | App settings CRUD |
| `scopes.py` | `/api/v1` | ✅ | Knowledge scopes, versions, proposals |
| `roles.py` | `/api/v1` | ✅ | RBAC roles CRUD |
| `teams.py` | `/api/v1` | ✅ | Teams & members |
| `assignments.py` | `/api/v1` | ✅ | Scope assignees (team/user grants) |
| `users.py` | `/api/v1` | ✅ | User management (invite, activate, list) |

**Auth Pattern**: `auth.py` + `github.webhook_router` unguarded; all others use `Depends(require_principal)`.

---

## Configuration (Environment Variables)

All config via `SYNAPSE_*` prefix (`.env` or environment):

### Core
| Variable | Default | Description |
|----------|---------|-------------|
| `SYNAPSE_HOST` | `127.0.0.1` | Bind address |
| `SYNAPSE_PORT` | `8001` | Port |
| `SYNAPSE_AUTH_TOKEN` | `None` | Legacy shared secret (transition mode) |
| `SYNAPSE_DATA_DIR` | `./var` | Data directory |

### Graph
| Variable | Default | Description |
|----------|---------|-------------|
| `SYNAPSE_GRAPH_BACKEND` | `falkordblite` | `falkordblite`, `falkordb`, `neo4j` |
| `SYNAPSE_GRAPH_DATABASE` | `synapse` | Database name |
| `SYNAPSE_FALKORDB_HOST` | `localhost` | FalkorDB host |
| `SYNAPSE_FALKORDB_PORT` | `6379` | FalkorDB port |
| `SYNAPSE_NEO4J_URI` | `bolt://localhost:7687` | Neo4j URI |

### LLM
| Variable | Default | Description |
|----------|---------|-------------|
| `SYNAPSE_LLM_PROVIDER` | `nvidia` | `stub`, `openai`, `ollama`, `nvidia`, `groq` |
| `SYNAPSE_NVIDIA_API_KEY` | `None` | NVIDIA API key |
| `SYNAPSE_NVIDIA_MODEL` | `nvidia/nemotron-3-ultra-550b-a55b` | Primary model |
| `SYNAPSE_GROQ_API_KEY` | `None` | Groq API key (fallback) |
| `SYNAPSE_OPENAI_API_KEY` | `None` | OpenAI API key |
| `SYNAPSE_OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Ollama endpoint |
| `SYNAPSE_EMBEDDING_DIM` | `1024` | Embedding dimension |

### GitHub
| Variable | Description |
|----------|-------------|
| `SYNAPSE_GITHUB_APP_ID` | GitHub App ID |
| `SYNAPSE_GITHUB_APP_PRIVATE_KEY` | GitHub App private key (PEM) |
| `SYNAPSE_GITHUB_WEBHOOK_SECRET` | Webhook HMAC secret |
| `SYNAPSE_GITHUB_EXTERNAL_AGENT_WEBHOOK_URL` | External agent webhook |
| `SYNAPSE_GITHUB_DEFAULT_POLL_INTERVAL_HOURS` | `6` |

### Mail
| Variable | Description |
|----------|-------------|
| `SYNAPSE_MAIL_GOOGLE_CLIENT_ID` | Google OAuth client ID |
| `SYNAPSE_MAIL_GOOGLE_CLIENT_SECRET` | Google OAuth client secret |
| `SYNAPSE_MAIL_GOOGLE_REDIRECT_URI` | `http://localhost:5173/mail/oauth/callback` |
| `SYNAPSE_MAIL_DEFAULT_POLL_INTERVAL_MINUTES` | `15` |

### RBAC
| Variable | Default | Description |
|----------|---------|-------------|
| `SYNAPSE_JWT_SECRET` | `change-me-in-production` | **Must change in production** |
| `SYNAPSE_ACCESS_TOKEN_TTL_MINUTES` | `10 years` | Access token TTL |
| `SYNAPSE_REFRESH_TOKEN_TTL_DAYS` | `10 years` | Refresh token TTL |
| `SYNAPSE_RBAC_TRANSITION_MODE` | `False` | Legacy compatibility mode |
| `SYNAPSE_BOOTSTRAP_ADMIN_EMAIL` | `None` | Bootstrap admin email |
| `SYNAPSE_BOOTSTRAP_ADMIN_PASSWORD` | `None` | Bootstrap admin password |

---

## Database Schema (SQLite)

### Ingestion & Pipeline
| Table | Purpose |
|-------|---------|
| `documents` | Canonical documents (1 per upload/sync item) |
| `chunks` | Paragraph chunks with provenance |
| `prefilter_drops` | Dropped chunks with rule/outcome |
| `pipeline_jobs` | Job queue (pending/processing/completed/failed) |
| `pipeline_metrics` | Per-job counters (chunks, nodes, edges, LLM calls) |

### Receipts (R3)
| Table | Purpose |
|-------|---------|
| `episode_receipts` | Intent-then-commit for Graphiti episodes |
| `event_receipts` | Idempotency for at-least-once delivery |
| `entity_watermarks` | Highest sequence per entity (CDC) |
| `dead_letters` | Poison messages with full payload |

### Sources & Activity
| Table | Purpose |
|-------|---------|
| `sources` | Source registry (upload, slack-eng, github-platform, etc.) |
| `activity` | Human-readable activity log |

### Ontology & Curation
| Table | Purpose |
|-------|---------|
| `ontology_entity_types` | Seeded entity type descriptions |
| `ontology_relation_types` | Relation constraints (versioned) |
| `ontology_changes` | Migration proposals with impact reports |
| `curation_candidates` | Merge candidates from audit |
| `quarantine` | Anomalies needing review |
| `calibration` | Threshold tuning |

### Snapshots
| Table | Purpose |
|-------|---------|
| `snapshots` | Exported snapshot metadata + validation |
| `consistency_escalations` | Reconciliation mismatches |

### RBAC (Phases 1-6)
| Table | Purpose |
|-------|---------|
| `organizations` | Org metadata |
| `users` | Users (password hash, status, token_version) |
| `organization_members` | Membership |
| `sessions` | Refresh tokens (SHA-256) |
| `roles` | Role definitions (clearance, is_system_role) |
| `user_roles` | Role grants with validity windows |
| `authorization_versions` | AuthZ version counter |
| `knowledge_scopes` | Scope definitions (sensitivity, boundary_kind) |
| `knowledge_scope_versions` | Versioned boundaries (candidate/approved) |
| `scope_entities` | Entity UUIDs in scope version |
| `scope_edges` | Edge UUIDs in scope version |
| `teams` | Team definitions |
| `team_members` | Team membership |
| `scope_assignees` | User/team → scope grants |
| `scope_proposals` | Discovery proposals |

### GitHub
| Table | Purpose |
|-------|---------|
| `github_installations` | GitHub App installations |
| `github_repositories` | Tracked repos + sync state |
| `github_file_state` | Per-file sync state (blob_sha, entity_uuid) |
| `github_commits` | Synced commits |
| `github_sync_runs` | Sync run history |
| `github_webhook_events` | Received webhook events |
| `github_agent_webhooks` | External agent webhooks per repo |

### Mail
| Table | Purpose |
|-------|---------|
| `mail_accounts` | Gmail accounts + OAuth tokens |
| `mail_sync_runs` | Sync run history |

### Counters & Settings
| Table | Purpose |
|-------|---------|
| `counters` | Monotonic counters (observability) |
| `app_settings` | Runtime key-value settings |
| `api_keys` | API key hashes |

---

## Key Flows

### 1. Document Upload → Knowledge Graph
```
POST /api/v1/sources/upload
    │
    ▼
parse_document() → normalized text
    │
    ▼
chunk_document() → chunks with provenance
    │
    ▼
prefilter_chunks() → dedup + boilerplate drop
    │
    ▼
build_episodes() → episodes (target 1200 chars)
    │
    ▼
Pipeline.submit() → queue
    │
    ▼
Worker: add_episode() → Graphiti (LLM extraction)
    │
    ▼
ReceiptStore: PENDING → COMMITTED
    │
    ▼
StageTracker: record stage metrics
    │
    ▼
graph.persist() + persist_stages()
```

### 2. GitHub Sync → Knowledge Graph
```
GitHubSyncJob.run()
    │
    ├─ Initial: GET /tree (recursive) → all blobs
    │
    └─ Incremental: GET /compare (base...head) → changed files
            │
            ▼
    should_include_file() → filter by significance
            │
            ▼
    Batch (20 files): process_file_batch()
            │
            ├─ Pipeline.submit() for each file (text content)
            │
            ├─ parse_code_file() → AST nodes/edges
            │
            ├─ LLM summarize_files_batch() → summaries
            │
            └─ add_deterministic_nodes_and_edges() → Graph
                    │
                    ├─ GitRepository, GitDirectory, GitFile nodes
                    ├─ CodeClass, CodeFunction, CodeMethod from AST
                    ├─ CONTAINS, IMPORTS, CALLS, EXTENDS, IMPLEMENTS edges
                    └─ Commit/PR/Issue nodes + MODIFIES/AFFECTS edges
```

### 3. Mail Sync → Knowledge Graph
```
MailSyncJob.run()
    │
    ├─ Incremental: Gmail History API (history_id)
    │
    └─ Full: list_message_ids (max_history_days)
            │
            ▼
    For each thread:
            │
            ├─ For each message:
            │     │
            │     ├─ message_to_text() → structured plain text
            │     ├─ Pipeline.submit() → Graphiti extraction
            │     │
            │     ├─ Create Email node (metadata: from, to, subject, labels)
            │     ├─ Create Person nodes (From, To, CC) — MD5 email hash
            │     ├─ Edges: SENT_BY, SENT_TO, REPLIES_TO (thread order)
            │     └─ need_attention = customer_label OR issue_keywords
            │
            └─ add_deterministic_nodes_and_edges()
```

### 4. Chat Query → Grounded Answer
```
POST /api/v1/chat
    │
    ▼
Resolve group_ids (workspace or all)
    │
    ▼
Extract last user message → search_query
    │
    ▼
Hybrid Retrieval:
    ├─ Graphiti.search() → semantic edges (2× candidates)
    │
    └─ Direct Cypher (Mail/GitHub/Attention queries):
         MATCH (n:Entity) WHERE ... name/summary CONTAINS query
         → matched_nodes → edges between matched nodes
    │
    ▼
Re-rank by score → deduplicate facts
    │
    ▼
Build context: facts + entity summaries + conversation history
    │
    ▼
LLM synthesis (with fallback chain) → answer
    │
    ▼
Return: answer + citations + facts_used
```

---

## Testing

| Test File | Coverage |
|-----------|----------|
| `test_resume_normalize.py` | Resume parsing: name promotion, contact scrubbing, subject marker |
| `test_ast_parser.py` | Python AST extraction: classes, functions, methods, edges |
| `test_file_classifier.py` | Significance scoring: CRITICAL/SUPPORTING/GENERATED |
| `test_github_client.py` | JWT auth, installation token caching |
| `test_github_connector.py` | Initial/incremental sync, tombstones, commit processing |
| `test_github_reconciliation.py` | Stale repo detection |
| `test_audit.py` | Merge candidates: aliases, density, cross-group, confidence |
| `test_graph_pipeline.py` | End-to-end pipeline |
| `test_contracts.py` | Pydantic model validation |
| `tests/rbac/` | 7 phase RBAC tests |

**Pytest Config** (`pyproject.toml`):
```toml
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "session"
asyncio_default_test_loop_scope = "session"
# Embedded FalkorDB bound to creating loop
```

---

## Identified Issues & Improvement Opportunities

### Critical Issues

1. **Hardcoded JWT Secret** (`config.py:102`)
   ```python
   jwt_secret: str = "change-me-in-production"
   ```
   **Fix**: Require `SYNAPSE_JWT_SECRET` in production, fail fast if default detected.

2. **No Database Migration System**
   - Schema changes via `ALTER TABLE` in `db.py:709` (ad-hoc)
   - No version tracking, no rollback capability
   - **Fix**: Add Alembic or custom migration runner with version table.

3. **Pipeline Worker Single Point of Failure**
   - Single asyncio task; crash loses in-flight job (though receipts recover)
   - No horizontal scaling
   - **Fix**: Consider Redis-backed queue (Celery/RQ) for production.

4. **Graphiti LLM Client Not Fully Instrumented**
   - `llm_metrics.instrument()` wraps but stub client counts its own calls
   - Double-counting risk when wrapping stub
   - **Fix**: Unify instrumentation path.

### High Priority

5. **Chat Endpoint LLM Fallback Chain Duplicates GraphService Logic**
   - `chat.py:350-391` has own provider chain (NVIDIA→Groq→OpenAI→Ollama)
   - `GraphService._build_llm_client()` has separate chain (NVIDIA→Groq fallback)
   - **Fix**: Centralize LLM client factory; share across chat + Graphiti.

6. **No Request Validation on Upload Size**
   - `max_upload_bytes` config exists (50MB) but not enforced in `sources.py`
   - **Fix**: Add FastAPI `File(..., max_size=...)` or middleware check.

7. **SQL Injection Risk in Dynamic SQL**
   - `authorized_graph.py:57-58`, `77-78` use f-strings for `IN` placeholders
   - While params are bound, construction is fragile
   - **Fix**: Use helper to build safe placeholders.

8. **Gmail Token Storage in Plaintext**
   - `mail_accounts` table stores `access_token`, `refresh_token` unencrypted
   - **Fix**: Encrypt at rest (Fernet/AES-GCM) with `SYNAPSE_ENCRYPTION_KEY`.

### Medium Priority

9. **Rate Limiting Too Coarse**
   - Global 100 req/min per IP
   - No per-user/per-endpoint limits
   - **Fix**: Tiered limits (auth: stricter, webhooks: exempt).

10. **No Structured Logging / Correlation IDs**
    - Basic `logging.basicConfig` in `main.py:17`
    - No request ID propagation, no JSON output
    - **Fix**: Add `structlog` or `logging` formatter with request context.

11. **Pipeline `pending` Property Broken**
    ```python
    @property
    def pending(self) -> int:
        return 0  # Comment admits it doesn't work
    ```
    - **Fix**: Implement proper count or remove.

12. **Chat Endpoint No Streaming**
    - Single large response; no token streaming
    - **Fix**: Add SSE/streaming support for better UX.

13. **Missing OpenAPI Documentation for Some Endpoints**
    - Webhook, some internal endpoints lack descriptions
    - **Fix**: Add docstrings + response models.

14. **No Health Check Dependencies**
    - `/health` reports `graph_ready` but not DB, LLM, connectors
    - **Fix**: Add `db_ready`, `llm_ready`, `github_ready`, `mail_ready`.

### Low Priority / Nice-to-Have

15. **Duplicate `SourceKind` Values**
    - `kg_contracts` has `SourceKind` enum; `github/connector.py` imports it
    - But `mail/connector.py` uses `SourceKind.EMAIL` directly
    - **Fix**: Ensure single source of truth.

16. **Inconsistent Error Handling Patterns**
    - Some routers catch `Exception` → 500; others let propagate
    - **Fix**: Standardize with `@app.exception_handler` + custom exceptions.

17. **Pre-Filter Rules Hardcoded**
    - `kg_prefilter/filters.py` has fixed regex patterns
    - **Fix**: Make configurable via `app_settings` or ontology.

18. **Community Detection Runs Synchronously in Pipeline**
    - `build_communities()` called per-document (expensive)
    - **Fix**: Schedule as background job, decouple from ingestion.

19. **No Metrics Export (Prometheus/OpenTelemetry)**
    - Counters in SQLite only
    - **Fix**: Add `/metrics` endpoint or OTel exporter.

20. **GitHub Webhook No Retry/Dead Letter**
    - Failed webhook processing → logged but not retried
    - **Fix**: Use `event_receipts` pattern for webhook idempotency.

---

## Deployment Considerations

### Production Checklist
- [ ] Set `SYNAPSE_JWT_SECRET` to strong random value
- [ ] Set `SYNAPSE_RBAC_TRANSITION_MODE=false`
- [ ] Set `SYNAPSE_BOOTSTRAP_ADMIN_EMAIL` + `PASSWORD`
- [ ] Configure external FalkorDB/Neo4j (not `falkordblite`)
- [ ] Set `SYNAPSE_LLM_PROVIDER` + API keys (NVIDIA/OpenAI/Groq)
- [ ] Configure GitHub App credentials
- [ ] Configure Google OAuth for Gmail
- [ ] Enable HTTPS (reverse proxy: nginx/Traefik)
- [ ] Set up log aggregation (ELK/Loki)
- [ ] Configure backup for SQLite + graph DB
- [ ] Set resource limits (memory/CPU) for containers

### Scaling Considerations
| Component | Current | Production Recommendation |
|-----------|---------|---------------------------|
| Pipeline | Single in-process worker | Celery + Redis queue |
| Graph | Embedded FalkorDB | Standalone FalkorDB cluster or Neo4j |
| LLM | Direct HTTP calls | Gateway (LiteLLM) for routing/failover |
| Auth | SQLite sessions | Redis-backed sessions for multi-instance |
| Rate Limit | In-memory per-IP | Redis-backed distributed rate limit |

---

## Appendix: Key Files Quick Reference

| Area | Key Files |
|------|-----------|
| **Entry Point** | `app/main.py`, `app/config.py` |
| **Pipeline** | `app/pipeline.py`, `kg_prefilter/filters.py`, `kg_prefilter/batching.py` |
| **Graph** | `kg_graphiti/service.py`, `kg_graphiti/ontology.py`, `kg_graphiti/drivers.py` |
| **RBAC** | `kg_acl/principal.py`, `kg_acl/authorized_graph.py`, `kg_acl/resolver.py` |
| **GitHub** | `github/connector.py`, `github/ast_parsers.py`, `github/llm.py` |
| **Mail** | `mail/connector.py`, `mail/client_gmail.py` |
| **Observability** | `kg_observability/receipts.py`, `kg_observability/stages.py` |
| **Export** | `kg_export/snapshots.py`, `kg_export/policies.py` |
| **Tests** | `tests/*.py`, `tests/rbac/*.py` |

---

*Generated from code review on 2026-08-29. Synapse v2 backend version 2.0.0.*