from typing import Any, Dict, Optional
from kg_serializer.models import (
    CanonicalNode, NodeIdentity, NodeSource, NodeClassification,
    NodeSemantic, NodeAttention, NodeProvenance, SourceMetadata,
    AttentionType, AttentionPriority
)
from app.services import ProvenanceIndex

def _get_node_type(entity: dict) -> str:
    labels = entity.get("labels") or []
    if isinstance(labels, str):
        labels = [labels]
    for label in labels:
        if label and label != "Entity":
            return str(label)
    return "Entity"

def _derive_classification(entity_type: str, entity: dict = None) -> dict:
    # Basic ontology mapping
    mapping = {
        "GitFile": {"domain": "engineering", "category": "code", "type": "file"},
        "GitRepository": {"domain": "engineering", "category": "code", "type": "repository"},
        "GitPullRequest": {"domain": "engineering", "category": "collaboration", "type": "pull_request"},
        "GitCommit": {"domain": "engineering", "category": "code", "type": "commit"},
        "GitMerge": {"domain": "engineering", "category": "code", "type": "commit"},
        "GitIssue": {"domain": "engineering", "category": "collaboration", "type": "issue"},
        "Person": {"domain": "hr", "category": "person", "type": "person"},
        "Developer": {"domain": "engineering", "category": "person", "type": "person", "subtype": "developer"},
        "Project": {"domain": "engineering", "category": "project", "type": "project"},
        "Policy": {"domain": "compliance", "category": "document", "type": "policy"},
        "Email": {"domain": "operations", "category": "communication", "type": "email"}
    }
    result = mapping.get(entity_type, {})
    if not result and entity_type == "Entity" and entity:
        # Fallback for generic document entities
        name = (entity.get("name") or entity.get("attributes", {}).get("name", "")).lower()
        if "policy" in name or "guideline" in name:
            result = {"domain": "compliance", "category": "document", "type": "policy"}
        elif "report" in name:
            result = {"domain": "operations", "category": "document", "type": "report"}
        elif "manual" in name or "handbook" in name:
            result = {"domain": "hr", "category": "document", "type": "manual"}
        else:
            result = {"domain": "knowledge", "category": "document", "type": "document"}
    return result

def serialize_node(entity: dict, index: Optional[ProvenanceIndex] = None) -> CanonicalNode:
    """Map a raw Graphiti/SQLite entity dict to the CanonicalNode contract."""
    raw_attrs = entity.get("attributes", {})
    # Create a unified property accessor that checks entity first, then attributes
    def get_val(key, default=None):
        v = entity.get(key)
        if v is not None: return v
        return raw_attrs.get(key, default)

    entity_type = _get_node_type(entity)
    classification_derived = _derive_classification(entity_type, entity)
    
    # 1. Identity
    identity = NodeIdentity(
        name=get_val("name", "Unnamed"),
        entity_type=entity_type,
        subtype=get_val("subtype") or classification_derived.get("subtype")
    )
    
    # 2. Source
    source_type = get_val("source_type", "unknown")
    source = NodeSource(
        source_type=source_type,
        source_id=get_val("source_id", "unknown"),
        external_id=get_val("external_id"),
        external_url=get_val("external_url")
    )
    
    # 3. Classification
    classification = NodeClassification(
        domain=get_val("domain") or classification_derived.get("domain"),
        category=get_val("category") or classification_derived.get("category"),
        type=get_val("type") or classification_derived.get("type"),
        subtype=get_val("classification.subtype") or get_val("classification_subtype") or classification_derived.get("subtype"),
        tags=get_val("tags", [])
    )
    
    # 4. Semantic
    semantic = NodeSemantic(
        summary=get_val("summary"),
        description=get_val("description")
    )
    
    # 5. Attention
    attention_obj = get_val("attention")
    if isinstance(attention_obj, dict):
        # Already structured
        attention = NodeAttention(**attention_obj)
    else:
        # Fallback from boolean/flat attributes
        req = bool(get_val("need_attention"))
        
        att_type_val = get_val("attention_type")
        try:
            att_type = AttentionType(att_type_val) if att_type_val else AttentionType.NONE
        except ValueError:
            att_type = AttentionType.NONE
            
        if req and att_type == AttentionType.NONE:
            att_type = AttentionType.ACTION_REQUIRED



        try:
            att_prio_val = get_val("attention_priority")
            att_prio = AttentionPriority(att_prio_val) if att_prio_val else None
        except ValueError:
            att_prio = None
            
        if req and not att_prio:
            att_prio = AttentionPriority.MEDIUM

        reason = get_val("attention_reason")
        if req and not reason:
            reason = "Flagged during ingestion or dependency analysis."

        rec_agent = get_val("recommended_agent")
        if req and not rec_agent:
            rec_agent = "Developer"

        attention = NodeAttention(
            required=req,
            type=att_type,
            priority=att_prio,
            reason=reason,
            recommended_agent=rec_agent,
            recommended_action=get_val("recommended_action"),
            requires_human_approval=bool(get_val("requires_human_approval"))
        )
    
    # 6. Source Metadata
    source_metadata = SourceMetadata()
    
    if source_type == "github":
        github_fields = ["owner", "repository", "branch", "path", "language", "file_type", 
                         "architectural_role", "commit_sha", "commit_message", "author", 
                         "pr_number", "target_branch", "symbols", "imports"]
        
        # Handle legacy nested github_context
        gh_context = get_val("github_context")
        if isinstance(gh_context, str):
            import json
            try: gh_context = json.loads(gh_context)
            except: gh_context = {}
        elif not isinstance(gh_context, dict):
            gh_context = {}
            
        def get_gh_val(k):
            v = get_val(k)
            if v is not None: return v
            # legacy resolution
            if "repository" in gh_context and isinstance(gh_context["repository"], dict):
                v = gh_context["repository"].get(k)
                if v is not None: return v
            return gh_context.get(k)
            
        gh_data = {k: get_gh_val(k) for k in github_fields if get_gh_val(k) is not None}
        if gh_data:
            source_metadata.github = gh_data
            
    elif source_type == "mail":
        mail_fields = ["message_id", "thread_id", "from", "to", "cc", "subject", 
                       "sent_at", "received_at", "is_reply", "has_attachments", 
                       "attachment_count", "customer_related", "requires_response",
                       "provider", "account_id", "email_address"]
        mail_data = {k: get_val(k) for k in mail_fields if get_val(k) is not None}
        if mail_data:
            source_metadata.mail = mail_data
            
    elif source_type == "upload":
        doc_fields = ["document_id", "filename", "document_type", "department", 
                      "version", "effective_from", "language", "file_size", "mime_type", "uploader", "upload_time"]
        doc_data = {k: get_val(k) for k in doc_fields if get_val(k) is not None}
        if doc_data:
            source_metadata.document = doc_data
            
    # 7. Provenance
    import json
    def _parse_ids(val):
        if not val: return []
        if isinstance(val, str):
            try: return json.loads(val)
            except: return [val]
        return list(val)

    source_ids = _parse_ids(get_val("source_ids"))
    episode_ids = _parse_ids(get_val("episode_ids"))
    document_ids = _parse_ids(get_val("document_ids"))
    
    if index:
        episodes = get_val("episodes", [])
        if isinstance(episodes, str):
            episodes = [episodes]
        else:
            episodes = [str(e) for e in episodes]
            
        if episodes:
            derived_chunks = index.chunks_for(episodes)
            derived_docs = index.documents_for(episodes)
            source_ids.extend(derived_chunks)
            document_ids.extend(derived_docs)
            episode_ids.extend(episodes)
            
    provenance = NodeProvenance(
        source_ids=list(set(source_ids)),
        episode_ids=list(set(episode_ids)),
        document_ids=list(set(document_ids))
    )
    
    return CanonicalNode(
        id=entity["uuid"],
        identity=identity,
        source=source,
        classification=classification,
        semantic=semantic,
        attention=attention,
        source_metadata=source_metadata,
        provenance=provenance
    )
