"""Schema registry and compatibility enforcement.

A connector that changes the event shape must fail *here*, loudly, at registration
time — not downstream where a missing field turns into a null that extraction
quietly treats as absent data.

Two implementations behind one interface, matching the pattern used elsewhere in
this codebase (stub vs real LLM, embedded vs server graph):

- `LocalSchemaRegistry` — file-backed, no broker or network. Runs the same
  compatibility gate so the rules are exercised by tests and by local dev.
- `ConfluentSchemaRegistry` — the real thing, for deployment.

SCOPE OF THE LOCAL CHECKER
--------------------------
It implements the Avro resolution rules that this contract can actually violate:
field addition without a default, field removal, non-promotable type changes,
enum symbol removal, and union branch removal. It is deliberately *stricter* than
full Avro in ambiguous cases — a false rejection is a conversation, a false
acceptance is corrupted extraction. Confluent's server implements the complete
specification; in production it is the authority.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

# Avro's numeric/string promotions: a reader of the target type can read data
# written as the source type. Anything not listed here is a breaking change.
_PROMOTIONS: dict[str, set[str]] = {
    "int": {"long", "float", "double"},
    "long": {"float", "double"},
    "float": {"double"},
    "string": {"bytes"},
    "bytes": {"string"},
}


class Compatibility(str, Enum):
    """Registry compatibility mode for a subject."""

    NONE = "NONE"
    BACKWARD = "BACKWARD"
    FORWARD = "FORWARD"
    FULL = "FULL"


class IncompatibleSchemaError(RuntimeError):
    """Raised when a schema change violates the subject's compatibility mode."""

    def __init__(self, subject: str, violations: list[str]) -> None:
        self.subject = subject
        self.violations = violations
        detail = "\n  - ".join(violations)
        super().__init__(f"schema for subject '{subject}' is incompatible:\n  - {detail}")


@dataclass
class RegisteredSchema:
    schema_id: int
    subject: str
    version: int
    schema: dict[str, Any] = field(repr=False)


# --- compatibility checking ------------------------------------------------


def _type_name(node: Any) -> str:
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        return str(node.get("type", ""))
    if isinstance(node, list):
        return "union"
    return ""


def _fields_by_name(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f["name"]: f for f in record.get("fields", [])}


def _has_default(field_def: dict[str, Any]) -> bool:
    return "default" in field_def


def _compare_types(path: str, old: Any, new: Any, out: list[str]) -> None:
    old_name, new_name = _type_name(old), _type_name(new)

    if old_name == "union" or new_name == "union":
        if old_name != new_name:
            out.append(f"{path}: union changed to non-union (or vice versa)")
            return
        old_branches = {_type_name(b) for b in old}
        new_branches = {_type_name(b) for b in new}
        removed = old_branches - new_branches
        if removed:
            out.append(
                f"{path}: union branch(es) removed ({', '.join(sorted(removed))}) — "
                "a reader without the branch cannot decode data that used it"
            )
        return

    if old_name != new_name:
        if new_name not in _PROMOTIONS.get(old_name, set()):
            out.append(f"{path}: type changed {old_name} -> {new_name} and is not promotable")
        return

    if old_name == "enum" and isinstance(old, dict) and isinstance(new, dict):
        removed = set(old.get("symbols", [])) - set(new.get("symbols", []))
        if removed and "default" not in new:
            out.append(
                f"{path}: enum symbol(s) removed ({', '.join(sorted(removed))}) "
                "without a default symbol to absorb them"
            )

    if old_name == "record" and isinstance(old, dict) and isinstance(new, dict):
        _compare_records(path, old, new, out)

    if old_name == "array" and isinstance(old, dict) and isinstance(new, dict):
        _compare_types(f"{path}[]", old.get("items"), new.get("items"), out)

    if old_name == "map" and isinstance(old, dict) and isinstance(new, dict):
        _compare_types(f"{path}{{}}", old.get("values"), new.get("values"), out)


def _compare_records(path: str, old: dict[str, Any], new: dict[str, Any], out: list[str]) -> None:
    old_fields, new_fields = _fields_by_name(old), _fields_by_name(new)

    for name in new_fields.keys() - old_fields.keys():
        if not _has_default(new_fields[name]):
            out.append(
                f"{path}.{name}: field added without a default — a reader cannot "
                "materialise it from data written before it existed"
            )

    for name in old_fields.keys() - new_fields.keys():
        out.append(f"{path}.{name}: field removed")

    for name in old_fields.keys() & new_fields.keys():
        _compare_types(f"{path}.{name}", old_fields[name]["type"], new_fields[name]["type"], out)


def check_compatibility(
    old: dict[str, Any], new: dict[str, Any], mode: Compatibility
) -> list[str]:
    """Return the list of violations; empty means compatible.

    BACKWARD means a new *reader* can read old data, so the questions are asked in
    the direction old -> new. FORWARD is the same machinery with the arguments
    swapped: an old reader must cope with new data. FULL demands both.
    """
    if mode is Compatibility.NONE:
        return []

    violations: list[str] = []
    if mode in (Compatibility.BACKWARD, Compatibility.FULL):
        found: list[str] = []
        _compare_records("", old, new, found)
        violations += [f"BACKWARD {v}" for v in found]
    if mode in (Compatibility.FORWARD, Compatibility.FULL):
        found = []
        _compare_records("", new, old, found)
        violations += [f"FORWARD {v}" for v in found]
    return violations


# --- registries ------------------------------------------------------------


class SchemaRegistry(Protocol):
    def register(self, subject: str, schema: dict[str, Any]) -> RegisteredSchema: ...
    def by_id(self, schema_id: int) -> RegisteredSchema: ...
    def latest(self, subject: str) -> RegisteredSchema | None: ...


class LocalSchemaRegistry:
    """File-backed registry. Same gate as production, no network."""

    def __init__(
        self, path: Path, *, compatibility: Compatibility = Compatibility.BACKWARD
    ) -> None:
        self._path = path
        self._compatibility = compatibility
        self._state: dict[str, Any] = {"next_id": 1, "subjects": {}, "by_id": {}}
        if path.exists():
            self._state = json.loads(path.read_text("utf-8"))

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._state, indent=2), "utf-8")

    def register(self, subject: str, schema: dict[str, Any]) -> RegisteredSchema:
        versions = self._state["subjects"].setdefault(subject, [])

        if versions:
            current = self._state["by_id"][str(versions[-1]["schema_id"])]
            if current == schema:
                last = versions[-1]
                return RegisteredSchema(last["schema_id"], subject, last["version"], schema)

            violations = check_compatibility(current, schema, self._compatibility)
            if violations:
                raise IncompatibleSchemaError(subject, violations)

        schema_id = self._state["next_id"]
        self._state["next_id"] += 1
        version = len(versions) + 1
        versions.append({"schema_id": schema_id, "version": version})
        self._state["by_id"][str(schema_id)] = schema
        self._flush()
        return RegisteredSchema(schema_id, subject, version, schema)

    def by_id(self, schema_id: int) -> RegisteredSchema:
        raw = self._state["by_id"].get(str(schema_id))
        if raw is None:
            raise KeyError(f"unknown schema id {schema_id}")
        for subject, versions in self._state["subjects"].items():
            for entry in versions:
                if entry["schema_id"] == schema_id:
                    return RegisteredSchema(schema_id, subject, entry["version"], raw)
        return RegisteredSchema(schema_id, "", 0, raw)

    def latest(self, subject: str) -> RegisteredSchema | None:
        versions = self._state["subjects"].get(subject) or []
        if not versions:
            return None
        entry = versions[-1]
        return RegisteredSchema(
            entry["schema_id"], subject, entry["version"], self._state["by_id"][str(entry["schema_id"])]
        )


class ConfluentSchemaRegistry:
    """Confluent-compatible HTTP registry. The authority in deployment."""

    def __init__(self, base_url: str, *, timeout: float = 5.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._cache: dict[int, RegisteredSchema] = {}

    def _client(self):
        import httpx

        return httpx.Client(timeout=self._timeout)

    def register(self, subject: str, schema: dict[str, Any]) -> RegisteredSchema:
        with self._client() as http:
            # Ask before telling: POST /versions would register and only then
            # report a problem on some server configurations.
            probe = http.post(
                f"{self._base}/compatibility/subjects/{subject}/versions/latest",
                json={"schema": json.dumps(schema), "schemaType": "AVRO"},
                headers={"Content-Type": "application/vnd.schemaregistry.v1+json"},
            )
            if probe.status_code == 200 and not probe.json().get("is_compatible", True):
                raise IncompatibleSchemaError(subject, ["rejected by Confluent Schema Registry"])

            response = http.post(
                f"{self._base}/subjects/{subject}/versions",
                json={"schema": json.dumps(schema), "schemaType": "AVRO"},
                headers={"Content-Type": "application/vnd.schemaregistry.v1+json"},
            )
            if response.status_code == 409:
                raise IncompatibleSchemaError(subject, [response.text])
            response.raise_for_status()
            schema_id = int(response.json()["id"])
        return RegisteredSchema(schema_id, subject, 0, schema)

    def by_id(self, schema_id: int) -> RegisteredSchema:
        if schema_id in self._cache:
            return self._cache[schema_id]
        with self._client() as http:
            response = http.get(f"{self._base}/schemas/ids/{schema_id}")
            response.raise_for_status()
            schema = json.loads(response.json()["schema"])
        registered = RegisteredSchema(schema_id, "", 0, schema)
        self._cache[schema_id] = registered
        return registered

    def latest(self, subject: str) -> RegisteredSchema | None:
        with self._client() as http:
            response = http.get(f"{self._base}/subjects/{subject}/versions/latest")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            body = response.json()
        return RegisteredSchema(
            int(body["id"]), subject, int(body["version"]), json.loads(body["schema"])
        )
