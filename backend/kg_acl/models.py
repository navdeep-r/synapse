"""Core RBAC data models: Sensitivity, Principal, AccessContext.

These are pure data structures with no I/O. They flow through the request
lifecycle from ``require_principal`` into routers and repositories.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class Sensitivity(enum.IntEnum):
    """Clearance / sensitivity ladder — higher value = more restricted.

    A resource is visible only when ``resource_sensitivity <= principal_clearance``
    **and** the resource belongs to an approved scope the principal holds.
    """

    PUBLIC = 0
    INTERNAL = 1
    CONFIDENTIAL = 2
    RESTRICTED = 3

    @classmethod
    def parse(cls, value: str | int | None) -> "Sensitivity":
        """Case-insensitive parse from string or int, defaulting to INTERNAL."""
        if value is None:
            return cls.INTERNAL
        if isinstance(value, int):
            try:
                return cls(value)
            except ValueError:
                return cls.INTERNAL
        try:
            return cls[value.upper()]
        except KeyError:
            return cls.INTERNAL


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated identity attached to a request.

    Built by ``require_principal`` and carried through the request. Contains
    everything needed to authorize graph reads without additional DB round-trips
    (except for scope-entity resolution, which is cached separately).
    """

    user_id: str
    email: str
    display_name: str
    role_ids: list[str] = field(default_factory=list)
    clearance: Sensitivity = Sensitivity.INTERNAL
    is_org_supervisor: bool = False
    active_scope_ids: list[str] = field(default_factory=list)
    active_scope_version_ids: list[str] = field(default_factory=list)
    session_id: str | None = None
    token_version: int = 1

    def to_access_context(self) -> "AccessContext":
        return AccessContext(
            user_id=self.user_id,
            is_org_supervisor=self.is_org_supervisor,
            clearance=self.clearance,
            active_scope_ids=list(self.active_scope_ids),
            active_scope_version_ids=list(self.active_scope_version_ids),
            allowed_entity_uuids=None,
            allowed_edge_uuids=None,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serializable representation for ``/auth/me``."""
        return {
            "user_id": self.user_id,
            "email": self.email,
            "display_name": self.display_name,
            "role_ids": self.role_ids,
            "clearance": self.clearance.name.lower(),
            "is_org_supervisor": self.is_org_supervisor,
            "active_scope_ids": self.active_scope_ids,
            "active_scope_version_ids": self.active_scope_version_ids,
            "session_id": self.session_id,
            "token_version": self.token_version,
        }


@dataclass(frozen=True, slots=True)
class AccessContext:
    """Subset of Principal that is passed to data-access layers.

    ``allowed_entity_uuids`` / ``allowed_edge_uuids`` are populated lazily
    by the effective-scope resolver (Phase 3). Until then they are ``None``,
    meaning "not yet resolved" — callers must treat ``None`` as "deny all"
    for non-supervisors.
    """

    user_id: str
    is_org_supervisor: bool = False
    clearance: Sensitivity = Sensitivity.INTERNAL
    active_scope_ids: list[str] = field(default_factory=list)
    active_scope_version_ids: list[str] = field(default_factory=list)
    allowed_entity_uuids: set[str] | None = None
    allowed_edge_uuids: set[str] | None = None
