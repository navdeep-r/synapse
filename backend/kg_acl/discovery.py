"""Generates Discovery Proposals for Knowledge Scopes.

Analyzes the structural connectedness of the knowledge graph (using BFS from hubs)
to propose new RBAC Knowledge Scopes automatically for supervisor review.

Also supports LLM-guided, prompt-driven discovery where a supervisor describes
the community they want and the LLM scores entities by semantic relevance.
"""

from __future__ import annotations

import json
import re
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from app.services import read_graph
from app.state import AppState
from kg_acl.authorized_graph import AuthorizedGraphRepository
from kg_acl.models import Principal, Sensitivity


async def _get_system_graph_snapshot(state: AppState) -> tuple[list[dict], list[dict], dict, list[dict], dict]:
    """Helper to load the graph and compute basic degree metrics for discovery."""
    system_principal = Principal(
        user_id="system",
        email="system@local",
        display_name="System",
        role_ids=["system"],
        clearance=Sensitivity.RESTRICTED,
        is_org_supervisor=True,
        active_scope_version_ids=[]
    )
    system_repo = AuthorizedGraphRepository(state.graph, state.db, system_principal)
    entities, edges = await read_graph(state, system_repo)
    
    if not entities:
        return [], [], {}, [], {}
        
    uuid_to_entity = {e["uuid"]: e for e in entities if e.get("uuid")}
    active_edges = [e for e in edges if not e.get("invalid_at") and not e.get("expired_at")]
    
    degree = defaultdict(int)
    for e in active_edges:
        s, t = e.get("source_uuid"), e.get("target_uuid")
        if s in uuid_to_entity: degree[s] += 1
        if t in uuid_to_entity: degree[t] += 1
        
    return entities, edges, uuid_to_entity, active_edges, degree


def _find_subgraphs(active_edges: list[dict], eligible_entities: set[str], min_entities: int) -> tuple[list[list[str]], dict[str, set[str]]]:
    """Extract connected components using BFS."""
    adjacency: dict[str, set[str]] = defaultdict(set)
    for e in active_edges:
        s, t = e.get("source_uuid"), e.get("target_uuid")
        if s in eligible_entities and t in eligible_entities:
            adjacency[s].add(t)
            adjacency[t].add(s)
            
    seen: set[str] = set()
    components: list[list[str]] = []
    for entity_uuid in eligible_entities:
        if entity_uuid in seen:
            continue
        stack, component = [entity_uuid], []
        seen.add(entity_uuid)
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbour in adjacency[current]:
                if neighbour not in seen:
                    seen.add(neighbour)
                    stack.append(neighbour)
        if len(component) >= min_entities:
            components.append(component)

    components.sort(key=len, reverse=True)
    return components, adjacency


async def generate_scope_proposals(state: AppState, min_entities: int = 10, min_density: float = 0.1) -> list[dict[str, Any]]:
    """Generate structural discovery proposals and persist them."""
    entities, edges, uuid_to_entity, active_edges, degree = await _get_system_graph_snapshot(state)
    if not entities:
        return []

    names = {u: ent.get("name") or "Unnamed" for u, ent in uuid_to_entity.items()}
    
    # Filter: only consider entities with degree >= 2 (filter out leaves)
    eligible_entities = {u for u, d in degree.items() if d >= 2}
    if not eligible_entities:
        return []

    components, adjacency = _find_subgraphs(active_edges, eligible_entities, min_entities=min_entities)
    proposals_created = []

    from collections import Counter
    from kg_acl.validator import CommunityValidator
    
    # Clear existing unapproved structural proposals to avoid duplicates
    await state.db.execute("DELETE FROM scope_proposals WHERE status = 'open' AND discovery_method = 'structural_bfs'")
    
    validator = CommunityValidator(min_density=min_density, min_entities=min_entities)
    
    for component in components[:20]:  # Limit to top 20 communities
        comp_set = set(component)
        if not validator.validate_structure(comp_set, active_edges):
            continue

        members = sorted(component, key=lambda u: len(adjacency[u]), reverse=True)
        density = validator.calculate_density(comp_set, active_edges)
        hub = names[members[0]]
        member_set = set(component)
        
        proposal_id = f"prop_{uuid.uuid4().hex[:12]}"
        now = datetime.now(UTC).isoformat()
        
        # FIXED: Proper boundary spec format
        boundary_spec = {
            "kind": "entity_list",
            "entity_uuids": component,
            "excluded_entity_uuids": [],
            "inclusion_mode": "explicit"
        }
        
        sample_entities = [names[u] for u in members[:10]]
        
        # Generate descriptive name based on entity types
        types_in_comp = Counter(e.get("labels", [])[0] if e.get("labels") else "Entity" for e in entities if e.get("uuid") in member_set)
        type_summary = ", ".join(f"{t}: {c}" for t, c in types_in_comp.most_common(3))
        suggested_name = f"Community: {hub} ({len(component)} entities, {type_summary})"
        description = f"Discovered community of {len(component)} entities centered on {hub}. Density: {density:.2%}. Contains: {type_summary}."
        
        await state.db.execute(
            """
            INSERT INTO scope_proposals(
                id, suggested_name, description, discovery_method, confidence,
                boundary_spec_json, sample_entities_json, source_distribution_json,
                estimated_entity_count, estimated_edge_count, suggested_sensitivity,
                status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
            """,
            (
                proposal_id,
                suggested_name,
                description,
                "structural_bfs",
                min(0.9, 0.5 + density * 0.5),  # Confidence based on density
                json.dumps(boundary_spec),
                json.dumps(sample_entities),
                "{}", # source_distribution mock
                len(component),
                int(density * len(component) * (len(component) - 1) / 2),
                "internal",
                now
            )
        )
        
        proposals_created.append({
            "id": proposal_id,
            "suggested_name": suggested_name,
            "estimated_entity_count": len(component),
            "estimated_edge_count": int(density * len(component) * (len(component) - 1) / 2),
            "density": density
        })

    return proposals_created


# ---------------------------------------------------------------------------
# LLM-guided prompt-driven community discovery
# ---------------------------------------------------------------------------

_DISCOVERY_SYSTEM_PROMPT = """\
You are a knowledge graph architect. Given a user's natural language description and a list of entities \
(each with their name and a brief summary), your job is to identify coherent communities relevant to the prompt.

RULES:
- A community must contain at least 3 entities.
- Only group entities that are genuinely related to each other AND to the prompt.
- The hub_uuid must be one of the entity UUIDs from the input list.
- entity_uuids must be a subset of the input UUIDs.
- relevance_score must be a float between 0.0 and 1.0.
- matched_keywords must be lowercase English words/phrases extracted from the prompt.
- Return valid JSON only — no markdown, no explanation, no preamble.
"""

_DISCOVERY_USER_PROMPT = """\
USER PROMPT: "{prompt}"

ENTITIES (uuid | name | summary):
{entity_list}

Identify up to {max_communities} coherent communities relevant to the user's prompt.

OUTPUT FORMAT (JSON only):
{{
  "communities": [
    {{
      "hub_uuid": "<uuid of most central entity>",
      "hub_name": "<name>",
      "name": "<descriptive community name>",
      "description": "<one sentence describing what this community represents>",
      "entity_uuids": ["<uuid>", ...],
      "relevance_score": 0.87,
      "matched_keywords": ["keyword1", "keyword2"]
    }}
  ]
}}
"""


def _sanitize_prompt(prompt: str, max_len: int = 500) -> str:
    """Strip control characters and enforce max length."""
    cleaned = re.sub(r"[\x00-\x1f\x7f]", " ", prompt).strip()
    return cleaned[:max_len]


async def generate_scope_proposals_with_prompt(
    state: AppState,
    prompt: str,
    max_communities: int = 10,
    min_entities: int = 3,
    min_density: float = 0.05,
) -> list[dict[str, Any]]:
    """Generate LLM-guided community proposals based on a natural-language prompt."""
    from collections import Counter
    from kg_acl.models import Principal, Sensitivity

    prompt = _sanitize_prompt(prompt)
    if not prompt:
        return []

    entities, edges, uuid_to_entity, active_edges, degree = await _get_system_graph_snapshot(state)

    if not entities:
        return []

    # ── 2. Smart candidate selection (keyword relevance + hub centrality) ───
    prompt_keywords = set(re.findall(r"\b\w{3,}\b", prompt.lower()))
    
    def _entity_score(u: str) -> tuple[int, int]:
        ent = uuid_to_entity[u]
        text = f"{ent.get('name') or ''} {ent.get('summary') or ''}".lower()
        hits = sum(1 for kw in prompt_keywords if kw in text)
        return (hits, degree.get(u, 0))

    # Take top 60 relevant entities to keep prompt compact (~2k tokens) and fast
    candidate_uuids = sorted(uuid_to_entity.keys(), key=_entity_score, reverse=True)[:60]

    # ── 3. Build the entity list string for the prompt ───────────────────────
    lines = []
    for u in candidate_uuids:
        ent = uuid_to_entity[u]
        name = ent.get("name") or "Unnamed"
        summary = (ent.get("summary") or "").replace("\n", " ")[:90]
        lines.append(f"{u} | {name} | {summary}")
    entity_list_str = "\n".join(lines)

    user_message = _DISCOVERY_USER_PROMPT.format(
        prompt=prompt,
        entity_list=entity_list_str,
        max_communities=max_communities,
    )

    # ── 4. Call the LLM ──────────────────────────────────────────────────────
    llm_communities: list[dict] = []
    try:
        llm_client = state.graph._new_llm_client()
        # Use the underlying chat completions interface
        response_text = await _call_llm_for_discovery(llm_client, _DISCOVERY_SYSTEM_PROMPT, user_message)
        if isinstance(response_text, dict):
            parsed = response_text
        elif isinstance(response_text, str):
            cleaned = response_text.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
                cleaned = re.sub(r"\s*```$", "", cleaned)
            parsed = json.loads(cleaned)
        else:
            parsed = {}
        llm_communities = parsed.get("communities", [])
    except Exception as exc:  # noqa: BLE001
        # Fallback: keyword-match entities to the prompt words
        llm_communities = _keyword_fallback(prompt, uuid_to_entity, degree, max_communities)

    # ── 5. Build adjacency for density checks ────────────────────────────────
    adjacency: dict[str, set[str]] = defaultdict(set)
    for e in edges:
        s, t = e.get("source_uuid"), e.get("target_uuid")
        if s in uuid_to_entity and t in uuid_to_entity:
            adjacency[s].add(t)
            adjacency[t].add(s)

    # ── 6. Persist valid communities as proposals ────────────────────────────
    valid_uuids = set(uuid_to_entity.keys())
    proposals_created = []
    now = datetime.now(UTC).isoformat()

    for community in llm_communities[:max_communities]:
        raw_uuids = community.get("entity_uuids", [])
        # Filter to only UUIDs that actually exist in graph
        member_uuids = [u for u in raw_uuids if u in valid_uuids]

        if len(member_uuids) < min_entities:
            continue

        # Density check
        comp_set = set(member_uuids)
        internal_edges = sum(
            1 for e in edges
            if e.get("source_uuid") in comp_set and e.get("target_uuid") in comp_set
        )
        possible = len(member_uuids) * (len(member_uuids) - 1) / 2
        density = internal_edges / possible if possible > 0 else 0

        if density < min_density:
            continue

        relevance_score = float(community.get("relevance_score", 0.5))
        matched_keywords = community.get("matched_keywords", [])
        suggested_name = community.get("name") or f"AI Community: {community.get('hub_name', 'Unknown')}"
        description = community.get("description") or f"LLM-discovered community of {len(member_uuids)} entities relevant to: {prompt[:80]}"

        # Sample entities: hub first, then by degree
        members_sorted = sorted(
            member_uuids,
            key=lambda u: (u == community.get("hub_uuid"), degree.get(u, 0)),
            reverse=True,
        )
        sample_entities = [uuid_to_entity[u].get("name") or "?" for u in members_sorted[:10]]

        boundary_spec = {
            "kind": "entity_list",
            "entity_uuids": member_uuids,
            "excluded_entity_uuids": [],
            "inclusion_mode": "explicit",
        }

        # Reuse source_distribution_json to store AI metadata (no schema migration)
        ai_metadata = {
            "discovery_method": "prompt_llm",
            "relevance_score": relevance_score,
            "matched_keywords": matched_keywords,
            "prompt": prompt[:200],
        }

        proposal_id = f"prop_{uuid.uuid4().hex[:12]}"
        confidence = min(0.95, 0.4 + relevance_score * 0.35 + density * 0.3)

        await state.db.execute(
            """
            INSERT INTO scope_proposals(
                id, suggested_name, description, discovery_method, confidence,
                boundary_spec_json, sample_entities_json, source_distribution_json,
                estimated_entity_count, estimated_edge_count, suggested_sensitivity,
                status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
            """,
            (
                proposal_id,
                suggested_name,
                description,
                "prompt_llm",
                confidence,
                json.dumps(boundary_spec),
                json.dumps(sample_entities),
                json.dumps(ai_metadata),
                len(member_uuids),
                internal_edges,
                "internal",
                now,
            ),
        )

        proposals_created.append({
            "id": proposal_id,
            "suggested_name": suggested_name,
            "description": description,
            "estimated_entity_count": len(member_uuids),
            "estimated_edge_count": internal_edges,
            "confidence": confidence,
            "relevance_score": relevance_score,
            "matched_keywords": matched_keywords,
            "discovery_method": "prompt_llm",
        })

    # Sort by relevance descending
    proposals_created.sort(key=lambda p: p["relevance_score"], reverse=True)
    return proposals_created


async def _call_llm_for_discovery(llm_client: Any, system_prompt: str, user_message: str) -> str:
    """Call the LLM client and return the raw text response."""
    # Graphiti's LLMClient interface: client.generate_response(messages)
    # We call the underlying method depending on what's available.
    from graphiti_core.llm_client.client import LLMClient
    from graphiti_core.prompts.models import Message

    messages = [
        Message(role="system", content=system_prompt),
        Message(role="user", content=user_message),
    ]

    if hasattr(llm_client, "generate_response"):
        result = await llm_client.generate_response(messages, max_tokens=2048)
        if isinstance(result, (str, dict)):
            return result
        if hasattr(result, "content"):
            return result.content
        return str(result)

    raise RuntimeError("LLM client does not expose generate_response")


def _keyword_fallback(
    prompt: str,
    uuid_to_entity: dict[str, dict],
    degree: dict[str, int],
    max_communities: int,
) -> list[dict]:
    """Simple keyword-based fallback when LLM is unavailable."""
    keywords = [w.lower() for w in re.findall(r"\b\w{3,}\b", prompt)]
    if not keywords:
        return []

    scored: dict[str, float] = {}
    for u, ent in uuid_to_entity.items():
        text = ((ent.get("name") or "") + " " + (ent.get("summary") or "")).lower()
        hits = sum(1 for kw in keywords if kw in text)
        if hits:
            scored[u] = hits / len(keywords)

    if not scored:
        return []

    # Build one community from all matched entities
    matched_sorted = sorted(scored, key=lambda u: (scored[u], degree.get(u, 0)), reverse=True)
    hub_uuid = matched_sorted[0]
    hub_name = uuid_to_entity[hub_uuid].get("name") or "Unknown"

    return [{
        "hub_uuid": hub_uuid,
        "hub_name": hub_name,
        "name": f"Keyword Community: {hub_name}",
        "description": f"Entities matching keywords from prompt: {prompt[:80]}",
        "entity_uuids": matched_sorted[:50],
        "relevance_score": max(scored.values()),
        "matched_keywords": keywords[:10],
    }]
