"""Community reports and the Simulate Agent Query panel."""

from __future__ import annotations

import asyncio
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.services import build_provenance_index, edge_tag_map, episodes_of, read_graph
from app.state import AppState, get_state
from kg_query import simulate_query
from kg_acl.authorized_graph import AuthorizedGraphRepository
from kg_acl.principal import get_authorized_graph

router = APIRouter()


class SimulateRequest(BaseModel):
    query: str
    access_level: str = "internal"
    limit: int = 10
    include_invalid: bool = False


@router.get("/reports")
async def list_reports(state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)) -> list[dict]:
    return await build_reports(state, graph)


@router.get("/reports/{report_id}")
async def get_report(report_id: str, state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)) -> dict:
    reports = await build_reports(state, graph)
    report = next((r for r in reports if r["id"] == report_id), None)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Unknown report: {report_id}")
    return report


@router.post("/query/simulate")
async def simulate(request: SimulateRequest, state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)) -> dict:
    """Show what an agent would retrieve, and what access control withheld.

    R6: this panel is documented as steward-only. It is not access-controlled by
    the API unless SYNAPSE_AUTH_TOKEN is set.
    """
    _entities, edges = await read_graph(state, graph)
    index = await build_provenance_index(state)
    for edge in edges:
        edge["episodes"] = episodes_of(edge)

    provenance = {
        (edge.get("uuid") or ""): index.records_for(episodes_of(edge)) for edge in edges
    }

    return await asyncio.to_thread(
        simulate_query,
        request.query,
        edges,
        edge_tags=edge_tag_map(edges, index),
        provenance=provenance,
        access_level=request.access_level,
        limit=request.limit,
        include_invalid=request.include_invalid,
    )


async def build_reports(state: AppState, graph: AuthorizedGraphRepository) -> list[dict]:
    """Summarise each connected neighbourhood of the graph.

    Graphiti's own `build_communities` needs an LLM to name and summarise a
    community. With the offline stub those names would be meaningless, so
    reports are derived structurally instead: the most connected entities and
    the facts around them.
    """
    entities, edges = await read_graph(state, graph)
    if not entities:
        return []

    names = {e["uuid"]: e.get("name") or "Unnamed" for e in entities if e.get("uuid")}

    adjacency: dict[str, set[str]] = defaultdict(set)
    active_edges = [e for e in edges if not e.get("invalid_at")]
    # Superseded facts still define the neighbourhood. Building adjacency from
    # active edges alone would drop an entity whose only fact was invalidated,
    # and the report would then omit the very supersession worth flagging.
    for edge in edges:
        source, target = edge.get("source_uuid"), edge.get("target_uuid")
        if source in names and target in names:
            adjacency[source].add(target)
            adjacency[target].add(source)

    # Connected components via breadth-first search.
    seen: set[str] = set()
    components: list[list[str]] = []
    for uuid in names:
        if uuid in seen:
            continue
        stack, component = [uuid], []
        seen.add(uuid)
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbour in adjacency[current]:
                if neighbour not in seen:
                    seen.add(neighbour)
                    stack.append(neighbour)
        components.append(component)

    components.sort(key=len, reverse=True)

    reports = []
    for index_, component in enumerate(components):
        if len(component) < 2:
            continue

        members = sorted(component, key=lambda u: len(adjacency[u]), reverse=True)
        hub = names[members[0]]
        member_set = set(component)
        facts = [
            e
            for e in active_edges
            if e.get("source_uuid") in member_set and e.get("target_uuid") in member_set
        ]
        superseded = [
            e
            for e in edges
            if e.get("invalid_at")
            and e.get("source_uuid") in member_set
            and e.get("target_uuid") in member_set
        ]

        lines = [f"{names.get(e.get('source_uuid'), '?')} {e.get('relation_type')} "
                 f"{names.get(e.get('target_uuid'), '?')}" for e in facts[:12]]

        reports.append(
            {
                "id": f"report-{index_ + 1:03d}",
                "title": f"{hub} and {len(component) - 1} connected entit"
                f"{'y' if len(component) == 2 else 'ies'}",
                "date": "Derived from the current graph",
                # 'signal' flags a neighbourhood containing superseded facts.
                "status": "signal" if superseded else "confirmed",
                "includedInExport": not superseded,
                "content": (
                    f"This neighbourhood centres on {hub} and contains "
                    f"{len(component)} entities linked by {len(facts)} active fact(s)."
                    + (
                        f" {len(superseded)} fact(s) in this neighbourhood have been "
                        "superseded by newer information."
                        if superseded
                        else ""
                    )
                    + ("\n\n" + "\n".join(lines) if lines else "")
                ),
                "entity_count": len(component),
                "fact_count": len(facts),
                "superseded_count": len(superseded),
                "members": [names[u] for u in members[:20]],
            }
        )

    return reports
