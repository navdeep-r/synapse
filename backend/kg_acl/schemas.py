from typing import Any, Literal, Union, Optional
from pydantic import BaseModel, Field
from datetime import datetime

class EntityListBoundary(BaseModel):
    kind: Literal["entity_list"] = "entity_list"
    entity_uuids: list[str] = Field(default_factory=list)
    excluded_entity_uuids: list[str] = Field(default_factory=list)

class SourceSetBoundary(BaseModel):
    kind: Literal["source_set"] = "source_set"
    source_ids: list[str] = Field(default_factory=list)
    excluded_source_ids: list[str] = Field(default_factory=list)

class RepositorySetBoundary(BaseModel):
    kind: Literal["repository_set"] = "repository_set"
    github_repo_ids: list[str] = Field(default_factory=list)
    excluded_repo_ids: list[str] = Field(default_factory=list)

class ProjectRootBoundary(BaseModel):
    kind: Literal["project_root"] = "project_root"
    root_entity_uuids: list[str] = Field(default_factory=list)
    allowed_entity_types: list[str] = Field(default_factory=list)
    allowed_relation_types: list[str] = Field(default_factory=list)
    max_depth: int = 2

class OntologySubtreeBoundary(BaseModel):
    kind: Literal["ontology_subtree"] = "ontology_subtree"
    root_entity_type: str
    allowed_relation_types: list[str] = Field(default_factory=list)
    max_depth: int = 3

class DiscoverySnapshotBoundary(BaseModel):
    kind: Literal["discovery_snapshot"] = "discovery_snapshot"
    community_ids: list[str] = Field(default_factory=list)
    snapshot_version: str

class AllOrgBoundary(BaseModel):
    kind: Literal["all_org"] = "all_org"

BoundarySpec = Union[
    EntityListBoundary,
    SourceSetBoundary,
    RepositorySetBoundary,
    ProjectRootBoundary,
    OntologySubtreeBoundary,
    DiscoverySnapshotBoundary,
    AllOrgBoundary
]

class ScopeCreate(BaseModel):
    name: str
    description: str
    sensitivity: str = "internal"

class ScopePatch(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    sensitivity: Optional[str] = None
    owner_user_id: Optional[str] = None

class MaterializeRequest(BaseModel):
    boundary_kind: str
    boundary_spec: dict[str, Any]

class ScopeResponse(BaseModel):
    id: str
    name: str
    description: str
    owner_user_id: Optional[str]
    status: str
    sensitivity: str
    active_version_id: Optional[str]
    review_status: str
    discovery_origin: Optional[str]
    created_at: datetime
    updated_at: datetime
    deprecated_at: Optional[datetime]

class ScopeVersionResponse(BaseModel):
    id: str
    scope_id: str
    version_number: int
    status: str
    boundary_kind: str
    boundary_spec_json: dict[str, Any]
    member_entity_count: int
    member_edge_count: int
    proposed_by: Optional[str]
    approved_by: Optional[str]
    created_at: datetime
    materialized_at: datetime
    approved_at: Optional[datetime]
    rejected_at: Optional[datetime]
    change_summary: str
    entity_drift_pct: float
    edge_drift_pct: float
    previous_version_id: Optional[str]

class VersionDiffResponse(BaseModel):
    entity_added: list[str]
    entity_removed: list[str]
    edge_added: list[str]
    edge_removed: list[str]
    entity_drift_pct: float
    edge_drift_pct: float
    change_summary: str

# ---------------------------------------------------------------------------
# Phase 3 Schemas
# ---------------------------------------------------------------------------

class RoleCreate(BaseModel):
    name: str
    description: str = ""
    clearance: str = "internal"

class RoleResponse(BaseModel):
    id: str
    name: str
    description: str
    clearance: str
    is_system_role: bool
    created_at: datetime
    updated_at: datetime

class TeamCreate(BaseModel):
    name: str
    description: str = ""

class TeamResponse(BaseModel):
    id: str
    name: str
    description: str
    created_at: datetime
    updated_at: datetime

class TeamMemberAdd(BaseModel):
    user_id: str

class ScopeAssignmentCreate(BaseModel):
    assignee_kind: Literal["role", "team", "user"]
    assignee_id: str
    reason: str = ""
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None

class BulkAssignmentRequest(BaseModel):
    assignments: list[ScopeAssignmentCreate]

class ScopeAssignmentResponse(BaseModel):
    id: str
    scope_id: str
    assignee_kind: str
    assignee_id: str
    granted_by: Optional[str]
    valid_from: datetime
    valid_to: Optional[datetime]
    revoked_at: Optional[datetime]
    created_at: datetime
    reason: str

