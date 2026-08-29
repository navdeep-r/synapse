from enum import Enum
from typing import Any, List, Optional
from pydantic import BaseModel, Field

class AttentionPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class AttentionType(str, Enum):
    ACTION_REQUIRED = "action_required"
    REVIEW_REQUIRED = "review_required"
    ERROR = "error"
    UPDATE = "update"
    NONE = "none"

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

class GitHubSourceMetadata(BaseModel):
    architectural_role: Optional[str] = None
    language: Optional[str] = None
    file_type: Optional[str] = None
    owner: Optional[str] = None
    repository: Optional[str] = None
    branch: Optional[str] = None
    path: Optional[str] = None
    commit_sha: Optional[str] = None

class MailSourceMetadata(BaseModel):
    message_id: Optional[str] = None
    thread_id: Optional[str] = None
    email_address: Optional[str] = None
    provider: Optional[str] = None
    account_id: Optional[str] = None

class DocumentSourceMetadata(BaseModel):
    document_id: Optional[str] = None

class SourceMetadata(BaseModel):
    github: Optional[GitHubSourceMetadata] = None
    mail: Optional[MailSourceMetadata] = None
    document: Optional[DocumentSourceMetadata] = None

class NodeProvenance(BaseModel):
    source_ids: List[str] = Field(default_factory=list)
    episode_ids: List[str] = Field(default_factory=list)
    document_ids: List[str] = Field(default_factory=list)

class CanonicalNode(BaseModel):
    id: str
    identity: NodeIdentity
    source: NodeSource
    classification: NodeClassification
    semantic: NodeSemantic
    attention: NodeAttention
    source_metadata: SourceMetadata
    provenance: NodeProvenance
    
    # Backward compatibility properties
    @property
    def val(self) -> int:
        return 1
        
    @property
    def name(self) -> str:
        return self.identity.name
        
    @property
    def type(self) -> str:
        return self.classification.type or self.identity.entity_type
        
    @property
    def need_attention(self) -> bool:
        return self.attention.required
