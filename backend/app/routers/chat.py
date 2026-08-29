"""KG-grounded chat endpoint.

Uses Graphiti's hybrid search to retrieve the most relevant facts from the
knowledge graph, then synthesises a grounded answer with the configured LLM.

Improvements over v1:
- Fetches 2× candidates then re-ranks by relevance score before truncating
- Includes entity summaries alongside raw edge facts for richer context
- Uses conversation history to construct a richer search query for follow-ups
- Higher token budget and longer timeout for quality answers
- Structured prompt with clear sections (context, history, question)
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.state import AppState, get_state
from kg_acl.authorized_graph import AuthorizedGraphRepository
from kg_acl.principal import get_authorized_graph

logger = logging.getLogger("synapse.chat")

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response shapes
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(..., min_length=1)
    workspace: str | None = None  # None → search all workspaces
    num_facts: int = Field(default=20, ge=1, le=60)


class CitedFact(BaseModel):
    fact: str
    relation_type: str | None = None
    source_name: str | None = None
    target_name: str | None = None


class ChatResponse(BaseModel):
    answer: str
    citations: list[CitedFact]
    facts_used: int
    workspace: str | None


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post("/chat")
async def chat(
    body: ChatRequest,
    state: AppState = Depends(get_state),
    graph: AuthorizedGraphRepository = Depends(get_authorized_graph),
) -> ChatResponse:
    """Answer a question grounded entirely on the knowledge graph."""

    # ── 1. Resolve group_ids filter ──────────────────────────────────────────
    if body.workspace and body.workspace != "all":
        group_ids = [body.workspace]
    else:
        group_ids = await state.group_ids()

    # ── 2. Extract conversation history and build an enriched search query ───
    user_messages = [m for m in body.messages if m.role == "user"]
    if not user_messages:
        return ChatResponse(
            answer="No question found.", citations=[], facts_used=0, workspace=body.workspace
        )

    query = user_messages[-1].content

    # For follow-up questions, incorporate the last 2 assistant turns to
    # contextualise the search (e.g. "tell me more about that" needs context).
    search_query = query
    if len(body.messages) > 2:
        recent = body.messages[-5:]  # last 5 turns
        context_snippet = " ".join(
            m.content[:200] for m in recent if m.role in ("user", "assistant")
        )
        # Trim to a sensible search query length
        search_query = f"{query} {context_snippet}"[:1000]

    # ── 3. Hybrid Retrieval: Graphiti semantic search + Cypher entity & edge matching ───
    candidate_limit = min(body.num_facts * 2, 60)
    raw_edges: list = []
    try:
        await graph.ensure_ready()
        raw_edges = await graph.search(
            search_query, group_ids=group_ids, limit=candidate_limit
        )
    except Exception:
        logger.warning("Graph search failed", exc_info=True)

    # 3b. Direct Cypher Entity & Edge Search (handles Mails, GitHub files/PRs, and keyword matches)
    q_lower = query.lower()
    is_mail_query = any(k in q_lower for k in ("mail", "email", "gmail", "inbox", "thread", "message", "sender", "received", "sent"))
    is_github_query = any(k in q_lower for k in ("github", "repo", "commit", "pr", "pull request", "issue", "branch", "file", "code"))
    is_attention_query = any(k in q_lower for k in ("attention", "need_attention", "urgent", "todo", "action", "review", "bug", "alert"))

    matched_nodes: list[dict[str, Any]] = []
    cypher_edges: list[dict[str, Any]] = []
    try:
        allowed_entity_uuids = await graph._get_allowed_entity_uuids()
        allowed_edge_uuids = await graph._get_allowed_edge_uuids()

        if allowed_entity_uuids is None or len(allowed_entity_uuids) > 0:
            tokens = [t.strip(",.?!:;\"'") for t in query.split() if len(t.strip(",.?!:;\"'")) > 2]
            query_clauses = ["toLower(n.name) CONTAINS toLower($q)", "toLower(n.summary) CONTAINS toLower($q)"]
            for i, t in enumerate(tokens[:5]):
                query_clauses.append(f"toLower(n.name) CONTAINS toLower($t{i})")

            if is_mail_query:
                query_clauses.append("n.source_type = 'mail' OR 'Mail' IN n.labels OR 'Email' IN n.labels")
            if is_github_query:
                query_clauses.append("n.source_type = 'github' OR 'Repository' IN n.labels OR 'GitFile' IN n.labels OR 'GitPullRequest' IN n.labels OR 'GitIssue' IN n.labels")
            if is_attention_query:
                query_clauses.append("n.need_attention = true")

            node_where = " OR ".join(query_clauses)
            cypher_nodes = (
                f"MATCH (n:Entity) WHERE n.group_id IN $group_ids AND ({node_where}) "
                f"RETURN n.uuid AS uuid, n.name AS name, n.summary AS summary, n.source_type AS source_type, n.labels AS labels, n.need_attention AS need_attention "
                f"LIMIT 30"
            )
            params = {"group_ids": group_ids, "q": query, **{f"t{i}": t for i, t in enumerate(tokens[:5])}}
            raw_matched_nodes = await state.graph.query(cypher_nodes, **params)

            if allowed_entity_uuids is not None:
                matched_nodes = [n for n in raw_matched_nodes if n.get("uuid") in allowed_entity_uuids]
            else:
                matched_nodes = raw_matched_nodes

            if matched_nodes:
                matched_uuids = [n["uuid"] for n in matched_nodes if n.get("uuid")]
                cypher_edge_query = (
                    "MATCH (a:Entity)-[r]->(b:Entity) "
                    "WHERE (a.uuid IN $uuids OR b.uuid IN $uuids) "
                    "AND r.group_id IN $group_ids "
                    "AND r.invalid_at IS NULL AND r.expired_at IS NULL "
                    "RETURN r.uuid AS uuid, a.name AS source_name, a.summary AS source_summary, "
                    "b.name AS target_name, b.summary AS target_summary, "
                    "r.name AS relation_type, r.fact AS fact, r.source_type AS source_type "
                    "LIMIT 30"
                )
                raw_cypher_edges = await state.graph.query(cypher_edge_query, uuids=matched_uuids, group_ids=group_ids)
                if allowed_edge_uuids is not None:
                    cypher_edges = [e for e in raw_cypher_edges if e.get("uuid") in allowed_edge_uuids]
                else:
                    cypher_edges = raw_cypher_edges
    except Exception:
        logger.warning("Direct Cypher entity retrieval failed", exc_info=True)

    # ── 4. Re-rank and deduplicate facts ──────────────────────────────────────
    def _score(edge) -> float:
        if isinstance(edge, dict):
            return float(edge.get("score") or edge.get("relevance_score") or 0.0)
        return float(getattr(edge, "score", None) or getattr(edge, "relevance_score", None) or 0.0)

    ranked_edges = sorted(raw_edges, key=_score, reverse=True)[: body.num_facts]

    # ── 5. Build context block from ranked edges + Cypher edges + entity summaries ──
    citations: list[CitedFact] = []
    context_parts: list[str] = []
    seen_facts: set[str] = set()
    seen_entity_names: set[str] = set()
    entity_summaries: dict[str, str] = {}

    def _add_edge_fact(fact_text: str, relation: str, source_name: str, source_summary: str, target_name: str, target_summary: str):
        if not fact_text or fact_text in seen_facts:
            return
        seen_facts.add(fact_text)

        if source_name and source_summary and source_name not in seen_entity_names:
            entity_summaries[source_name] = source_summary
            seen_entity_names.add(source_name)
        if target_name and target_summary and target_name not in seen_entity_names:
            entity_summaries[target_name] = target_summary
            seen_entity_names.add(target_name)

        citations.append(
            CitedFact(
                fact=fact_text,
                relation_type=str(relation) if relation else None,
                source_name=source_name or None,
                target_name=target_name or None,
            )
        )
        if source_name and target_name:
            line = f"[{source_name} —{relation}→ {target_name}]: {fact_text}"
        elif source_name:
            line = f"[{source_name}]: {fact_text}"
        else:
            line = fact_text
        context_parts.append(line)

    for edge in ranked_edges:
        if isinstance(edge, dict):
            fact_text = edge.get("fact") or ""
            relation = edge.get("name") or edge.get("relation_type") or ""
            source_node = edge.get("source_node") or {}
            target_node = edge.get("target_node") or {}
            source_name = source_node.get("name", "") if isinstance(source_node, dict) else getattr(source_node, "name", "")
            source_summary = source_node.get("summary", "") if isinstance(source_node, dict) else getattr(source_node, "summary", "")
            target_name = target_node.get("name", "") if isinstance(target_node, dict) else getattr(target_node, "name", "")
            target_summary = target_node.get("summary", "") if isinstance(target_node, dict) else getattr(target_node, "summary", "")
        else:
            fact_text = getattr(edge, "fact", None) or ""
            relation = getattr(edge, "name", None) or getattr(edge, "relation_type", None) or ""
            src = getattr(edge, "source_node", None)
            tgt = getattr(edge, "target_node", None)
            source_name = getattr(src, "name", None) or ""
            source_summary = getattr(src, "summary", None) or ""
            target_name = getattr(tgt, "name", None) or ""
            target_summary = getattr(tgt, "summary", None) or ""

        _add_edge_fact(fact_text, relation, source_name, source_summary, target_name, target_summary)

    for ce in cypher_edges:
        _add_edge_fact(
            ce.get("fact") or "",
            ce.get("relation_type") or "",
            ce.get("source_name") or "",
            ce.get("source_summary") or "",
            ce.get("target_name") or "",
            ce.get("target_summary") or "",
        )

    # Also register summaries from matched nodes
    for node in matched_nodes:
        n_name = node.get("name") or ""
        n_summary = node.get("summary") or ""
        n_src = node.get("source_type") or "entity"
        if n_name and n_summary:
            entity_summaries[n_name] = n_summary
            line = f"[{n_src.upper()} Node: {n_name}]: {n_summary}"
            if line not in context_parts:
                context_parts.append(line)

    # ── 6. Build conversation history text ───────────────────────────────────
    history_text = ""
    if len(body.messages) > 1:
        prior = body.messages[:-1]
        history_text = "\n".join(
            f"{'User' if m.role == 'user' else 'Assistant'}: {m.content}"
            for m in prior[-8:]  # last 8 messages
        )

    # ── 7. Generate the grounded answer ──────────────────────────────────────
    answer = await _generate_answer(
        state=state,
        query=query,
        context_parts=context_parts,
        entity_summaries=entity_summaries,
        history_text=history_text,
    )

    return ChatResponse(
        answer=answer,
        citations=citations,
        facts_used=len(citations),
        workspace=body.workspace,
    )


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

_SYSTEM = """\
You are Synapse Assistant, an advanced enterprise intelligence AI. You answer questions \
about software codebases, git commits, pull requests, issues, email threads, communications, \
documentation, and project entities using verified facts extracted from the knowledge graph.

Guidelines:
1. Prioritise the provided knowledge graph facts and entity context when answering — they \
represent verified information about this workspace across code, emails, and documents.
2. If the user asks about emails or communications, synthesize relevant messages, senders, \
subjects, dates, and discussion points clearly.
3. If the user asks about codebase architecture, files, or PRs, synthesize technical details clearly.
4. If asked about items needing attention or action items, highlight pending tasks or bugs.
5. Reference specific entity names, email senders, subjects, and file names from the context.
6. Format your answer in clean, readable Markdown with clear headings and bullet points.
"""


async def _generate_answer(
    state: AppState,
    query: str,
    context_parts: list[str],
    entity_summaries: dict[str, str],
    history_text: str,
) -> str:
    settings = state.settings

    if not context_parts and not entity_summaries:
        return (
            "I could not find any relevant facts or entities in the knowledge graph to answer your question.\n\n"
            "Suggestions:\n"
            "- Ensure your data sources (GitHub repos, Mail accounts, or Documents) have completed syncing\n"
            "- Try mentioning specific names, email senders, subjects, or file paths\n"
            "- Check that the knowledge graph is active"
        )

    # Build rich context: facts + entity summaries
    facts_block = "\n".join(f"{i + 1}. {line}" for i, line in enumerate(context_parts))

    summaries_block = ""
    if entity_summaries:
        summaries_lines = "\n".join(
            f"- **{name}**: {summary[:300]}"
            for name, summary in list(entity_summaries.items())[:10]
        )
        summaries_block = f"\n\n**Entity Context:**\n{summaries_lines}"

    prompt_parts = [
        "## Knowledge Graph Facts (ranked by relevance)\n",
        facts_block,
    ]
    if summaries_block:
        prompt_parts.append(summaries_block)
    if history_text:
        prompt_parts.append(f"\n## Conversation History\n{history_text}")
    prompt_parts.append(f"\n## Question\n{query}")
    prompt_parts.append(
        "\n## Instructions\nUsing the facts and entity context above, provide a thorough, "
        "well-structured answer. Synthesise across multiple facts where useful. "
        "Reference specific entities and relationships by name."
    )

    prompt = "\n".join(prompt_parts)

    # ── Candidate LLMs (Primary + Fallback) ───────────────────────────────────
    candidates: list[tuple[str, str, str]] = []  # (base_url, api_key, model)

    if settings.llm_provider == "openai" and settings.openai_api_key:
        candidates.append(("https://api.openai.com/v1", settings.openai_api_key, settings.openai_model))
    elif settings.llm_provider == "groq" and settings.groq_api_key:
        candidates.append(("https://api.groq.com/openai/v1", settings.groq_api_key, settings.groq_model))
    elif settings.llm_provider == "ollama":
        candidates.append((settings.ollama_base_url, "ollama", settings.ollama_model))
    elif settings.llm_provider == "nvidia" and settings.nvidia_api_key:
        candidates.append((settings.nvidia_base_url, settings.nvidia_api_key, settings.nvidia_model))

    if settings.groq_api_key and settings.llm_provider != "groq":
        candidates.append(("https://api.groq.com/openai/v1", settings.groq_api_key, settings.groq_model))

    if candidates:
        import httpx
        for i, (base_url, api_key, model) in enumerate(candidates):
            try:
                # 30s timeout per candidate so UI doesn't stall indefinitely on hanging endpoints
                async with httpx.AsyncClient(timeout=35.0) as client:
                    resp = await client.post(
                        f"{base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}"},
                        json={
                            "model": model,
                            "messages": [
                                {"role": "system", "content": _SYSTEM},
                                {"role": "user", "content": prompt},
                            ],
                            "temperature": 0.3,
                            "max_tokens": 2048,
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    return data["choices"][0]["message"]["content"].strip()
            except Exception as exc:
                if i < len(candidates) - 1:
                    logger.warning("Primary LLM (%s on %s) failed: %s. Trying fallback...", model, base_url, exc)
                else:
                    logger.error("All LLM synthesis candidates failed: %s", exc)

    # ── Stub / unknown provider ───────────────────────────────────────────────
    return _fallback_answer(context_parts, query)


def _fallback_answer(context_parts: list[str], query: str) -> str:
    """Return a readable summary when no LLM is configured."""
    intro = f"Based on the knowledge graph, here are the most relevant facts for **{query}**:\n\n"
    facts = "\n".join(f"- {line}" for line in context_parts[:15])
    note = (
        "\n\n---\n_Note: LLM synthesis is unavailable. Configure `SYNAPSE_LLM_PROVIDER` "
        "in your `.env` for AI-generated answers._"
    )
    return intro + facts + note
