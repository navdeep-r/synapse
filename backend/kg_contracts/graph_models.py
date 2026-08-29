from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from enum import Enum

class AttentionType(str, Enum):
    NONE = "none"
    ACTION_REQUIRED = "action_required"
    REVIEW_REQUIRED = "review_required"
    RESPONSE_REQUIRED = "response_required"
    APPROVAL_REQUIRED = "approval_required"
    FOLLOW_UP_REQUIRED = "follow_up_required"
    DEADLINE_APPROACHING = "deadline_approaching"
    CONFLICT_DETECTED = "conflict_detected"
    FAILURE_DETECTED = "failure_detected"
    ANOMALY_DETECTED = "anomaly_detected"

class AttentionPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class NodeIdentity(BaseModel):
    name: str
    entity_type: str
    subtype: Optional[str] = None

class NodeSource(BaseModel):
    source_type: str
    source_id: str
    external_id: Optional[str] = None
    external_url: Optional[str] = None

class NodeClassification(BaseModel):
    domain: Optional[str] = None
    category: Optional[str] = None
    type: Optional[str] = None
    subtype: Optional[str] = None
    tags: List[str] = Field(default_factory=list)

class NodeSemantic(BaseModel):
    summary: Optional[str] = None
    description: Optional[str] = None

class NodeAttention(BaseModel):
    required: bool = False
    type: AttentionType = AttentionType.NONE
    priority: Optional[AttentionPriority] = None
    reason: Optional[str] = None
    recommended_agent: Optional[str] = None
    recommended_action: Optional[str] = None
    requires_human_approval: bool = False

class NodeProvenance(BaseModel):
    source_ids: List[str] = Field(default_factory=list)
    episode_ids: List[str] = Field(default_factory=list)
    document_ids: List[str] = Field(default_factory=list)

class SourceMetadata(BaseModel):
    github: Optional[Dict[str, Any]] = None
    mail: Optional[Dict[str, Any]] = None
    document: Optional[Dict[str, Any]] = None

class CanonicalNode(BaseModel):
    id: str
    identity: NodeIdentity
    source: NodeSource
    classification: NodeClassification
    semantic: NodeSemantic
    attention: NodeAttention
    source_metadata: SourceMetadata
    provenance: NodeProvenance

    # Compatibility aliases to allow slow frontend migration
    @property
    def name(self) -> str:
        return self.identity.name

    @property
    def type(self) -> str:
        return self.classification.type or self.identity.entity_type

    @property
    def group_id(self) -> str:
        return "synapse"

    @property
    def source_id(self) -> str:
        return self.source.source_id

    @property
    def source_type(self) -> str:
        return self.source.source_type

    @property
    def need_attention(self) -> bool:
        return self.attention.required

    @property
    def summary(self) -> str:
        return self.semantic.summary or ""

