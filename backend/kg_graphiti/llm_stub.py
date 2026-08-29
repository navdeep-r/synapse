"""Deterministic, offline stand-in for Graphiti's extraction LLM.

COUPLING WARNING (R4)
---------------------
This client dispatches on `response_model.__name__`, which is part of
`graphiti_core`'s *internal* prompt set, not its public API. There is no semver
signal when those models change. `graphiti-core` is therefore pinned exactly in
pyproject.toml, and `tests/test_prompt_manifest.py` diffs the models Graphiti
actually requests during a real run against `tests/fixtures/prompt_models.json`.

Upgrade procedure: bump the pin, run the manifest test, read the diff, extend
`_HANDLERS`, refresh the manifest.

SILENT-FAILURE POLICY (R2)
--------------------------
An unrecognised response model is never answered silently. Every fallback
increments `stub_fallback` on the shared counter, logs at WARNING, and raises
outright when `SYNAPSE_STRICT_PROMPTS=1` (which tests enable). A neutral answer
returned quietly would let resolution or invalidation no-op while the pipeline
still reported success.
"""

from __future__ import annotations

import json
import logging
import re
import typing
from collections import Counter
from collections.abc import Callable

from graphiti_core.llm_client.client import LLMClient
from graphiti_core.llm_client.config import DEFAULT_MAX_TOKENS, LLMConfig, ModelSize
from graphiti_core.prompts.models import Message
from pydantic import BaseModel

from kg_graphiti import extraction, resolution
from kg_graphiti.ontology import EDGE_TYPES, ENTITY_TYPES
from kg_graphiti.resolution import LEGAL_SUFFIXES

logger = logging.getLogger("synapse.llm_stub")

# Response models that are our own ontology types rather than Graphiti prompts.
ONTOLOGY_MODEL_NAMES = frozenset(ENTITY_TYPES) | frozenset(EDGE_TYPES)

# All-lowercase technology names, which the capitalisation heuristic cannot catch.
KNOWN_TECHNOLOGIES = frozenset(
    {
        "postgres", "postgresql", "mysql", "sqlite", "redis", "kafka", "rabbitmq",
        "elasticsearch", "mongodb", "cassandra", "snowflake", "databricks",
        "kubernetes", "docker", "terraform", "nginx", "envoy", "airflow",
        "python", "java", "rust", "golang", "go", "ruby", "django", "flask",
        "fastapi", "react", "angular", "vue", "node", "nodejs", "grpc", "graphql",
    }
)

Handler = Callable[["StubLLMClient", str], dict[str, typing.Any]]


class UnknownPromptError(RuntimeError):
    """Raised in strict mode when Graphiti asks for an unmapped response model."""


def _tag(content: str, tag: str) -> str:
    """Pull the body of a `<TAG>...</TAG>` block out of a prompt."""
    match = re.search(rf"<{re.escape(tag)}>(.*?)</{re.escape(tag)}>", content, re.S)
    return match.group(1).strip() if match else ""


# Graphiti wraps the episode body in a different tag per prompt variant:
# extract_message uses <CURRENT MESSAGE>, extract_text uses <TEXT>, extract_json
# uses <JSON>, and extract_edges uses <CURRENT_MESSAGE>.
_EPISODE_TAGS = (
    "CURRENT_MESSAGE",
    "CURRENT MESSAGE",
    "TEXT",
    "JSON",
    "MESSAGES",
    "EPISODE_CONTENT",
)


def _reference_time(content: str) -> str | None:
    """The episode's reference timestamp, as an ISO string Graphiti can parse.

    The prompt renders this with a trailing inline comment
    ("2026-08-07 12:05:24+00:00  # ISO 8601 (UTC); ..."), which must be removed
    or `datetime.fromisoformat` rejects the whole value and the fact silently
    loses its temporal anchor.
    """
    raw = _tag(content, "REFERENCE_TIME").split("#")[0].strip()
    if not raw:
        return None
    from datetime import datetime

    try:
        datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        logger.debug("Unparseable REFERENCE_TIME %r", raw)
        return None
    return raw


def _episode_text(content: str) -> str:
    for tag in _EPISODE_TAGS:
        body = _tag(content, tag)
        if body:
            return body
    return ""


def _json_list(blob: str) -> list[dict[str, typing.Any]]:
    """Parse a JSON array out of a prompt block, tolerating Python-style repr."""
    blob = blob.strip()
    if not blob:
        return []
    for loader in (json.loads, _python_literal):
        try:
            value = loader(blob)
        except Exception:
            continue
        if isinstance(value, list):
            return [v for v in value if isinstance(v, dict)]
        if isinstance(value, dict):
            return [value]
    return []


def _python_literal(blob: str) -> typing.Any:
    import ast

    return ast.literal_eval(blob)


def _entity_type_ids(content: str) -> dict[str, int]:
    """Map an entity type name to the id Graphiti expects back.

    The prompt renders entity types as a list of `{entity_type_id, entity_type_name}`
    records; falling back to 0 ("Entity") is always schema-valid.
    """
    mapping: dict[str, int] = {}
    for record in _json_list(_tag(content, "ENTITY TYPES")):
        name = record.get("entity_type_name") or record.get("name")
        type_id = record.get("entity_type_id", record.get("id"))
        if isinstance(name, str) and isinstance(type_id, int):
            mapping[name.casefold()] = type_id
    return mapping


def _is_technology(name: str) -> bool:
    """Technologies read as companies to a name-shape heuristic, so detect them first.

    Internal capitalisation in a single token (PostgreSQL, MongoDB, TypeScript) is
    an unusually reliable signal; the seed list covers the all-lowercase names that
    shape misses.
    """
    if name.casefold() in KNOWN_TECHNOLOGIES:
        return True
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9.+#]*", name)) and bool(
        re.search(r"[a-z][A-Z0-9]", name)
    )


def _classify(name: str, type_ids: dict[str, int]) -> int:
    """Best-effort entity typing using the ontology names Graphiti supplied."""
    lowered = name.casefold()
    if not type_ids:
        return 0

    def pick(*candidates: str) -> int | None:
        for candidate in candidates:
            if candidate in type_ids:
                return type_ids[candidate]
        return None

    words = lowered.split()
    stripped = [w.strip(".,") for w in words]
    if _is_technology(name):
        chosen = pick("product", "repository")
    elif re.search(r"[-_]", lowered) or lowered.endswith((".py", ".ts", "-service", "-library")):
        chosen = pick("repository", "product")
    elif stripped and stripped[-1] in LEGAL_SUFFIXES:
        # "Tech Corp Inc" is the shape of a person's name but is unambiguously
        # a company, and it is the shape that dominates enterprise corpora.
        chosen = pick("organization", "product")
    elif len(words) >= 2 and all(w[:1].isalpha() for w in words) and len(words) <= 3:
        chosen = pick("employee", "person")
    else:
        chosen = pick("organization", "product", "document")
    return chosen if chosen is not None else 0


class StubLLMClient(LLMClient):
    """Answers Graphiti's prompts from deterministic heuristics."""

    def __init__(
        self,
        config: LLMConfig | None = None,
        *,
        strict: bool = False,
        counters: Counter | None = None,
    ) -> None:
        super().__init__(config or LLMConfig(model="synapse-stub"), cache=False)
        self.strict = strict
        # Shared with the observability layer so fallbacks become a visible metric.
        self.counters: Counter = counters if counters is not None else Counter()
        self.observed_models: set[str] = set()

    # --- LLMClient contract ------------------------------------------------

    async def _generate_response(
        self,
        messages: list[Message],
        response_model: type[BaseModel] | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        model_size: ModelSize = ModelSize.medium,
    ) -> dict[str, typing.Any]:
        content = "\n\n".join(m.content for m in messages if m.role == "user") or (
            messages[-1].content if messages else ""
        )
        model_name = response_model.__name__ if response_model else "None"
        self.observed_models.add(model_name)
        self.counters["llm_calls"] += 1

        # Custom entity/edge types from our own ontology come back as the response
        # model for Graphiti's attribute-extraction pass, so they are dispatched by
        # membership rather than by name.
        if model_name in ONTOLOGY_MODEL_NAMES:
            self.counters["prompt::ontology_attributes"] += 1
            return self._ontology_attributes(response_model)

        handler = _HANDLERS.get(model_name)
        if handler is None:
            return self._fallback(model_name, response_model)

        self.counters[f"prompt::{model_name}"] += 1
        return handler(self, content)

    def _ontology_attributes(
        self, response_model: type[BaseModel] | None
    ) -> dict[str, typing.Any]:
        """Attribute extraction for custom entity types.

        Every field is returned null on purpose. Graphiti's own base client warns
        that models routinely copy a field's *description* into its value when no
        real value exists; inventing a department or job title would put
        unfalsifiable data into the graph. Null means "not asserted", which is
        both honest and schema-valid since all ontology fields are optional.
        """
        if response_model is None:
            return {}
        return dict.fromkeys(response_model.model_json_schema().get("properties", {}))

    # --- R2: the loud fallback --------------------------------------------

    def _fallback(
        self, model_name: str, response_model: type[BaseModel] | None
    ) -> dict[str, typing.Any]:
        self.counters["stub_fallback"] += 1
        self.counters[f"fallback::{model_name}"] += 1
        message = (
            f"StubLLMClient has no handler for response model {model_name!r}. "
            "Graphiti's internal prompt set has probably changed (see R4 in the README). "
            "Resolution or invalidation for this call did nothing."
        )
        if self.strict:
            raise UnknownPromptError(message)
        logger.warning(message)
        return _neutral_payload(response_model)

    # --- Handlers ----------------------------------------------------------

    def _extracted_entities(self, content: str) -> dict[str, typing.Any]:
        episode = _episode_text(content)
        type_ids = _entity_type_ids(content)
        return {
            "extracted_entities": [
                {"name": name, "entity_type_id": _classify(name, type_ids)}
                for name in extraction.extract_entities(episode)
            ]
        }

    def _combined_extraction(self, content: str) -> dict[str, typing.Any]:
        episode = _episode_text(content)
        type_ids = _entity_type_ids(content)
        names = extraction.extract_entities(episode)
        edges = extraction.extract_relations(episode, names)
        return {
            "extracted_entities": [
                {"name": name, "entity_type_id": _classify(name, type_ids)} for name in names
            ],
            "edges": [
                {
                    "source_entity_name": hit.source,
                    "target_entity_name": hit.target,
                    "relation_type": hit.relation_type,
                    "fact": hit.fact,
                }
                for hit in edges
            ],
        }

    def _extracted_edges(self, content: str) -> dict[str, typing.Any]:
        episode = _episode_text(content)
        entities = [
            record.get("name", "")
            for record in _json_list(_tag(content, "ENTITIES"))
            if record.get("name")
        ]
        hits = extraction.extract_relations(episode, entities or None)

        # Graphiti passes edge_type_map through as FACT_TYPES but does not enforce
        # it, so a relation stays in the closed vocabulary while still joining a
        # type pair the ontology forbids. Enforce it here, and count rejections so
        # a mis-typed entity shows up as a metric rather than a missing edge.
        node_types = _entity_types(content)
        signatures = _fact_type_signatures(content)
        kept = []
        for hit in hits:
            allowed = signatures.get(hit.relation_type)
            pair = (node_types.get(hit.source, "Entity"), node_types.get(hit.target, "Entity"))
            if allowed is not None and pair not in allowed:
                self.counters["edge_type_rejected"] += 1
                logger.debug(
                    "rejected %s%s: not permitted between %s and %s",
                    hit.relation_type,
                    (hit.source, hit.target),
                    *pair,
                )
                continue
            kept.append(hit)
        hits = kept

        # Anchor every fact to the episode's reference time. Graphiti only
        # invalidates a contradicted edge when both edges carry `valid_at`
        # (resolve_edge_contradictions compares them), so returning null here
        # would silently disable the entire bi-temporal model.
        valid_at = _reference_time(content)

        return {
            "edges": [
                {
                    "source_entity_name": hit.source,
                    "target_entity_name": hit.target,
                    "relation_type": hit.relation_type,
                    "fact": hit.fact,
                    "valid_at": valid_at,
                    "invalid_at": None,
                }
                for hit in hits
            ]
        }

    def _node_resolutions(self, content: str) -> dict[str, typing.Any]:
        """R1: the merge decision. Structural match only — see resolution.compare."""
        extracted = _json_list(_tag(content, "ENTITIES"))
        existing = _json_list(_tag(content, "EXISTING ENTITIES"))

        resolutions = []
        for index, entity in enumerate(extracted):
            name = entity.get("name", "") or ""
            entity_id = entity.get("id", index)
            best_id, best_score, best_name = -1, 0.0, name

            for candidate in existing:
                candidate_name = candidate.get("name", "") or ""
                candidate_id = candidate.get("candidate_id", candidate.get("id", -1))
                verdict = resolution.compare(name, candidate_name)
                if verdict.is_match and verdict.score > best_score:
                    best_id, best_score = candidate_id, verdict.score
                    # Prefer the more complete surface form, as the prompt asks.
                    best_name = (
                        candidate_name if len(candidate_name) > len(name) else name
                    )

            resolutions.append(
                {"id": entity_id, "name": best_name, "duplicate_candidate_id": best_id}
            )

        return {"entity_resolutions": resolutions}

    def _node_duplicate(self, content: str) -> dict[str, typing.Any]:
        resolved = self._node_resolutions(content)["entity_resolutions"]
        if resolved:
            return resolved[0]
        return {"id": 0, "name": "", "duplicate_candidate_id": -1}

    def _edge_duplicate(self, content: str) -> dict[str, typing.Any]:
        """Duplicate and contradiction detection over facts.

        Contradiction is what drives Graphiti's bi-temporal invalidation, so this
        handler is what makes `t_invalid` ever get set in the stub configuration.
        """
        existing = _json_list(_tag(content, "EXISTING FACTS"))
        candidates = _json_list(_tag(content, "FACT INVALIDATION CANDIDATES"))

        # Unlike the two lists above, <NEW FACT> is rendered as a bare sentence
        # rather than a JSON record, so it has to be read as raw text.
        new_block = _tag(content, "NEW FACT")
        new_records = _json_list(new_block)
        new_text = (
            str(new_records[0].get("fact", "")) if new_records else new_block.strip()
        )

        duplicates: list[int] = []
        contradictions: list[int] = []

        new_key = _fact_signature(new_text)

        for bucket, is_existing in ((existing, True), (candidates, False)):
            for record in bucket:
                idx = record.get("idx")
                if not isinstance(idx, int):
                    continue
                old_text = str(record.get("fact", ""))
                if not old_text:
                    continue

                if _normalize_fact(old_text) == _normalize_fact(new_text):
                    if is_existing:
                        duplicates.append(idx)
                    continue

                old_key = _fact_signature(old_text)
                # Same subject and relation, different object: the new statement
                # supersedes the old one.
                if (
                    new_key
                    and old_key
                    and new_key[0] == old_key[0]
                    and new_key[1] == old_key[1]
                    and new_key[2] != old_key[2]
                ):
                    contradictions.append(idx)

        return {"duplicate_facts": duplicates, "contradicted_facts": contradictions}

    def _entity_summary(self, content: str) -> dict[str, typing.Any]:
        episode = _episode_text(content) or content
        name = _first_entity_name(content)
        return {"summary": extraction.summarize(name, episode)}

    def _summary(self, content: str) -> dict[str, typing.Any]:
        return {"summary": _condense(content)}

    def _summary_description(self, content: str) -> dict[str, typing.Any]:
        return {"description": _condense(content, max_chars=120)}

    def _summarized_entity(self, content: str) -> dict[str, typing.Any]:
        name = _first_entity_name(content)
        return {"name": name, "summary": _condense(content)}

    def _summarized_entities(self, content: str) -> dict[str, typing.Any]:
        entities = _json_list(_tag(content, "ENTITIES"))
        return {
            "summaries": [
                {
                    "name": record.get("name", ""),
                    "summary": str(record.get("summary", "") or record.get("name", "")),
                }
                for record in entities
            ]
        }

    def _edge_timestamps(self, content: str) -> dict[str, typing.Any]:
        # Without real temporal parsing, asserting nothing is safer than guessing;
        # Graphiti falls back to the episode reference time.
        return {"valid_at": None, "invalid_at": None}

    def _batch_edge_timestamps(self, content: str) -> dict[str, typing.Any]:
        edges = _json_list(_tag(content, "EDGES")) or _json_list(_tag(content, "FACTS"))
        return {
            "timestamps": [
                {"valid_at": None, "invalid_at": None} for _ in range(max(len(edges), 0))
            ]
        }

    def _saga_summary(self, content: str) -> dict[str, typing.Any]:
        return {"summary": _condense(content)}

    def _query_expansion(self, content: str) -> dict[str, typing.Any]:
        return {"query": _condense(content, max_chars=120)}

    def _qa_response(self, content: str) -> dict[str, typing.Any]:
        return {"ANSWER": _condense(content, max_chars=200)}

    def _eval_response(self, content: str) -> dict[str, typing.Any]:
        return {"is_correct": False, "reasoning": "stub client does not evaluate"}

    def _eval_add_episode_results(self, content: str) -> dict[str, typing.Any]:
        return {"candidate_is_worse": False, "reasoning": "stub client does not evaluate"}


def _normalize_fact(fact: str) -> str:
    return " ".join(resolution.normalize(fact).split())


def _fact_signature(fact: str) -> tuple[str, str, str] | None:
    """Reduce a fact sentence to (subject, relation, object) for contradiction checks."""
    hits = extraction.extract_relations(fact)
    if not hits:
        return None
    hit = hits[0]
    return (
        resolution.canonical_key(hit.source),
        hit.relation_type,
        resolution.canonical_key(hit.target),
    )


def _entity_types(content: str) -> dict[str, str]:
    """Map each entity name to its single ontology type.

    Every node carries the generic `Entity` label alongside its real one. Reducing
    to the specific label matters: if a typed node were allowed to match on
    `Entity`, the ("Entity", "Entity") catch-all in EDGE_TYPE_MAP would make every
    pair legal and the check would be decorative.
    """
    index: dict[str, str] = {}
    for record in _json_list(_tag(content, "ENTITIES")):
        name = record.get("name")
        if not name:
            continue
        raw_types = record.get("labels") or record.get("entity_types") or []
        specific = [t for t in raw_types if t != "Entity"]
        index[str(name)] = specific[0] if specific else "Entity"
    return index


def _fact_type_signatures(content: str) -> dict[str, set[tuple[str, str]]]:
    """The (source_type, target_type) pairs Graphiti says each relation permits."""
    signatures: dict[str, set[tuple[str, str]]] = {}
    for record in _json_list(_tag(content, "FACT_TYPES")):
        name = record.get("fact_type_name")
        if not name:
            continue
        signatures[str(name)] = {
            (str(pair[0]), str(pair[1]))
            for pair in record.get("fact_type_signatures") or []
            if isinstance(pair, list) and len(pair) == 2
        }
    return signatures


def _first_entity_name(content: str) -> str:
    for tag in ("ENTITY", "ENTITIES", "NODE"):
        records = _json_list(_tag(content, tag))
        if records and records[0].get("name"):
            return str(records[0]["name"])
    match = re.search(r'"name"\s*:\s*"([^"]+)"', content)
    return match.group(1) if match else "Entity"


def _condense(content: str, max_chars: int = 220) -> str:
    """Take the most content-bearing line of a prompt as a summary."""
    for block in ("CURRENT_MESSAGE", "CURRENT MESSAGE", "SUMMARIES", "ENTITY"):
        body = _tag(content, block)
        if body:
            content = body
            break
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    text = " ".join(lines)
    text = re.sub(r"\s+", " ", text)
    return text[:max_chars].rstrip()


def _neutral_payload(response_model: type[BaseModel] | None) -> dict[str, typing.Any]:
    """A schema-valid empty answer, used only after the fallback has been counted."""
    if response_model is None:
        return {}
    payload: dict[str, typing.Any] = {}
    schema = response_model.model_json_schema()
    for field, spec in schema.get("properties", {}).items():
        kind = spec.get("type")
        if kind == "array":
            payload[field] = []
        elif kind == "string":
            payload[field] = ""
        elif kind == "integer":
            payload[field] = -1 if "candidate" in field else 0
        elif kind == "number":
            payload[field] = 0.0
        elif kind == "boolean":
            payload[field] = False
        else:
            payload[field] = None
    return payload


_HANDLERS: dict[str, Handler] = {
    "ExtractedEntities": StubLLMClient._extracted_entities,
    "CombinedExtraction": StubLLMClient._combined_extraction,
    "ExtractedEdges": StubLLMClient._extracted_edges,
    "NodeResolutions": StubLLMClient._node_resolutions,
    "NodeDuplicate": StubLLMClient._node_duplicate,
    "EdgeDuplicate": StubLLMClient._edge_duplicate,
    "EntitySummary": StubLLMClient._entity_summary,
    "Summary": StubLLMClient._summary,
    "SummaryDescription": StubLLMClient._summary_description,
    "SummarizedEntity": StubLLMClient._summarized_entity,
    "SummarizedEntities": StubLLMClient._summarized_entities,
    "EdgeTimestamps": StubLLMClient._edge_timestamps,
    "BatchEdgeTimestamps": StubLLMClient._batch_edge_timestamps,
    "SagaSummary": StubLLMClient._saga_summary,
    "QueryExpansion": StubLLMClient._query_expansion,
    "QAResponse": StubLLMClient._qa_response,
    "EvalResponse": StubLLMClient._eval_response,
    "EvalAddEpisodeResults": StubLLMClient._eval_add_episode_results,
}

SUPPORTED_RESPONSE_MODELS = frozenset(_HANDLERS)
