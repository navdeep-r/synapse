import pytest
from kg_serializer.canonical import serialize_node
from kg_serializer.models import AttentionType, AttentionPriority

def test_1_generic_entity():
    raw = {
        "uuid": "node-1",
        "name": "Generic",
        "labels": ["Organization"],
        "summary": "A generic node",
        "attributes": {}
    }
    cnode = serialize_node(raw)
    assert cnode.id == "node-1"
    assert cnode.identity.name == "Generic"
    assert cnode.identity.entity_type == "Organization"
    assert cnode.semantic.summary == "A generic node"
    assert cnode.attention.required is False
    
def test_2_github_repository():
    raw = {
        "uuid": "repo-1",
        "name": "synapse",
        "labels": ["GitRepository"],
        "source_type": "github",
        "attributes": {
            "owner": "example",
            "repository": "synapse",
            "type": "repository"
        }
    }
    cnode = serialize_node(raw)
    assert cnode.source.source_type == "github"
    assert cnode.classification.type == "repository"
    assert cnode.source_metadata.github == {"owner": "example", "repository": "synapse"}
    assert cnode.source_metadata.mail is None
    
def test_3_github_backend_file():
    raw = {
        "uuid": "file-1",
        "name": "backend/app/main.py",
        "labels": ["GitFile"],
        "source_type": "github",
        "attributes": {
            "type": "file",
            "classification_subtype": "backend",
            "architectural_role": "backend",
            "language": "python",
            "file_type": "source"
        }
    }
    cnode = serialize_node(raw)
    assert cnode.classification.type == "file"
    assert cnode.classification.subtype == "backend"
    assert cnode.source_metadata.github["architectural_role"] == "backend"

def test_4_github_commit():
    raw = {
        "uuid": "commit-123",
        "name": "feat: auth",
        "labels": ["GitCommit"],
        "source_type": "github",
        "attributes": {
            "external_id": "abc123sha",
            "commit_sha": "abc123sha"
        }
    }
    cnode = serialize_node(raw)
    assert cnode.source.external_id == "abc123sha"
    assert cnode.source_metadata.github["commit_sha"] == "abc123sha"

def test_5_mail_unique_node():
    raw = {
        "uuid": "mail-1",
        "name": "Invoice",
        "labels": ["Email"],
        "source_type": "mail",
        "attributes": {
            "external_id": "msg-id-456",
            "message_id": "msg-id-456"
        }
    }
    cnode = serialize_node(raw)
    assert cnode.source.external_id == "msg-id-456"
    assert cnode.source_metadata.mail["message_id"] == "msg-id-456"

def test_6_customer_email_attention():
    raw = {
        "uuid": "mail-2",
        "name": "Help me",
        "labels": ["Email"],
        "source_type": "mail",
        "attributes": {
            "need_attention": True,
            "attention_type": "action_required",
            "customer_related": True
        }
    }
    cnode = serialize_node(raw)
    assert cnode.attention.required is True
    assert cnode.attention.type == AttentionType.ACTION_REQUIRED

def test_7_advertisement_email():
    raw = {
        "uuid": "mail-3",
        "name": "Sale",
        "labels": ["Email"],
        "source_type": "mail",
        "attributes": {
            "need_attention": False
        }
    }
    cnode = serialize_node(raw)
    assert cnode.attention.required is False
    assert cnode.attention.type == AttentionType.NONE

def test_8_uploaded_document():
    raw = {
        "uuid": "doc-1",
        "name": "Policy",
        "labels": ["Document"],
        "source_type": "upload",
        "attributes": {
            "document_id": "doc-id-1",
            "filename": "policy.pdf"
        }
    }
    cnode = serialize_node(raw)
    assert cnode.source_metadata.document["filename"] == "policy.pdf"

def test_9_no_source_contamination():
    raw = {
        "uuid": "mail-1",
        "name": "Invoice",
        "labels": ["Email"],
        "source_type": "mail",
        "attributes": {
            "message_id": "msg-id",
            "owner": "example" # Simulate contamination in db
        }
    }
    cnode = serialize_node(raw)
    assert cnode.source_metadata.mail is not None
    assert cnode.source_metadata.github is None

def test_10_no_unnecessary_llm_calls():
    # Verify that the serialization process relies purely on dictionary mapping
    # without invoking any LLMs, async generation or external clients.
    import inspect
    import kg_serializer.canonical
    source = inspect.getsource(kg_serializer.canonical.serialize_node)
    assert "await" not in source, "Serialization should be synchronous"
    assert "llm" not in source.lower(), "Serialization must not invoke LLMs"
    assert "generate" not in source.lower(), "Serialization must not invoke LLMs"

def test_12_agent_runtime_payload():
    raw = {
        "uuid": "pr-1",
        "name": "PR 1",
        "labels": ["PullRequest"],
        "attributes": {
            "need_attention": True,
            "attention_type": "review_required",
            "attention_priority": "high",
            "attention_reason": "Needs review"
        }
    }
    cnode = serialize_node(raw)
    payload = cnode.model_dump(mode="json")
    assert payload["attention"]["required"] is True
    assert payload["attention"]["type"] == "review_required"
    assert payload["attention"]["priority"] == "high"
