"""Coerce LLM-produced node/edge attributes into property values FalkorDB accepts.

FalkorDB stores only primitives and arrays of primitives. Graphiti merges whatever
the extraction model returned in `attributes` straight into the property map
(`bulk_utils.add_nodes_and_edges_bulk_tx`), which is fine for a model that honours
the entity schema and fatal for one that does not.

They frequently do not. `openai/gpt-oss-20b` answers the flat `Employee(department,
title, manager_name)` schema with a nested object under its own `attributes` key and
invented field names:

    {"attributes": {"job_title": "Senior Engineer", "company": "Acme Corp."}}

FalkorDB rejects the dict, the bulk write raises, and the *entire episode* is lost —
including the entities and relationships that were extracted perfectly well. One
badly-shaped optional attribute should not cost the whole episode.

So this flattens one level (recovering the values as real, queryable properties),
JSON-encodes anything still non-primitive rather than discarding it, and drops empty
containers that would otherwise become useless empty properties.

This is a resilience layer, not a correctness fix: the model is still ignoring the
schema, and `attributes_json` on a node is the signal that it happened. The
alternative — letting the write fail — trades a partial result for no result.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any

logger = logging.getLogger("synapse.graph.sanitize")

PRIMITIVES = (str, int, float, bool, datetime, date, type(None))

# Never touched: written by Graphiti itself and already correctly typed. Embeddings
# in particular are large float arrays that must not be walked per element.
PRESERVED_KEYS = frozenset({"name_embedding", "fact_embedding", "labels", "episodes"})


def _is_primitive_array(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, PRIMITIVES) for item in value)


def _stringify(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)


def sanitize_properties(properties: dict[str, Any]) -> dict[str, Any]:
    """Return a property map FalkorDB will accept, preserving as much as possible."""
    clean: dict[str, Any] = {}
    overflow: dict[str, Any] = {}

    for key, value in properties.items():
        if key in PRESERVED_KEYS or isinstance(value, PRIMITIVES) or _is_primitive_array(value):
            clean[key] = value
            continue

        if isinstance(value, dict):
            # Flatten one level so the values stay queryable instead of becoming an
            # opaque blob. Existing keys win: an invented attribute must never
            # overwrite a field Graphiti set deliberately.
            for inner_key, inner_value in value.items():
                if inner_key in properties or inner_key in clean:
                    overflow[inner_key] = inner_value
                elif isinstance(inner_value, PRIMITIVES) or _is_primitive_array(inner_value):
                    clean[inner_key] = inner_value
                elif inner_value not in (None, {}, []):
                    overflow[inner_key] = inner_value
            continue

        if isinstance(value, list):
            overflow[key] = value
            continue

        clean[key] = _stringify(value)

    if overflow:
        clean["attributes_json"] = _stringify(overflow)

    return clean


def sanitize_params(params: dict[str, Any]) -> dict[str, Any]:
    """Sanitize the `nodes`/`edges` lists Graphiti passes to a bulk write."""
    out: dict[str, Any] = {}
    for key, value in params.items():
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            out[key] = [sanitize_properties(item) for item in value]
        elif isinstance(value, dict):
            out[key] = sanitize_properties(value)
        else:
            out[key] = value
    return out


_installed = False


def install() -> None:
    """Sanitize every database write, once per process."""
    global _installed
    if _installed:
        return

    # Patch FalkorDB
    try:
        from graphiti_core.driver.falkordb_driver import FalkorDriverSession
        original_falkor = FalkorDriverSession.run

        async def sanitized_falkor_run(self: Any, query: Any, **params: Any) -> Any:
            try:
                if isinstance(query, list):
                    query = [
                        (cypher, sanitize_params(inner) if isinstance(inner, dict) else inner)
                        for cypher, inner in query
                    ]
                params = sanitize_params(params)
            except Exception:
                logger.warning("property sanitization failed; passing through", exc_info=True)
            return await original_falkor(self, query, **params)

        FalkorDriverSession.run = sanitized_falkor_run
    except ImportError:
        pass

    # Patch Neo4j
    try:
        from graphiti_core.driver.neo4j_driver import Neo4jDriver
        original_neo4j = Neo4jDriver.execute_query

        async def sanitized_neo4j_run(self: Any, cypher_query_: str, **kwargs: Any) -> Any:
            try:
                params = kwargs.get('params', {})
                if params:
                    kwargs['params'] = sanitize_params(params)
            except Exception:
                logger.warning("neo4j property sanitization failed; passing through", exc_info=True)
            return await original_neo4j(self, cypher_query_, **kwargs)

        Neo4jDriver.execute_query = sanitized_neo4j_run
    except ImportError:
        pass

    _installed = True
