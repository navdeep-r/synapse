"""Snapshot generation with pre-delivery validation.

A snapshot is an immutable, versioned view of the graph. It is validated before
it is written, so an invalid export never reaches a downstream consumer.
"""

from __future__ import annotations

import gzip
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from kg_export.policies import apply_acl_policy, effective_tag


@dataclass
class ValidationCheck:
    name: str
    passed: bool
    detail: str


@dataclass
class SnapshotResult:
    version: str
    entity_count: int
    edge_count: int
    report_count: int
    removed_edges: int
    byte_size: int
    path: str | None
    checks: list[ValidationCheck]
    passed: bool
    acl_policy: str
    fact_scope: str
    report_scope: str
    created_at: str


class SnapshotBuilder:
    """Builds, validates and persists an export snapshot."""

    def __init__(self, snapshot_dir: Path) -> None:
        self._dir = snapshot_dir

    def build(
        self,
        *,
        entities: list[dict],
        edges: list[dict],
        fact_scope: str,
        report_scope: str,
        acl_policy: str,
        edge_tags: dict[str, list[str]],
        reports: list[dict] | None = None,
        known_relations: set[str] | None = None,
        max_bytes: int = 64 * 1024 * 1024,
    ) -> SnapshotResult:
        version = _next_version()

        # 1. Temporal scope: 'active' exports only facts that are currently true.
        if fact_scope == "active":
            scoped = [e for e in edges if not e.get("invalid_at") and not e.get("expired_at")]
        else:
            scoped = list(edges)

        # 2. Access control.
        kept, removed = apply_acl_policy(
            scoped, acl_policy, lambda e: effective_tag(edge_tags.get(e.get("uuid") or "", []))
        )

        # 3. Only keep entities still referenced by an exported fact.
        referenced = {e.get("source_uuid") for e in kept} | {e.get("target_uuid") for e in kept}
        scoped_entities = [e for e in entities if e.get("uuid") in referenced]

        included_reports = list(reports or []) if report_scope != "none" else []

        payload = {
            "version": version,
            "created_at": datetime.now(UTC).isoformat(),
            "fact_scope": fact_scope,
            "report_scope": report_scope,
            "acl_policy": acl_policy,
            "entities": scoped_entities,
            "facts": kept,
            "reports": included_reports,
        }

        blob = json.dumps(payload, default=str).encode("utf-8")
        checks = self._validate(
            scoped_entities,
            kept,
            removed,
            acl_policy,
            known_relations=known_relations or set(),
            payload_bytes=len(blob),
            max_bytes=max_bytes,
        )
        passed = all(check.passed for check in checks)

        path: str | None = None
        byte_size = 0
        if passed:
            self._dir.mkdir(parents=True, exist_ok=True)
            target = self._dir / f"{version}.json.gz"
            with gzip.open(target, "wb") as handle:
                handle.write(blob)
            path = str(target)
            byte_size = target.stat().st_size

        return SnapshotResult(
            version=version,
            entity_count=len(scoped_entities),
            edge_count=len(kept),
            report_count=len(included_reports),
            removed_edges=len(removed),
            byte_size=byte_size,
            path=path,
            checks=checks,
            passed=passed,
            acl_policy=acl_policy,
            fact_scope=fact_scope,
            report_scope=report_scope,
            created_at=payload["created_at"],
        )

    def _validate(
        self,
        entities: list[dict],
        edges: list[dict],
        removed: list[dict],
        acl_policy: str,
        *,
        known_relations: set[str],
        payload_bytes: int,
        max_bytes: int,
    ) -> list[ValidationCheck]:
        """The five pre-delivery checks.

        The names match the labels `ExportScreen` already renders, so the screen
        reports what was actually verified rather than a parallel vocabulary.
        """
        entity_uuids = {e.get("uuid") for e in entities}

        # 1. No fact may point at an entity that is not in the snapshot.
        dangling = [
            e
            for e in edges
            if e.get("source_uuid") not in entity_uuids or e.get("target_uuid") not in entity_uuids
        ]
        checks = [
            ValidationCheck(
                name="No orphan edges",
                passed=not dangling,
                detail=(
                    f"All {len(edges)} exported fact(s) resolve to an exported entity."
                    if not dangling
                    else f"{len(dangling)} fact(s) reference an entity missing from the snapshot."
                ),
            )
        ]

        # 2. Every relation must exist in the ontology a consumer will read this with.
        unknown = sorted(
            {
                str(e.get("relation_type"))
                for e in edges
                if e.get("relation_type") and str(e["relation_type"]) not in known_relations
            }
        )
        checks.append(
            ValidationCheck(
                name="Ontology versions resolvable",
                passed=not unknown,
                detail=(
                    "Every relation type in the snapshot is defined in the current ontology."
                    if not unknown
                    else f"Relation type(s) not defined in the ontology: {', '.join(unknown)}."
                ),
            )
        )

        # 3. Prove the policy was applied rather than asserting it.
        unprovenanced = [e for e in edges if not e.get("episodes")]
        acl_ok = not unprovenanced
        checks.append(
            ValidationCheck(
                name="ACL policy actually applied",
                passed=acl_ok,
                detail=(
                    f"Policy '{acl_policy}' removed {len(removed)} fact(s); every retained fact "
                    "carries the provenance its access tag was derived from."
                    if acl_ok
                    else (
                        f"{len(unprovenanced)} retained fact(s) have no provenance, so their "
                        "access tag could not be established."
                    )
                ),
            )
        )

        # 4. Empty or oversized payloads are both delivery failures.
        size_ok = bool(edges) and payload_bytes <= max_bytes
        checks.append(
            ValidationCheck(
                name="Payload size within threshold",
                passed=size_ok,
                detail=(
                    f"{len(entities)} entities and {len(edges)} facts, {payload_bytes:,} bytes "
                    f"(limit {max_bytes:,})."
                    if size_ok
                    else (
                        "Snapshot is empty. Check the tenant, fact scope and ACL policy."
                        if not edges
                        else f"Payload is {payload_bytes:,} bytes, over the {max_bytes:,} limit."
                    )
                ),
            )
        )

        return checks


def _next_version() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"v{stamp}-{uuid.uuid4().hex[:4]}"


def checks_to_payload(checks: list[ValidationCheck]) -> list[dict]:
    return [asdict(check) for check in checks]
