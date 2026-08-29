"""The Graphiti layer: client assembly, episode writes and graph reads."""

from __future__ import annotations

import asyncio
import contextvars
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from graphiti_core import Graphiti
from graphiti_core.nodes import EpisodeType

import graphiti_core.utils.bulk_utils
import json

# Monkey patch Graphiti's bulk inserter to safely serialize nested dictionaries
# into JSON strings before they hit Neo4j (which throws Map{} type errors).
if not hasattr(graphiti_core.utils.bulk_utils, "_original_bulk_tx"):
    graphiti_core.utils.bulk_utils._original_bulk_tx = graphiti_core.utils.bulk_utils.add_nodes_and_edges_bulk_tx

    async def patched_bulk_tx(tx, episodic_nodes, episodic_edges, entity_nodes, entity_edges, embedder, driver):
        for node in entity_nodes:
            if node.attributes:
                for k, v in list(node.attributes.items()):
                    if isinstance(v, (dict, list)):
                        node.attributes[k] = json.dumps(v)
        for edge in entity_edges:
            if edge.attributes:
                for k, v in list(edge.attributes.items()):
                    if isinstance(v, (dict, list)):
                        edge.attributes[k] = json.dumps(v)
        return await graphiti_core.utils.bulk_utils._original_bulk_tx(tx, episodic_nodes, episodic_edges, entity_nodes, entity_edges, embedder, driver)

    graphiti_core.utils.bulk_utils.add_nodes_and_edges_bulk_tx = patched_bulk_tx
    # Crucially, update the reference inside the already-imported graphiti module
    import graphiti_core.graphiti
    graphiti_core.graphiti.add_nodes_and_edges_bulk_tx = patched_bulk_tx

from app.config import Settings
from kg_contracts import EpisodePayload
from kg_graphiti import falkor_sanitize, llm_metrics
from kg_graphiti.drivers import build_driver
from kg_graphiti.embedder import DEFAULT_DIM, DeterministicEmbedder
from kg_graphiti.llm_stub import StubLLMClient
from kg_graphiti.ontology import EDGE_TYPE_MAP, EDGE_TYPES, ENTITY_TYPES
from kg_graphiti.reranker import LexicalCrossEncoder

from graphiti_core.embedder.client import EmbedderClient
from graphiti_core.llm_client.client import LLMClient

class FallbackEmbedderClient(EmbedderClient):
    def __init__(self, primary: EmbedderClient, secondary: EmbedderClient):
        self.primary = primary
        self.secondary = secondary

    async def create(self, *args, **kwargs) -> list[float]:
        try:
            return await self.primary.create(*args, **kwargs)
        except Exception as e:
            logger.warning("Primary EmbedderClient failed with %s, falling back...", e)
            return await self.secondary.create(*args, **kwargs)

    async def create_batch(self, *args, **kwargs) -> list[list[float]]:
        try:
            return await self.primary.create_batch(*args, **kwargs)
        except Exception as e:
            logger.warning("Primary EmbedderClient failed with %s, falling back...", e)
            return await self.secondary.create_batch(*args, **kwargs)

class FallbackLLMClient(LLMClient):
    def __init__(self, primary: LLMClient, secondary: LLMClient):
        super().__init__(None)
        self.primary = primary
        self.secondary = secondary

    async def generate_response(self, *args, **kwargs) -> dict[str, Any]:
        try:
            return await self.primary.generate_response(*args, **kwargs)
        except Exception as e:
            logger.warning("Primary LLMClient failed with %s, falling back to secondary...", e)
            return await self.secondary.generate_response(*args, **kwargs)

    async def _generate_response(self, *args, **kwargs):
        pass

class LocalSentenceTransformerEmbedder(EmbedderClient):
    def __init__(self, model_name: str = "google/embeddinggemma-300m", dim: int = 1024, token: str | None = None):
        import os
        from sentence_transformers import SentenceTransformer
        
        # Set token in env as fallback for some downstream HuggingFace hub functions
        if token:
            os.environ["HF_TOKEN"] = token
            
        self.model = SentenceTransformer(model_name, token=token)
        self.dim = dim

    async def create(self, input_data: Any) -> list[float]:
        if isinstance(input_data, str):
            embedding = self.model.encode(input_data)
        elif isinstance(input_data, list) and len(input_data) > 0 and isinstance(input_data[0], str):
            embedding = self.model.encode(input_data[0])
        else:
            embedding = self.model.encode(str(input_data))
        return embedding.tolist()[:self.dim]

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        embeddings = self.model.encode(input_data_list)
        return [emb.tolist()[:self.dim] for emb in embeddings]

logger = logging.getLogger("synapse.graph")

current_episode_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_episode_id", default=None)


@dataclass
class EpisodeWriteResult:
    episode_uuid: str | None
    nodes_created: int
    edges_created: int
    llm_calls: int
    latency_ms: int
    invalidated_edges: int = 0
    error: str | None = None


@dataclass
class GraphService:
    """Owns the Graphiti client and the embedded graph server."""

    settings: Settings
    db: Database | None = None
    counters: Counter = field(default_factory=Counter)
    graphiti: Graphiti | None = None
    _server: Any = None
    _driver: Any = None
    _ready: bool = False
    _init_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _init_error: str | None = None

    @property
    def is_ready(self) -> bool:
        return self._ready

    @property
    def init_error(self) -> str | None:
        return self._init_error

    def _build_llm_client(self) -> Any:
        client = self._new_llm_client()
        if self.settings.llm_provider == "stub":
            return client  # already counts its own calls; wrapping would double-count
        return llm_metrics.instrument(client, self.counters, self.db)

    def _new_llm_client(self) -> Any:
        provider = self.settings.llm_provider
        if provider == "stub":
            return StubLLMClient(strict=self.settings.strict_prompts, counters=self.counters)

        from graphiti_core.llm_client.config import LLMConfig
        from graphiti_core.llm_client.openai_client import OpenAIClient

        if provider == "openai":
            if not self.settings.openai_api_key:
                raise RuntimeError("SYNAPSE_LLM_PROVIDER=openai requires SYNAPSE_OPENAI_API_KEY")
            config = LLMConfig(
                api_key=self.settings.openai_api_key, model=self.settings.openai_model
            )
            return OpenAIClient(config=config)

        if provider == "groq":
            if not self.settings.groq_api_key:
                raise RuntimeError("SYNAPSE_LLM_PROVIDER=groq requires SYNAPSE_GROQ_API_KEY")
            config = LLMConfig(
                api_key=self.settings.groq_api_key,
                model=self.settings.groq_model,
                base_url="https://api.groq.com/openai/v1",
                max_tokens=2048,
            )
            return OpenAIClient(config=config)

        if provider == "nvidia":
            if not self.settings.nvidia_api_key:
                raise RuntimeError("SYNAPSE_LLM_PROVIDER=nvidia requires SYNAPSE_NVIDIA_API_KEY")
            from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
            
            nvidia_config = LLMConfig(
                api_key=self.settings.nvidia_api_key,
                model=self.settings.nvidia_model,
                base_url=self.settings.nvidia_base_url,
            )
            primary_client = OpenAIGenericClient(
                config=nvidia_config,
                structured_output_mode="json_schema",
                max_tokens=2048,
            )
            
            # Setup Groq fallback
            if self.settings.groq_api_key:
                groq_config = LLMConfig(
                    api_key=self.settings.groq_api_key,
                    model=self.settings.groq_model,
                    base_url="https://api.groq.com/openai/v1"
                )
                secondary_client = OpenAIClient(config=groq_config)
                return FallbackLLMClient(primary_client, secondary_client)
            else:
                return primary_client

        # Ollama exposes an OpenAI-compatible endpoint; the key is ignored but required.
        config = LLMConfig(
            api_key="ollama",
            model=self.settings.ollama_model,
            base_url=self.settings.ollama_base_url,
        )
        return OpenAIClient(config=config)

    def _build_embedder(self) -> Any:
        return LocalSentenceTransformerEmbedder(
            dim=self.settings.embedding_dim or 1024,
            token=self.settings.hf_token
        )

    async def start(self) -> None:
        """Bring up the graph. Safe to call repeatedly."""
        async with self._init_lock:
            if self._ready:
                return
            try:
                driver, server = build_driver(
                    self.settings.graph_backend,
                    database=self.settings.graph_database,
                    db_path=self.settings.graph_db_path,
                    falkordb_host=self.settings.falkordb_host,
                    falkordb_port=self.settings.falkordb_port,
                    neo4j_uri=self.settings.neo4j_uri,
                    neo4j_user=self.settings.neo4j_user,
                    neo4j_password=self.settings.neo4j_password,
                )
                self._driver, self._server = driver, server

                falkor_sanitize.install()

                self.graphiti = Graphiti(
                    graph_driver=driver,
                    llm_client=self._build_llm_client(),
                    embedder=self._build_embedder(),
                    cross_encoder=LexicalCrossEncoder(),
                )
                await self.graphiti.build_indices_and_constraints()
                self._ready = True
                self._init_error = None
                logger.info("Graph ready (backend=%s)", self.settings.graph_backend)
            except Exception as exc:
                self._init_error = str(exc)
                logger.exception("Graph initialization failed")
                raise

    async def persist(self) -> bool:
        """Flush the embedded graph to disk.

        redislite tries to save on shutdown, but on the async client that call
        returns a coroutine nobody awaits, so the snapshot is silently skipped
        and the whole graph is lost on restart while SQLite keeps its receipts.
        Issuing an awaited SAVE ourselves is the only way to actually persist.
        """
        client = getattr(self._server, "client", None)
        if client is None or not hasattr(client, "save"):
            return False
        try:
            await client.save()
            return True
        except Exception:
            logger.warning("Graph snapshot failed; data may not survive restart", exc_info=True)
            return False

    async def close(self) -> None:
        await self.persist()
        if self.graphiti is not None:
            try:
                await self.graphiti.close()
            except Exception:
                logger.debug("Graphiti close raised", exc_info=True)
        self._ready = False

    async def ensure_ready(self) -> None:
        if not self._ready:
            await self.start()

    # --- Writes ------------------------------------------------------------

    async def add_episode(self, payload: EpisodePayload) -> EpisodeWriteResult:
        """Run one episode through Graphiti, recording cost and yield metrics."""
        await self.ensure_ready()
        assert self.graphiti is not None

        token = current_episode_id.set(payload.episode_id)

        before = self.counters.get("llm_calls", 0)
        started = time.perf_counter()

        kwargs = {
            "name": payload.name,
            "episode_body": payload.body,
            "source_description": payload.source_description,
            "reference_time": payload.reference_time,
            "source": EpisodeType.json if payload.is_json else EpisodeType.text,
            "group_id": "synapse",
        }

        if not self.settings.dynamic_extraction:
            kwargs.update({
                "entity_types": ENTITY_TYPES,
                "edge_types": EDGE_TYPES,
                "edge_type_map": EDGE_TYPE_MAP,
            })

        try:
            results = await self.graphiti.add_episode(**kwargs)
        except Exception as exc:
            latency = int((time.perf_counter() - started) * 1000)
            logger.exception("add_episode failed for %s", payload.name)
            current_episode_id.reset(token)
            return EpisodeWriteResult(None, 0, 0, 0, latency, error=str(exc))

        latency = int((time.perf_counter() - started) * 1000)
        edges = list(getattr(results, "edges", []) or [])
        invalidated = sum(1 for edge in edges if getattr(edge, "invalid_at", None) is not None)

        node_uuids = [n.uuid for n in getattr(results, "nodes", []) or []]
        edge_uuids = [e.uuid for e in edges]
        
        if node_uuids or edge_uuids:
            try:
                sid = getattr(payload, "source_id", "unknown")
                st = getattr(payload, "source_type", "unknown")
                if node_uuids:
                    await self.query(
                        "MATCH (n:Entity) WHERE n.uuid IN $uuids SET n.source_id = $sid, n.source_type = $st",
                        group_id=payload.group_id, read_only=False, uuids=node_uuids, sid=sid, st=st
                    )
                if edge_uuids:
                    await self.query(
                        "MATCH ()-[e]->() WHERE e.uuid IN $uuids SET e.source_id = $sid, e.source_type = $st",
                        group_id=payload.group_id, read_only=False, uuids=edge_uuids, sid=sid, st=st
                    )
                # Ensure deterministic nodes from this source get the episode provenance
                await self.query(
                    "MATCH (n:Entity) WHERE n.source_id = $sid AND NOT $ep IN coalesce(n.episodes, []) SET n.episodes = coalesce(n.episodes, []) + [$ep]",
                    group_id=payload.group_id, read_only=False, sid=sid, ep=payload.episode_id
                )
            except Exception:
                logger.warning("Failed to tag source attribution on extracted entities", exc_info=True)

        self.counters["episodes_written"] += 1
        self.counters["edges_invalidated"] += invalidated

        current_episode_id.reset(token)

        return EpisodeWriteResult(
            episode_uuid=getattr(getattr(results, "episode", None), "uuid", None),
            nodes_created=len(getattr(results, "nodes", []) or []),
            edges_created=len(edges),
            llm_calls=self.counters.get("llm_calls", 0) - before,
            latency_ms=latency,
            invalidated_edges=invalidated,
        )

    async def build_communities(self) -> int:
        await self.ensure_ready()
        assert self.graphiti is not None
        
        # Swap client to flash version for discovery
        original_client = self.graphiti.llm_client
        
        try:
            # Create a flash version of the current config
            provider = self.settings.llm_provider
            flash_client = None
            
            if provider in ("openai", "nvidia", "groq"):
                from graphiti_core.llm_client.config import LLMConfig
                
                # Determine flash model name based on current
                current_model = ""
                if provider == "openai":
                    current_model = self.settings.openai_model
                    api_key = self.settings.openai_api_key
                    base_url = None
                elif provider == "groq":
                    current_model = self.settings.groq_model
                    api_key = self.settings.groq_api_key
                    base_url = "https://api.groq.com/openai/v1"
                else:
                    current_model = self.settings.nvidia_model
                    api_key = self.settings.nvidia_api_key
                    base_url = self.settings.nvidia_base_url
                    
                flash_model = current_model
                if "gemini" in current_model.lower():
                    flash_model = "gemini-1.5-flash"
                elif "gpt-4o" in current_model.lower() and "mini" not in current_model.lower():
                    flash_model = "gpt-4o-mini"
                    
                config = LLMConfig(
                    api_key=api_key or "mock", 
                    model=flash_model,
                    base_url=base_url
                )
                
                if provider == "openai":
                    from graphiti_core.llm_client.openai_client import OpenAIClient
                    flash_client = OpenAIClient(config=config)
                else:
                    from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
                    flash_client = OpenAIGenericClient(config=config)
            
            if flash_client:
                instrumented = llm_metrics.instrument(flash_client, self.counters, self.db)
                self.graphiti.llm_client = instrumented
                
            communities, _ = await self.graphiti.build_communities()
            return len(communities)
        except Exception:
            logger.exception("build_communities failed")
            return 0
        finally:
            self.graphiti.llm_client = original_client

    # --- Reads -------------------------------------------------------------

    def _driver_for(self, group_id: str | None) -> Any:
        """Resolve the driver that actually holds a group's data.

        FalkorDB is multi-tenant by *graph*: Graphiti clones the driver per
        `group_id`, so writes for `tenant-a` live in a FalkorDB graph named
        `tenant-a`, not in the configured default database. Reading without
        cloning silently returns nothing. Neo4j keeps everything in one database
        and separates groups by property, so it needs no clone.
        """
        if group_id is None or self.settings.graph_backend == "neo4j":
            return self._driver
        return self._driver.clone(group_id)

    async def query(
        self, cypher: str, group_id: str | None = None, read_only: bool = True, **params: Any
    ) -> list[dict[str, Any]]:
        await self.ensure_ready()
        assert self._driver is not None
        if read_only:
            upper_cypher = cypher.upper()
            if any(kw in upper_cypher for kw in ["CREATE ", "MERGE ", "SET ", "DELETE ", "DROP ", "REMOVE "]):
                raise ValueError("Write operations are not allowed in this query.")
        
        driver = self._driver_for(group_id)
        if group_id is not None and "group_id" not in params:
            params["group_id"] = group_id
        result = await driver.execute_query(cypher, **params)
        if not result:
            return []
        records = result[0] if isinstance(result, tuple) else result
        return list(records or [])

    async def query_groups(
        self, cypher: str, group_ids: list[str], **params: Any
    ) -> list[dict[str, Any]]:
        """Run one query across several groups and concatenate the rows."""
        rows: list[dict[str, Any]] = []
        for group_id in group_ids:
            try:
                for row in await self.query(cypher, group_id=group_id, **params):
                    rows.append({**row, "group_id": group_id})
            except Exception:
                logger.debug("query failed for group %s", group_id, exc_info=True)
        return rows

    async def search(self, query: str, group_ids: list[str] | None = None, limit: int = 10):
        await self.ensure_ready()
        assert self.graphiti is not None
        return await self.graphiti.search(query, group_ids=group_ids, num_results=limit)

    async def episode_count(self, group_ids: list[str]) -> int:
        rows = await self.query_groups("MATCH (e:Episodic) WHERE e.group_id = $group_id RETURN count(e) AS n", group_ids)
        return sum(int(row.get("n") or 0) for row in rows)

    async def episode_uuids(self, group_ids: list[str]) -> set[str]:
        rows = await self.query_groups("MATCH (e:Episodic) WHERE e.group_id = $group_id RETURN e.uuid AS uuid", group_ids)
        return {row["uuid"] for row in rows if row.get("uuid")}

    async def entity_count(self, group_ids: list[str]) -> int:
        rows = await self.query_groups("MATCH (n:Entity) WHERE n.group_id = $group_id RETURN count(n) AS n", group_ids)
        return sum(int(row.get("n") or 0) for row in rows)

    async def entities(self, group_ids: list[str]) -> list[dict[str, Any]]:
        return await self.query_groups(
            "MATCH (n:Entity) WHERE n.group_id = $group_id RETURN n.uuid AS uuid, n.name AS name, n.labels AS labels, "
            "n.summary AS summary, n.created_at AS created_at, n.need_attention AS need_attention, "
            "n.source_id AS source_id, n.source_type AS source_type",
            group_ids,
        )

    async def edges(
        self, group_ids: list[str], *, include_invalid: bool = True
    ) -> list[dict[str, Any]]:
        """All facts as (source, relation, target) with their temporal bounds."""
        cypher = (
            "MATCH (a:Entity)-[r]->(b:Entity) "
            "WHERE r.group_id = $group_id "
            "RETURN r.uuid AS uuid, a.uuid AS source_uuid, a.name AS source_name, "
            "b.uuid AS target_uuid, b.name AS target_name, r.name AS relation_type, "
            "r.fact AS fact, r.episodes AS episodes, r.created_at AS created_at, "
            "r.valid_at AS valid_at, r.invalid_at AS invalid_at, r.expired_at AS expired_at, "
            "r.source_id AS source_id, r.source_type AS source_type"
        )
        rows = await self.query_groups(cypher, group_ids)
        if include_invalid:
            return rows
        return [row for row in rows if not row.get("invalid_at") and not row.get("expired_at")]

    async def topology(
        self, group_ids: list[str], *, include_invalid: bool = False
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Fetch entities and edges with degree counts in a single pass.

        This replaces the separate ``entities()`` + ``edges()`` calls with one
        query that also computes node degree in-graph, avoiding two round-trips
        and Python-side degree computation.
        """
        # Single query: nodes with their degree, plus edges — all in one Cypher.
        invalid_filter = "" if include_invalid else "WHERE r.invalid_at IS NULL AND r.expired_at IS NULL"
        cypher = (
            "MATCH (n:Entity) WHERE n.group_id = $group_id "
            "OPTIONAL MATCH (n)-[r]-(m:Entity) "
            f"{invalid_filter} "
            "WITH n, count(r) AS degree "
            "RETURN n.uuid AS uuid, n.name AS name, n.labels AS labels, "
            "n.summary AS summary, n.need_attention AS need_attention, "
            "n.source_id AS source_id, n.source_type AS source_type, degree"
        )
        node_rows = await self.query_groups(cypher, group_ids)

        edge_cypher = (
            "MATCH (a:Entity)-[r]->(b:Entity) "
            "WHERE r.group_id = $group_id "
            + ("AND r.invalid_at IS NULL AND r.expired_at IS NULL " if not include_invalid else "")
            + "RETURN a.uuid AS source_uuid, b.uuid AS target_uuid, "
            "r.name AS relation_type, r.source_id AS source_id, r.source_type AS source_type"
        )
        edge_rows = await self.query_groups(edge_cypher, group_ids)

        return node_rows, edge_rows

    async def add_deterministic_nodes_and_edges(
        self,
        group_id: str,
        nodes_data: list[dict],
        edges_data: list[dict]
    ) -> None:
        """Insert AST/deterministic entities directly without LLM extraction."""
        if not self.is_ready or not self.graphiti:
            raise RuntimeError("Graph service is not ready")

        from graphiti_core.nodes import EntityNode
        from graphiti_core.edges import EntityEdge
        from graphiti_core.utils.bulk_utils import add_nodes_and_edges_bulk
        
        from datetime import datetime, UTC
        now = datetime.now(UTC).isoformat()
        nodes = []
        for n in nodes_data:
            node = EntityNode(
                uuid=n["uuid"],
                name=n["name"],
                summary=n["summary"],
                labels=n.get("labels", []),
                attributes=n.get("attributes", {}),
                group_id=group_id,
                created_at=now
            )
            nodes.append(node)
            
        edges = []
        for e in edges_data:
            import hashlib
            rel_name = e.get("labels", ["RELATES_TO"])[0] if e.get("labels") else "RELATES_TO"
            edge_id_str = f"{e['source_uuid']}-{rel_name}-{e['target_uuid']}"
            edge_uuid = hashlib.sha256(edge_id_str.encode()).hexdigest()
            
            edge = EntityEdge(
                uuid=edge_uuid,
                source_node_uuid=e["source_uuid"],
                target_node_uuid=e["target_uuid"],
                name=rel_name,
                fact=e["fact"],
                labels=e.get("labels", []),
                group_id=group_id,
                created_at=now,
                episodes=[]
            )
            edges.append(edge)
            
        embedder = self.graphiti.embedder
        # We need to manually generate embeddings first
        for node in nodes:
            await node.generate_name_embedding(embedder)
        for edge in edges:
            await edge.generate_embedding(embedder)
        
        driver = self._driver_for(group_id)
        await add_nodes_and_edges_bulk(driver, [], [], nodes, edges, embedder)

def utcnow() -> datetime:
    return datetime.now(UTC)
