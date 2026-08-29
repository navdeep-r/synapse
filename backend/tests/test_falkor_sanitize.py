"""Attribute sanitization for FalkorDB.

FalkorDB accepts only primitives and arrays of primitives as property values, while
Graphiti merges whatever the extraction model returned into the property map. A model
that ignores the entity schema therefore aborts the *entire* episode write — losing
entities and relationships that were extracted correctly — over one optional field.

`openai/gpt-oss-20b` does exactly this, answering the flat `Employee(department,
title, manager_name)` schema with a nested object under its own `attributes` key.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from kg_graphiti.falkor_sanitize import sanitize_params, sanitize_properties


def test_nested_attributes_are_flattened_into_real_properties() -> None:
    """The observed gpt-oss-20b shape. Values should stay queryable, not become a blob."""
    clean = sanitize_properties(
        {
            "uuid": "n1",
            "name": "Alice Chen",
            "attributes": {
                "job_title": "Senior Engineer",
                "company": "Acme Corp.",
                "reports_to": "Robert Diaz",
            },
        }
    )

    assert clean["job_title"] == "Senior Engineer"
    assert clean["company"] == "Acme Corp."
    assert "attributes" not in clean, "the dict itself must not survive as a property"


def test_empty_attributes_are_dropped_entirely() -> None:
    """Most nodes come back with `attributes: {}`, which is still a rejected dict."""
    clean = sanitize_properties({"uuid": "n1", "name": "Acme Corp", "attributes": {}})
    assert clean == {"uuid": "n1", "name": "Acme Corp"}


def test_extracted_attributes_never_overwrite_graphiti_fields() -> None:
    """A hallucinated `name` or `uuid` must not clobber the real one."""
    clean = sanitize_properties(
        {
            "uuid": "real-uuid",
            "name": "Alice Chen",
            "attributes": {"name": "alice", "uuid": "hallucinated", "title": "Engineer"},
        }
    )

    assert clean["uuid"] == "real-uuid"
    assert clean["name"] == "Alice Chen"
    assert clean["title"] == "Engineer"
    # The collided values are preserved rather than dropped, for debugging.
    assert json.loads(clean["attributes_json"])["name"] == "alice"


def test_deeply_nested_values_are_preserved_as_json() -> None:
    """Losing them silently would make a bad extraction impossible to diagnose."""
    clean = sanitize_properties(
        {"uuid": "n1", "attributes": {"history": [{"role": "SWE", "year": 2021}]}}
    )

    assert "history" not in clean or isinstance(clean.get("history"), str)
    assert json.loads(clean["attributes_json"])["history"][0]["role"] == "SWE"


def test_embeddings_pass_through_untouched() -> None:
    """Large float arrays are valid and must not be walked or rewritten."""
    vector = [0.1] * 2048
    clean = sanitize_properties({"uuid": "n1", "name_embedding": vector})
    assert clean["name_embedding"] is vector


def test_primitive_arrays_are_left_alone() -> None:
    clean = sanitize_properties({"labels": ["Entity", "Employee"], "episodes": ["e1", "e2"]})
    assert clean["labels"] == ["Entity", "Employee"]
    assert clean["episodes"] == ["e1", "e2"]


def test_datetimes_survive_as_datetimes() -> None:
    """Graphiti's driver converts these downstream; stringifying here breaks temporality."""
    now = datetime.now(UTC)
    clean = sanitize_properties({"created_at": now, "valid_at": None})
    assert clean["created_at"] is now
    assert clean["valid_at"] is None


def test_bulk_node_lists_are_sanitized() -> None:
    """The real call shape: `tx.run(query, nodes=[...])`."""
    params = sanitize_params(
        {
            "nodes": [
                {"uuid": "n1", "attributes": {"title": "Engineer"}},
                {"uuid": "n2", "attributes": {}},
            ]
        }
    )

    assert params["nodes"][0]["title"] == "Engineer"
    assert all("attributes" not in node for node in params["nodes"])


def test_non_dict_params_are_untouched() -> None:
    params = sanitize_params({"group_id": "tenant-a", "limit": 10, "uuids": ["a", "b"]})
    assert params == {"group_id": "tenant-a", "limit": 10, "uuids": ["a", "b"]}
