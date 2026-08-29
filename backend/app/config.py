"""Runtime configuration for the Synapse v2 backend.

Every knob is environment-driven so the same image can run key-free on a laptop
or against OpenAI + hosted Neo4j without code changes.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = BACKEND_ROOT / "var"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SYNAPSE_", env_file=".env", extra="ignore"
    )

    host: str = "127.0.0.1"
    port: int = 8001

    # Bearer token. When unset the API is fully open, which is only safe on loopback.
    auth_token: str | None = None

    data_dir: Path = DEFAULT_DATA_DIR

    graph_backend: Literal["falkordblite", "falkordb", "neo4j"] = "falkordblite"
    graph_database: str = "synapse"
    falkordb_host: str = "localhost"
    falkordb_port: int = 6379
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "password"

    llm_provider: Literal["stub", "openai", "ollama", "nvidia", "groq"] = "nvidia"
    openai_api_key: str | None = None
    openai_model: str = "deepseek-ai/deepseek-v4-flash-0731"
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = "deepseek-ai/deepseek-v4-flash-0731"

    # NVIDIA's catalog is OpenAI-compatible on /chat/completions but does not
    # implement the Responses API, so this provider uses OpenAIGenericClient.
    nvidia_api_key: str | None = None
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    # Must be fast and must honour response_format. Graphiti issues roughly six LLM
    # calls per chunk (extract nodes, extract edges, dedupe both, temporal), so
    # per-call latency multiplies hard. Reasoning models are the trap here: z-ai/glm-5.2
    # is available and accurate but spends ~250s per call on its thinking block, which
    # turns one document into hours. meta/llama-3.1-8b-instruct passes all structured
    # output checks, has 128k context, and handles json_object mode correctly.
    # Note: NVIDIA's endpoint rejects json_schema response_format; json_object mode is
    # used instead (schema is injected into the prompt). See kg_graphiti/service.py.
    nvidia_model: str = "nvidia/nemotron-3-ultra-550b-a55b"
    nvidia_embedding_model: str = "nvidia/nemotron-3-embed-1b"
    nvidia_embedding_dim: int = 2048

    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-120b"
    
    hf_token: str | None = None

    embedding_dim: int = 1024

    # R2: turn the unknown-response-model fallback into a hard failure. On in tests.
    strict_prompts: bool = False

    # Whether to allow Graphiti to perform open-domain extraction (invent dynamic nodes and edges)
    # instead of constraining it to the strict closed ontology in kg_graphiti/ontology.py.
    dynamic_extraction: bool = False

    # R7: conservative by default; every drop is logged with the rule that fired.
    prefilter_min_chars: int = 40
    prefilter_batch_target_chars: int = 1200

    default_tenant: str = "tenant-a"

    # GitHub Connector Settings
    github_app_id: str | None = None
    github_app_private_key: str | None = None
    github_webhook_secret: str | None = None
    github_external_agent_webhook_url: str | None = None
    github_default_poll_interval_hours: int = 6

    # Mail / Gmail Integration
    mail_google_client_id: str | None = None
    mail_google_client_secret: str | None = None
    mail_google_redirect_uri: str = "http://localhost:5173/mail/oauth/callback"
    mail_default_poll_interval_minutes: int = 15

    # Downstream Delivery & AgentSuite Sync
    agentsuite_sync_url: str | None = None
    delivery_endpoint: str | None = None

    max_upload_bytes: int = 50 * 1024 * 1024

    # --- RBAC ---
    jwt_secret: str = "change-me-in-production"
    access_token_ttl_minutes: int = 60 * 24 * 365 * 10  # 10 years
    refresh_token_ttl_days: int = 365 * 10  # 10 years
    rbac_transition_mode: bool = False
    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: str | None = None
    bootstrap_admin_display_name: str = "Bootstrap Administrator"
    bootstrap_admin_api_key: str | None = None

    # R3: a receipt still pending after this long is a reconciliation mismatch.
    reconcile_stuck_after_seconds: int = 120

    @property
    def sqlite_path(self) -> Path:
        return self.data_dir / "synapse.db"

    @property
    def graph_db_path(self) -> Path:
        return self.data_dir / "falkor" / "synapse.rdb"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def snapshot_dir(self) -> Path:
        return self.data_dir / "snapshots"

    def ensure_dirs(self) -> None:
        for path in (
            self.data_dir,
            self.graph_db_path.parent,
            self.upload_dir,
            self.snapshot_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
