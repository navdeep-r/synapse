# Synapse v2 — Enterprise Intelligence Platform

Synapse v2 is a **Graphiti-backed temporal knowledge graph platform** that ingests enterprise documents (uploads, GitHub repositories, Gmail threads, Slack, Notion), extracts a **closed-vocabulary ontology**, resolves entities, and maintains **bi-temporal fact validity**. It serves as the backend for a React-based console for exploring and querying the knowledge graph.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              FASTAPI BACKEND (port 8001)                     │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐  │
│  │  Auth    │ │  Chat    │ │  Graph   │ │ Sources  │ │   Connectors     │  │
│  │  Router  │ │  Router  │ │  Router  │ │  Router  │ │  GitHub / Mail   │  │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────────┬─────────┘  │
│       │            │            │            │                  │            │
│       └────────────┴────────────┴────────────┴──────────────────┘            │
│                                  │                                            │
│                    ┌─────────────┴─────────────┐                             │
│                    │   require_principal       │  ← JWT / API Key / Legacy   │
│                    │   (kg_acl)                │                             │
│                    └─────────────┬─────────────┘                             │
│                                  │                                            │
│                    ┌─────────────┴─────────────┐                             │
│                    │   AuthorizedGraph         │  ← RBAC scope filtering     │
│                    │   Repository              │                             │
│                    └─────────────┬─────────────┘                             │
└──────────────────────────────────┼────────────────────────────────────────────┘
                                   │
          ┌────────────────────────┼────────────────────────┐
          ▼                        ▼                        ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   GitHub        │    │   Mail (Gmail)  │    │   Direct Upload │
│   Connector     │    │   Connector     │    │   Pipeline      │
└────────┬────────┘    └────────┬────────┘    └────────┬────────┘
         │                      │                      │
         └──────────────────────┼──────────────────────┘
                                ▼
                 ┌─────────────────────────┐
                 │  kg_parsing             │  ← PDF, DOCX, HTML, code
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
                 │  kg_prefilter           │  ← Dedup, boilerplate, batching
                 │  prefilter_chunks()     │
                 │  build_episodes()       │
                 └───────────┬─────────────┘
                             │
                             ▼
                 ┌─────────────────────────┐
                 │  Graphiti Core          │  ← LLM extraction (closed ontology)
                 │  add_episode()          │
                 └───────────┬─────────────┘
                             │
                             ▼
                 ┌─────────────────────────┐
                 │  kg_observability       │  ← Receipts, telemetry, reconcile
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

**Graph Backend**: Configured via `SYNAPSE_GRAPH_BACKEND` — use `neo4j` for production/development (recommended). Falls back to `falkordblite` (embedded) when no `.env` is configured. Also supports external `falkordb`.

## Key Features

| Feature | Description |
|---------|-------------|
| **Multi-source Ingestion** | Uploads, GitHub repos (polling + webhooks), Gmail threads (History API) |
| **Closed-Vocabulary Ontology** | 27 entity types, 32 edge types, 32 type constraints — prevents hallucination |
| **Temporal Knowledge Graph** | Bi-temporal validity (valid_at, invalid_at) with edge invalidation |
| **Intent-Then-Commit (R3)** | Every Graphiti write has a durable SQLite receipt (PENDING → COMMITTED/FAILED) |
| **Pre-Filtering (R7)** | Exact-hash dedup + boilerplate drop before LLM calls; tracks episodes avoided |
| **Deterministic Extraction** | GitHub AST parsing (10+ languages) & Mail create nodes directly — no LLM |
| **Full RBAC** | Organizations, users, roles, teams, knowledge scopes, scope versions, drift detection |
| **Multi-Graph Tenancy** | Neo4j (`group_id` property filter); FalkorDB (separate graph per tenant via driver clone) |
| **Hybrid Chat Retrieval** | Graphiti semantic search + direct Cypher for Mail/GitHub/attention queries |
| **Observability** | Stage funnel counters, LLM telemetry (tokens, cost, latency), reconciliation |
| **Snapshots & Export** | Versioned gzipped JSON with ACL policy validation (strict/balanced/permissive) |

## Project Structure

```
synapse2/
├── backend/                    # FastAPI backend (Python 3.12)
│   ├── app/                    # Core application
│   │   ├── main.py            # FastAPI factory, lifespan, routers
│   │   ├── config.py          # Pydantic Settings (SYNAPSE_* env vars)
│   │   ├── db.py              # SQLite metadata store (50+ tables)
│   │   ├── pipeline.py        # 7-stage async ingestion pipeline
│   │   ├── state.py           # AppState singleton
│   │   ├── routers/           # 19 API routers
│   │   └── security*.py       # Auth middleware, rate limiting
│   ├── kg_graphiti/           # Graphiti integration layer
│   │   ├── service.py         # GraphService: client, writes, reads
│   │   ├── ontology.py        # Closed vocabulary (27 entities, 32 edges)
│   │   ├── drivers.py         # FalkorDB/Neo4j factory
│   │   └── embedder.py        # LocalSentenceTransformerEmbedder
│   ├── kg_acl/                # Complete RBAC system
│   │   ├── principal.py       # JWT/API Key resolution
│   │   ├── authorized_graph.py# Scope-filtered graph reads
│   │   ├── resolver.py        # Effective scope computation
│   │   ├── discovery.py       # Automated scope discovery
│   │   └── bootstrap.py       # Idempotent first-run setup
│   ├── github/                # GitHub connector
│   │   ├── connector.py       # Sync job (initial/incremental)
│   │   ├── ast_parsers.py     # Tree-sitter AST for 10+ languages
│   │   ├── llm.py             # Multi-provider LLM + disk cache
│   │   └── reconciliation.py  # Background polling
│   ├── mail/                  # Gmail connector
│   │   ├── connector.py       # MailSyncJob (History API)
│   │   └── client_gmail.py    # OAuth2 client
│   ├── kg_*/                  # 37 pipeline modules (parsing, chunking, prefilter, etc.)
│   ├── tests/                 # Pytest suite (rbac/, contract, e2e, etc.)
│   ├── scripts/               # Utility scripts (seed.py, check_nvidia.py)
│   ├── var/                   # Runtime data (DB, FalkorDB, uploads, snapshots)
│   ├── .env.example           # Configuration template
│   ├── pyproject.toml         # Dependencies (uv)
│   └── ARCHITECTURE.md        # Detailed architecture docs
├── console/                   # React frontend (Vite, TypeScript)
└── README.md                  # This file
```

## Quick Start

### Prerequisites
- **Python 3.12** (required by `pyproject.toml`)
- **uv** (fast Python package manager) — `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **Node.js 18+** (for console)
- **Git** (for GitHub connector)

### 1. Backend Setup

```bash
cd backend

# Copy environment template
cp .env.example .env

# Edit .env with your credentials (see Configuration section below)
# At minimum, set SYNAPSE_JWT_SECRET for production use

# Create virtual env & install deps (uses uv)
./run.sh
# OR manually:
# uv venv
# uv pip install -e ".[dev]"
# uvicorn app.main:app --host 127.0.0.1 --port 8001
```

The backend starts on **http://127.0.0.1:8001** with health endpoint at `/api/v1/health`.

### 2. Initialize Database & Bootstrap Admin (Required)

```bash
cd backend
# Creates DB tables, seeds ontology schema, default sources, and bootstrap admin
# Does NOT populate demo/mock graph data — use the console to upload/sync real data
uv run python scripts/seed.py --reset
```

### 3. Frontend Console Setup

```bash
cd console
npm install
npm run dev
```

Console runs on **http://localhost:5173** and proxies `/api` to backend.

The seed script initializes:
- SQLite schema (50+ tables)
- Closed ontology registry (27 entity types, 32 edge types)
- Default sources: `upload`, `github`, `mail`
- RBAC bootstrap: admin user, `org-supervisor` role, default scope

## Configuration

All configuration is via `SYNAPSE_*` environment variables. Copy `.env.example` to `.env` and edit.

### Core Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `SYNAPSE_HOST` | `127.0.0.1` | Bind address (use `0.0.0.0` for Docker/external) |
| `SYNAPSE_PORT` | `8001` | Port |
| `SYNAPSE_AUTH_TOKEN` | *unset* | Legacy shared secret (transition mode only) |
| `SYNAPSE_DATA_DIR` | `./var` | Data directory for SQLite, FalkorDB, uploads |
| `SYNAPSE_MAX_UPLOAD_BYTES` | `52428800` | 50MB max upload |

### Graph Backend

| Variable | Default | Options |
|----------|---------|---------|
| `SYNAPSE_GRAPH_BACKEND` | `falkordblite`* | `neo4j` (recommended), `falkordb`, `falkordblite` |
| `SYNAPSE_GRAPH_DATABASE` | `synapse` | Database/graph name |
| `SYNAPSE_FALKORDB_HOST` | `localhost` | FalkorDB host (for `falkordb`) |
| `SYNAPSE_FALKORDB_PORT` | `6379` | FalkorDB port |
| `SYNAPSE_NEO4J_URI` | `bolt://localhost:7687` | Neo4j bolt URI |
| `SYNAPSE_NEO4J_USER` | `neo4j` | Neo4j username |
| `SYNAPSE_NEO4J_PASSWORD` | `password` | Neo4j password |

### LLM Provider

| Variable | Default | Options |
|----------|---------|---------|
| `SYNAPSE_LLM_PROVIDER` | `nvidia` | `stub`, `openai`, `ollama`, `nvidia`, `groq` |
| `SYNAPSE_OPENAI_API_KEY` | *unset* | OpenAI API key |
| `SYNAPSE_OPENAI_MODEL` | `deepseek-ai/deepseek-v4-flash-0731` | OpenAI model |
| `SYNAPSE_OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Ollama endpoint |
| `SYNAPSE_OLLAMA_MODEL` | `deepseek-ai/deepseek-v4-flash-0731` | Ollama model |
| `SYNAPSE_NVIDIA_API_KEY` | *unset* | **Required for NVIDIA** |
| `SYNAPSE_NVIDIA_BASE_URL` | `https://integrate.api.nvidia.com/v1` | NVIDIA endpoint |
| `SYNAPSE_NVIDIA_MODEL` | `nvidia/nemotron-3-ultra-550b-a55b` | NVIDIA model |
| `SYNAPSE_NVIDIA_EMBEDDING_MODEL` | `nvidia/nemotron-3-embed-1b` | NVIDIA embedder |
| `SYNAPSE_NVIDIA_EMBEDDING_DIM` | `2048` | Embedding dimension |
| `SYNAPSE_GROQ_API_KEY` | *unset* | Groq API key (fallback) |
| `SYNAPSE_GROQ_MODEL` | `openai/gpt-oss-120b` | Groq model |
| `SYNAPSE_HF_TOKEN` | *unset* | HuggingFace token (for local embedder) |
| `SYNAPSE_EMBEDDING_DIM` | `1024` | Embedding dimension (non-NVIDIA) |

### GitHub Connector

| Variable | Description |
|----------|-------------|
| `SYNAPSE_GITHUB_APP_ID` | GitHub App ID (from GitHub Developer Settings) |
| `SYNAPSE_GITHUB_APP_PRIVATE_KEY` | GitHub App private key (PEM format, multi-line) |
| `SYNAPSE_GITHUB_WEBHOOK_SECRET` | Webhook HMAC secret |
| `SYNAPSE_GITHUB_EXTERNAL_AGENT_WEBHOOK_URL` | Optional external agent webhook |
| `SYNAPSE_GITHUB_DEFAULT_POLL_INTERVAL_HOURS` | `6` |

### Mail / Gmail Connector

| Variable | Description |
|----------|-------------|
| `SYNAPSE_MAIL_GOOGLE_CLIENT_ID` | Google OAuth Client ID |
| `SYNAPSE_MAIL_GOOGLE_CLIENT_SECRET` | Google OAuth Client Secret |
| `SYNAPSE_MAIL_GOOGLE_REDIRECT_URI` | `http://localhost:5173/mail/oauth/callback` |
| `SYNAPSE_MAIL_DEFAULT_POLL_INTERVAL_MINUTES` | `15` |

### RBAC & Authentication

| Variable | Default | Description |
|----------|---------|-------------|
| `SYNAPSE_JWT_SECRET` | `change-me-in-production` | **REQUIRED: Strong random secret** |
| `SYNAPSE_ACCESS_TOKEN_TTL_MINUTES` | `5256000` (10yr) | Access token TTL |
| `SYNAPSE_REFRESH_TOKEN_TTL_DAYS` | `3650` (10yr) | Refresh token TTL |
| `SYNAPSE_RBAC_TRANSITION_MODE` | `false` | Legacy compat (no auth required) |
| `SYNAPSE_BOOTSTRAP_ADMIN_EMAIL` | *unset* | Bootstrap admin email |
| `SYNAPSE_BOOTSTRAP_ADMIN_PASSWORD` | *unset* | Bootstrap admin password |
| `SYNAPSE_BOOTSTRAP_ADMIN_DISPLAY_NAME` | `Bootstrap Administrator` | Admin display name |
| `SYNAPSE_BOOTSTRAP_ADMIN_API_KEY` | *unset* | Optional API key for bootstrap admin |

### Observability & Reconciliation

| Variable | Default | Description |
|----------|---------|-------------|
| `SYNAPSE_STRICT_PROMPTS` | `false` | Raise on unrecognized prompts |
| `SYNAPSE_DYNAMIC_EXTRACTION` | `false` | Allow open-domain extraction |
| `SYNAPSE_PREFILTER_MIN_CHARS` | `40` | Min chars to keep chunk |
| `SYNAPSE_PREFILTER_BATCH_TARGET_CHARS` | `1200` | Episode target size |
| `SYNAPSE_RECONCILE_STUCK_AFTER_SECONDS` | `120` | Receipt stuck threshold |

### Downstream & Agent Integration

| Variable | Default | Description |
|----------|---------|-------------|
| `SYNAPSE_DEFAULT_TENANT` | `tenant-a` | Default tenant identifier |
| `SYNAPSE_AGENTSUITE_SYNC_URL` | *unset* | AgentSuite sync endpoint URL |
| `SYNAPSE_DELIVERY_ENDPOINT` | *unset* | Downstream delivery endpoint URL |

---

## Credential Seeding Script

Use this script to generate a secure `.env` file with all required credentials. Run it once during initial setup.

### `scripts/setup_credentials.py`

```python
#!/usr/bin/env python3
"""
Synapse v2 Credential Setup Script

Generates a secure .env file with strong secrets and guides you through
configuring external service credentials (GitHub, Gmail, LLM providers).

Usage:
    cd backend
    python scripts/setup_credentials.py
    # Then edit the generated .env with your service credentials
"""

import os
import secrets
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = BACKEND_ROOT / ".env"
ENV_EXAMPLE = BACKEND_ROOT / ".env.example"

# Strong secret generators
def gen_jwt_secret() -> str:
    return secrets.token_urlsafe(48)

def gen_api_key() -> str:
    return f"sk-{secrets.token_urlsafe(32)}"

def gen_password(length: int = 16) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))

def read_env_example() -> dict:
    """Parse .env.example for structure and comments."""
    if not ENV_EXAMPLE.exists():
        return {}
    config = {}
    current_section = "Core"
    with ENV_EXAMPLE.open() as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                if line.startswith("#"):
                    current_section = line[1:].strip()
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                config[key.strip()] = {"value": val.strip(), "section": current_section}
    return config

def write_env(config: dict) -> None:
    """Write .env file with sections and comments."""
    sections = {}
    for key, info in config.items():
        sections.setdefault(info["section"], []).append((key, info["value"]))

    lines = [
        "# Synapse v2 Configuration",
        "# Generated by setup_credentials.py",
        "# Edit the values below with your actual credentials",
        "",
    ]

    for section, items in sections.items():
        lines.append(f"# === {section} ===")
        for key, val in items:
            lines.append(f"{key}={val}")
        lines.append("")

    ENV_PATH.write_text("\n".join(lines))
    print(f"✅ Written to {ENV_PATH}")

def main():
    print("🔐 Synapse v2 Credential Setup")
    print("=" * 50)

    if ENV_PATH.exists():
        resp = input(f"⚠️  {ENV_PATH} already exists. Overwrite? [y/N]: ").strip().lower()
        if resp != "y":
            print("Aborted.")
            return

    # Base configuration with generated secrets
    config = {
        # Core
        "SYNAPSE_HOST": {"value": "127.0.0.1", "section": "Core"},
        "SYNAPSE_PORT": {"value": "8001", "section": "Core"},
        "SYNAPSE_AUTH_TOKEN": {"value": "", "section": "Core"},
        "SYNAPSE_DATA_DIR": {"value": "./var", "section": "Core"},
        "SYNAPSE_MAX_UPLOAD_BYTES": {"value": "52428800", "section": "Core"},

        # Graph
        "SYNAPSE_GRAPH_BACKEND": {"value": "falkordblite", "section": "Graph Backend"},
        "SYNAPSE_GRAPH_DATABASE": {"value": "synapse", "section": "Graph Backend"},
        "SYNAPSE_FALKORDB_HOST": {"value": "localhost", "section": "Graph Backend"},
        "SYNAPSE_FALKORDB_PORT": {"value": "6379", "section": "Graph Backend"},
        "SYNAPSE_NEO4J_URI": {"value": "bolt://localhost:7687", "section": "Graph Backend"},
        "SYNAPSE_NEO4J_USER": {"value": "neo4j", "section": "Graph Backend"},
        "SYNAPSE_NEO4J_PASSWORD": {"value": "password", "section": "Graph Backend"},

        # LLM Provider (choose ONE primary)
        "SYNAPSE_LLM_PROVIDER": {"value": "nvidia", "section": "LLM Provider"},
        "SYNAPSE_OPENAI_API_KEY": {"value": "", "section": "LLM Provider"},
        "SYNAPSE_OPENAI_MODEL": {"value": "deepseek-ai/deepseek-v4-flash-0731", "section": "LLM Provider"},
        "SYNAPSE_OLLAMA_BASE_URL": {"value": "http://localhost:11434/v1", "section": "LLM Provider"},
        "SYNAPSE_OLLAMA_MODEL": {"value": "deepseek-ai/deepseek-v4-flash-0731", "section": "LLM Provider"},
        "SYNAPSE_NVIDIA_API_KEY": {"value": "", "section": "LLM Provider"},
        "SYNAPSE_NVIDIA_BASE_URL": {"value": "https://integrate.api.nvidia.com/v1", "section": "LLM Provider"},
        "SYNAPSE_NVIDIA_MODEL": {"value": "nvidia/nemotron-3-ultra-550b-a55b", "section": "LLM Provider"},
        "SYNAPSE_NVIDIA_EMBEDDING_MODEL": {"value": "nvidia/nemotron-3-embed-1b", "section": "LLM Provider"},
        "SYNAPSE_NVIDIA_EMBEDDING_DIM": {"value": "2048", "section": "LLM Provider"},
        "SYNAPSE_GROQ_API_KEY": {"value": "", "section": "LLM Provider"},
        "SYNAPSE_GROQ_MODEL": {"value": "openai/gpt-oss-120b", "section": "LLM Provider"},
        "SYNAPSE_HF_TOKEN": {"value": "", "section": "LLM Provider"},
        "SYNAPSE_EMBEDDING_DIM": {"value": "1024", "section": "LLM Provider"},

        # GitHub
        "SYNAPSE_GITHUB_APP_ID": {"value": "", "section": "GitHub Connector"},
        "SYNAPSE_GITHUB_APP_PRIVATE_KEY": {"value": "", "section": "GitHub Connector"},
        "SYNAPSE_GITHUB_WEBHOOK_SECRET": {"value": gen_jwt_secret(), "section": "GitHub Connector"},
        "SYNAPSE_GITHUB_EXTERNAL_AGENT_WEBHOOK_URL": {"value": "", "section": "GitHub Connector"},
        "SYNAPSE_GITHUB_DEFAULT_POLL_INTERVAL_HOURS": {"value": "6", "section": "GitHub Connector"},

        # Mail/Gmail
        "SYNAPSE_MAIL_GOOGLE_CLIENT_ID": {"value": "", "section": "Mail/Gmail Connector"},
        "SYNAPSE_MAIL_GOOGLE_CLIENT_SECRET": {"value": "", "section": "Mail/Gmail Connector"},
        "SYNAPSE_MAIL_GOOGLE_REDIRECT_URI": {"value": "http://localhost:5173/mail/oauth/callback", "section": "Mail/Gmail Connector"},
        "SYNAPSE_MAIL_DEFAULT_POLL_INTERVAL_MINUTES": {"value": "15", "section": "Mail/Gmail Connector"},

        # RBAC & Auth
        "SYNAPSE_JWT_SECRET": {"value": gen_jwt_secret(), "section": "RBAC & Authentication"},
        "SYNAPSE_ACCESS_TOKEN_TTL_MINUTES": {"value": "5256000", "section": "RBAC & Authentication"},
        "SYNAPSE_REFRESH_TOKEN_TTL_DAYS": {"value": "3650", "section": "RBAC & Authentication"},
        "SYNAPSE_RBAC_TRANSITION_MODE": {"value": "false", "section": "RBAC & Authentication"},
        "SYNAPSE_BOOTSTRAP_ADMIN_EMAIL": {"value": "", "section": "RBAC & Authentication"},
        "SYNAPSE_BOOTSTRAP_ADMIN_PASSWORD": {"value": gen_password(), "section": "RBAC & Authentication"},
        "SYNAPSE_BOOTSTRAP_ADMIN_DISPLAY_NAME": {"value": "Bootstrap Administrator", "section": "RBAC & Authentication"},
        "SYNAPSE_BOOTSTRAP_ADMIN_API_KEY": {"value": gen_api_key(), "section": "RBAC & Authentication"},

        # Observability
        "SYNAPSE_STRICT_PROMPTS": {"value": "false", "section": "Observability & Pipeline"},
        "SYNAPSE_DYNAMIC_EXTRACTION": {"value": "false", "section": "Observability & Pipeline"},
        "SYNAPSE_PREFILTER_MIN_CHARS": {"value": "40", "section": "Observability & Pipeline"},
        "SYNAPSE_PREFILTER_BATCH_TARGET_CHARS": {"value": "1200", "section": "Observability & Pipeline"},
        "SYNAPSE_RECONCILE_STUCK_AFTER_SECONDS": {"value": "120", "section": "Observability & Pipeline"},
    }

    write_env(config)

    print("\n📋 Next Steps:")
    print("1. Edit .env and fill in YOUR credentials:")
    print("   - LLM Provider: At least one of OPENAI/NVIDIA/GROQ/OLLAMA API keys")
    print("   - GitHub: App ID + Private Key (from GitHub Developer Settings)")
    print("   - Gmail: Google OAuth Client ID + Secret (from Google Cloud Console)")
    print("   - Bootstrap Admin: Email + Password for first admin user")
    print()
    print("2. For NVIDIA NIM (recommended):")
    print("   - Get API key from https://build.nvidia.com")
    print("   - Run: python scripts/check_nvidia.py  # validates credentials")
    print()
    print("3. Start the backend:")
    print("   ./run.sh")
    print()
    print("4. Start the console (in another terminal):")
    print("   cd ../console && npm install && npm run dev")
    print()
    print("5. Access the console at http://localhost:5173")
    print("   Login with bootstrap admin credentials from .env")

if __name__ == "__main__":
    main()
```

### Make it executable and run:

```bash
cd backend
chmod +x scripts/setup_credentials.py
python scripts/setup_credentials.py
```

This generates a `.env` with:
- 🔐 **Strong JWT secret** (48-char URL-safe)
- 🔐 **Secure bootstrap admin password** (16-char)
- 🔐 **API key for bootstrap admin** (`sk-...`)
- 🔐 **GitHub webhook secret** (auto-generated)
- 📝 **Placeholders** for all your service credentials (GitHub, Gmail, LLM)

---

## Service Credential Guides

### GitHub App Setup
1. Go to **GitHub Settings → Developer settings → GitHub Apps → New GitHub App**
2. **Homepage URL**: `http://localhost:5173` (or your domain)
3. **Webhook URL**: `https://your-domain/api/v1/github/webhook`
4. **Webhook Secret**: Copy from `.env` (`SYNAPSE_GITHUB_WEBHOOK_SECRET`)
5. **Permissions**:
   - Repository: Contents (R), Metadata (R), Pull Requests (R), Issues (R), Commits (R)
   - Organization: Members (R)
6. **Subscribe to events**: Push, Pull Request, Issues, Repository
7. **Install** the app on your target repositories
8. Copy **App ID** and generate **Private Key** (`.pem`) → paste into `.env`

### Google OAuth (Gmail)
1. Go to **Google Cloud Console → APIs & Services → Credentials → Create Credentials → OAuth Client ID**
2. **Application type**: Web application
3. **Authorized redirect URIs**: `http://localhost:5173/mail/oauth/callback`
4. Copy **Client ID** and **Client Secret** → paste into `.env`
5. Enable **Gmail API** in APIs & Services

### NVIDIA NIM (Recommended LLM)
1. Sign up at **https://build.nvidia.com**
2. Generate API key
3. Run validation: `python scripts/check_nvidia.py`
4. Paste key into `SYNAPSE_NVIDIA_API_KEY`

---

## Production Deployment Checklist

- [ ] Set `SYNAPSE_JWT_SECRET` to strong random value (generated by setup script)
- [ ] Set `SYNAPSE_RBAC_TRANSITION_MODE=false`
- [ ] Configure `SYNAPSE_BOOTSTRAP_ADMIN_EMAIL` + `PASSWORD`
- [ ] Use external **FalkorDB** or **Neo4j** (not `falkordblite`)
- [ ] Set `SYNAPSE_LLM_PROVIDER` + valid API keys
- [ ] Configure **GitHub App** credentials
- [ ] Configure **Google OAuth** for Gmail
- [ ] Enable **HTTPS** (reverse proxy: nginx/Traefik/Caddy)
- [ ] Set `SYNAPSE_HOST=0.0.0.0` for container deployment
- [ ] Set up **log aggregation** (ELK/Loki/Grafana)
- [ ] Configure **backup** for SQLite (`var/synapse.db`) + graph DB
- [ ] Set **resource limits** (memory/CPU) for containers
- [ ] Configure **monitoring** (health endpoint `/api/v1/health`)

### Docker Example

```dockerfile
# Dockerfile.backend
FROM python:3.12-slim
WORKDIR /app
COPY backend/ .
RUN pip install uv && uv pip install --system -e .
EXPOSE 8001
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
```

```bash
# Build & run
docker build -f backend/Dockerfile.backend -t synapse-backend .
docker run -d \
  --name synapse-backend \
  -p 8001:8001 \
  --env-file backend/.env \
  -v synapse-data:/app/var \
  synapse-backend
```

---

## API Reference

### Health Check
```
GET /api/v1/health
```

### Authentication (unguarded)
```
POST   /api/v1/auth/login
POST   /api/v1/auth/refresh
POST   /api/v1/auth/logout
GET    /api/v1/auth/me
GET    /api/v1/auth/sessions
DELETE /api/v1/auth/sessions/{id}
DELETE /api/v1/auth/sessions
POST   /api/v1/auth/forgot-password
POST   /api/v1/auth/reset-password
POST   /api/v1/auth/accept-invitation
POST   /api/v1/auth/complete-signup
```

### Knowledge Graph Chat
```
POST /api/v1/chat
```
Body: `{ "messages": [...], "workspace": "synapse", "num_facts": 20 }`

### Graph Explorer
```
GET  /api/v1/graph/topology
GET  /api/v1/graph/entities/search?q=...
GET  /api/v1/graph/entities/{uuid}
GET  /api/v1/graph/history/{uuid}
```

### Sources & Ingestion
```
GET    /api/v1/sources
POST   /api/v1/sources
POST   /api/v1/sources/upload
GET    /api/v1/sources/{source_id}/documents
GET    /api/v1/sources/{source_id}/chunks
```

### GitHub Connector
```
GET  /api/v1/github/repos
POST /api/v1/github/repos
POST /api/v1/github/repos/{id}/sync
GET  /api/v1/github/repos/{id}/tree
```

### Mail Connector
```
GET  /api/v1/mail/accounts
POST /api/v1/mail/accounts
POST /api/v1/mail/accounts/{id}/sync
GET  /api/v1/mail/oauth/callback
```

### RBAC (Supervisor required)
```
GET  /api/v1/users
POST /api/v1/users/invite
GET  /api/v1/roles
POST /api/v1/roles
GET  /api/v1/teams
POST /api/v1/teams
GET  /api/v1/scopes
POST /api/v1/scopes
GET  /api/v1/assignments
POST /api/v1/assignments
```

### Observability & Export
```
GET /api/v1/observability/pipeline
GET /api/v1/observability/telemetry
GET /api/v1/observability/receipts
GET /api/v1/reports/consistency
POST /api/v1/export/snapshot
GET /api/v1/export/snapshots
```

---

## Testing

```bash
cd backend

# Run all tests
.venv/bin/python -m pytest tests/ -q

# Run specific test files
.venv/bin/python -m pytest tests/test_github_connector.py -v
.venv/bin/python -m pytest tests/rbac/ -v

# Run with coverage
.venv/bin/python -m pytest tests/ --cov=app --cov=kg_* --cov=github --cov=mail
```

**Pytest Config** (`pyproject.toml`):
- Session-scoped asyncio (embedded FalkorDB bound to event loop)
- Tests in `tests/` + `tests/rbac/`

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `graph_ready: false` on health check | Wait for background graph boot; check logs for FalkorDB init errors |
| `Authentication required` | Ensure `.env` has `SYNAPSE_JWT_SECRET`; login via `/auth/login` |
| GitHub sync fails | Verify App ID, Private Key, and installation on target repos |
| Gmail sync fails | Verify OAuth credentials; re-authenticate via console |
| LLM calls timeout | Check API key validity; try `stub` provider for testing |
| Port 8001 in use | Change `SYNAPSE_PORT` or kill existing process |
| `ModuleNotFoundError` | Run `uv pip install -e ".[dev]"` in backend |

---

## Documentation

- **Architecture Deep Dive**: `backend/ARCHITECTURE.md` — 2000+ lines covering modules, flows, patterns, issues
- **Inline Code Docs**: Docstrings throughout (run `pdoc` to generate HTML)
- **OpenAPI Spec**: `backend/openapi.json` (generated at startup)

---

## License

Proprietary — Internal Use Only.

---

## Support

For issues, check:
1. Backend logs (`./run.sh` output)
2. Health endpoint (`GET /api/v1/health`)
3. Observability endpoints (`/api/v1/observability/*`)
4. Reconciliation report (`/api/v1/reports/consistency`)