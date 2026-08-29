"""Ontology endpoints for the Ontology screen."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.services import read_graph
from app.state import AppState, get_state
from kg_ontology import OntologyRegistry
from kg_acl.authorized_graph import AuthorizedGraphRepository
from kg_acl.principal import get_authorized_graph

router = APIRouter()


class ChangeProposal(BaseModel):
    """Body posted by `OntologyScreen.handlePropose`."""

    relation_type: str
    field: str
    old_value: Any = None
    new_value: Any = None
    justification: str = ""


class EntityTypePatch(BaseModel):
    description: str = Field(min_length=1)


@router.get("/ontology/entity-types")
async def entity_types(state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)) -> list[dict]:
    return await OntologyRegistry(state.db).entity_types()


@router.patch("/ontology/entity-types/{type_id}")
async def patch_entity_type(
    type_id: str, patch: EntityTypePatch, state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)
) -> dict:
    updated = await OntologyRegistry(state.db).update_entity_description(
        type_id, patch.description
    )
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Unknown entity type: {type_id}")
    return updated


@router.get("/ontology/relation-types")
async def relation_types(state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)) -> list[dict]:
    return await OntologyRegistry(state.db).relation_types()


@router.get("/ontology/migrations")
async def migrations(state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)) -> list[dict]:
    """One array; the screen splits it on `status === 'pending'`."""
    return await OntologyRegistry(state.db).migrations()


@router.post("/ontology/changes")
async def propose_change(
    proposal: ChangeProposal, state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)
) -> dict:
    registry = OntologyRegistry(state.db)

    # The proposal may name a relation the ontology does not define; it is still
    # recorded so the request is not lost, and old_value is filled in when known.
    known = await registry.relation_type(proposal.relation_type)
    old_value = proposal.old_value
    if old_value is None and known is not None:
        old_value = known.get(proposal.field)

    affected = await _affected_edges(state, graph, proposal.relation_type)

    return await registry.propose_change(
        relation_type=proposal.relation_type,
        field=proposal.field,
        old_value=old_value,
        new_value=proposal.new_value,
        justification=proposal.justification,
        affected_edges=affected,
    )


@router.post("/ontology/migrations/{change_id}/approve")
async def approve_migration(change_id: str, state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)) -> dict:
    result = await OntologyRegistry(state.db).resolve_migration(change_id, approve=True)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Unknown migration: {change_id}")
    return result


@router.post("/ontology/migrations/{change_id}/reject")
async def reject_migration(change_id: str, state: AppState = Depends(get_state), graph: AuthorizedGraphRepository = Depends(get_authorized_graph)) -> dict:
    result = await OntologyRegistry(state.db).resolve_migration(change_id, approve=False)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Unknown migration: {change_id}")
    return result


async def _affected_edges(state: AppState, graph: AuthorizedGraphRepository, relation_type: str, limit: int = 25) -> list[dict]:
    """Sample the live edges a constraint change would apply to."""
    _entities, edges = await read_graph(state, graph)
    matching = [
        {
            "fact_id": edge.get("uuid"),
            "subject": edge.get("source_name"),
            "object": edge.get("target_name"),
            "fact": edge.get("fact"),
        }
        for edge in edges
        if edge.get("relation_type") == relation_type and not edge.get("invalid_at")
    ]
    return matching[:limit]
