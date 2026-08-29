"""The enterprise ontology (v2 doc 3.7).

Constraining extraction to a closed vocabulary is the accuracy argument carried
over from v1: a closed schema beats open-domain extraction regardless of which
extractor runs it. These Pydantic models are handed to `add_episode` as
`entity_types`, and the relation vocabulary as `edge_types` + `edge_type_map`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

class GraphitiElement(BaseModel):
    source_id: str | None = Field(default=None, description="Source ID of the entity/edge")
    source_type: str | None = Field(default=None, description="Source type (e.g. github, upload, mail)")
    source_metadata: dict | None = Field(default=None, description="Metadata from the source")
    
    source_classification: dict | None = Field(
        default=None,
        description="Agent-friendly classification for routing/spawning"
    )
    project_context: dict | None = Field(
        default=None,
        description="Project identification for multi-project environments"
    )
    github_context: dict | None = Field(
        default=None,
        description="GitHub-specific rich context"
    )



class Employee(GraphitiElement):
    """A person: employee, intern, candidate, student or volunteer.

    Use for named people only — never for emails, phone numbers, ratings or
    section headers.
    """

    department: str | None = Field(default=None, description="Department or business unit")
    title: str | None = Field(default=None, description="Job title or role (e.g. Backend Engineer Intern)")
    manager_name: str | None = Field(default=None, description="Name of this person's manager")


class Organization(GraphitiElement):
    """A company, school, team, competition organiser or external partner."""

    industry: str | None = Field(default=None, description="Industry or sector")
    location: str | None = Field(default=None, description="Primary location")


class Repository(GraphitiElement):
    """A source-code repository or software component."""

    org: str | None = Field(default=None, description="Owning organization or team")
    language: str | None = Field(default=None, description="Primary programming language")


class Product(GraphitiElement):
    """A product, project, service, platform or technology.

    Includes programming languages, frameworks, databases, cloud services and
    named projects (e.g. React, PostgreSQL, SmartIn, Kafka).
    """

    category: str | None = Field(default=None, description="Product category or tech stack layer")
    owner: str | None = Field(default=None, description="Owning team or person")


class Document(GraphitiElement):
    """A document, policy, runbook, certification or ticket.

    Do not use for resume section headers like EXPERIENCE or PROJECTS.
    """

    doc_type: str | None = Field(default=None, description="Kind of document")
    status: str | None = Field(default=None, description="Lifecycle status")


class GitRepository(GraphitiElement):
    """A GitHub source-code repository."""
    full_name: str | None = Field(default=None, description="Owner and name of the repository")


class GitFile(GraphitiElement):
    """A source code file within a GitHub repository."""
    path: str | None = Field(default=None, description="File path")
    language: str | None = Field(default=None, description="Programming language")
    summary: str | None = Field(default=None, description="AI-generated summary of the file's goal")
    need_attention: bool = Field(default=False, description="Whether this file needs attention due to active bugs or PR failures")


class GitCommit(GraphitiElement):
    """A revision/commit in a Git repository."""
    sha: str | None = Field(default=None, description="Commit SHA")
    message: str | None = Field(default=None, description="Commit message")


class Developer(GraphitiElement):
    """A software developer or GitHub user."""
    login: str | None = Field(default=None, description="GitHub username")
    email: str | None = Field(default=None, description="Email address")


class CodeClass(GraphitiElement):
    """A class defined in source code."""
    name: str | None = Field(default=None, description="Name of the class")
    need_attention: bool = Field(default=False, description="Whether this class needs attention")


class CodeFunction(GraphitiElement):
    """A function defined in source code."""
    name: str | None = Field(default=None, description="Name of the function")
    need_attention: bool = Field(default=False, description="Whether this function needs attention")


class CodeMethod(GraphitiElement):
    """A method defined in a class in source code."""
    name: str | None = Field(default=None, description="Name of the method")
    need_attention: bool = Field(default=False, description="Whether this method needs attention")


class GitPullRequest(GraphitiElement):
    """A pull request in a GitHub repository."""
    number: int | None = Field(default=None, description="Pull Request number")
    title: str | None = Field(default=None, description="Pull Request title")
    state: str | None = Field(default=None, description="PR state (open or closed)")
    html_url: str | None = Field(default=None, description="GitHub URL")
    need_attention: bool = Field(default=False, description="Whether this PR needs attention due to failures")


class GitIssue(GraphitiElement):
    """An issue in a GitHub repository."""
    number: int | None = Field(default=None, description="Issue number")
    title: str | None = Field(default=None, description="Issue title")
    state: str | None = Field(default=None, description="Issue state (open or closed)")
    html_url: str | None = Field(default=None, description="GitHub URL")
    need_attention: bool = Field(default=False, description="Whether this issue needs attention")


class CodeInterface(BaseModel):
    """Abstract interface, protocol, or trait (e.g. TS `interface`, Python
    `Protocol`, Go `interface{}`, Java `interface`). Extract ONLY named,
    declared contracts — do not extract implicit duck-typed shapes."""
    name: str = Field(..., description="Interface/protocol identifier")
    language: str | None = Field(default=None, description="Source language")
    need_attention: bool = Field(default=False, description="Flagged for review (e.g. undocumented, unstable)")


class TypeDefinition(BaseModel):
    """A named type alias, union, enum, or generic type declaration
    (e.g. TS `type X = ...`, Python `TypeAlias`, Rust `type`). Do NOT
    extract inline/anonymous types or primitive usages."""
    name: str = Field(..., description="Type alias identifier")
    kind: Literal["alias", "enum", "union", "generic"] = Field(..., description="Category of type definition")
    need_attention: bool = Field(default=False)


class APIEndpoint(BaseModel):
    """A concrete REST/GraphQL/gRPC endpoint exposed by the codebase.
    Extract only endpoints with an explicit route/method/schema
    (decorators, router.get(), .proto rpc defs). Do NOT extract internal
    helper functions with no external route binding."""
    method: str | None = Field(default=None, description="HTTP verb or RPC method name")
    path: str = Field(..., description="Route path or fully-qualified RPC name")
    protocol: Literal["REST", "GraphQL", "gRPC"] = Field(..., description="API protocol")
    need_attention: bool = Field(default=False)


class DataModel(BaseModel):
    """A persisted or transmitted data shape: ORM model, DB table, or
    event/message schema. Extract only top-level declared schemas, not
    ad-hoc dict/object literals."""
    name: str = Field(..., description="Model/table/schema name")
    model_type: Literal["orm_model", "db_table", "event_schema"] = Field(..., description="Kind of data model")
    need_attention: bool = Field(default=False)


class PackageDependency(BaseModel):
    """An external, third-party package declared in a manifest (package.json,
    pyproject.toml, Cargo.toml, go.mod). Do NOT create a node for every
    transitive sub-dependency in a lockfile — only direct manifest entries."""
    name: str = Field(..., description="Package name as declared in manifest")
    ecosystem: Literal["npm", "pypi", "crates", "go_modules", "other"] = Field(..., description="Package ecosystem")
    version_spec: str | None = Field(default=None, description="Declared version range/spec")
    dependency_type: Literal["runtime", "dev", "peer", "engine"] = Field(..., description="How the dependency is consumed")
    need_attention: bool = Field(default=False, description="e.g. deprecated, unpinned, vulnerable")


class License(BaseModel):
    """A software license identified via SPDX ID or LICENSE file. One node
    per distinct license, reused across all packages/repos that declare it —
    never duplicate per package."""
    spdx_id: str = Field(..., description="SPDX identifier, e.g. MIT, Apache-2.0")
    name: str | None = Field(default=None, description="Human-readable license name")


class SecurityAdvisory(BaseModel):
    """A known vulnerability advisory (CVE/GHSA) tied to a dependency
    version range. Extract only from explicit advisory data, never inferred."""
    advisory_id: str = Field(..., description="CVE or GHSA identifier")
    severity: Literal["low", "moderate", "high", "critical"] = Field(..., description="Advisory severity")
    summary: str | None = Field(default=None, description="Short advisory description")


class CIWorkflow(BaseModel):
    """A CI/CD pipeline definition (e.g. GitHub Actions .yml workflow).
    One node per workflow file, not per run/execution instance."""
    name: str = Field(..., description="Workflow name/file identifier")
    trigger_event: str | None = Field(default=None, description="e.g. push, pull_request, schedule")
    need_attention: bool = Field(default=False)


class CIJob(BaseModel):
    """A job within a CI workflow (a named unit of steps running on one
    runner). Do NOT extract individual shell steps as separate nodes —
    that is the anti-pattern threshold; steps stay as job metadata/summary."""
    name: str = Field(..., description="Job name within the workflow")
    need_attention: bool = Field(default=False)


class TestSuite(BaseModel):
    """A named test file or test suite (e.g. a pytest module, a Jest
    describe block file). Extract at file/suite granularity — do NOT
    create a node per individual `it()`/`test()` assertion."""
    name: str = Field(..., description="Test suite/file name")
    framework: str | None = Field(default=None, description="e.g. pytest, jest, go test")
    need_attention: bool = Field(default=False, description="e.g. flaky, skipped, failing")


class DeploymentTarget(BaseModel):
    """A named runtime deployment environment or target infrastructure
    (a Docker image/service, a Kubernetes cluster/namespace, or a cloud
    service binding). Modeled as one generic entity — distinguished by
    env_type — to avoid entity-class explosion across every infra provider."""
    name: str = Field(..., description="Environment/target name, e.g. 'production-cluster'")
    env_type: Literal["docker", "kubernetes", "cloud_service", "bare_metal", "other"] = Field(..., description="Deployment target category")
    need_attention: bool = Field(default=False)


class GitBranch(BaseModel):
    """A named Git branch (ref). One node per long-lived/active branch;
    ephemeral feature branches merged and deleted are typically pruned
    on next sync rather than kept indefinitely."""
    name: str = Field(..., description="Branch ref name")
    is_default: bool = Field(default=False, description="Whether this is the repo's default branch")
    need_attention: bool = Field(default=False)


class GitRelease(BaseModel):
    """A tagged release or version milestone (semver tag, GitHub Release)."""
    tag_name: str = Field(..., description="Git tag, e.g. v1.4.0")
    name: str | None = Field(default=None, description="Release title")
    need_attention: bool = Field(default=False)


ENTITY_TYPES: dict[str, type[BaseModel]] = {
    "Employee": Employee,
    "Organization": Organization,
    "Repository": Repository,
    "Product": Product,
    "Document": Document,
    "GitRepository": GitRepository,
    "GitFile": GitFile,
    "GitCommit": GitCommit,
    "Developer": Developer,
    "CodeClass": CodeClass,
    "CodeFunction": CodeFunction,
    "CodeMethod": CodeMethod,
    "GitPullRequest": GitPullRequest,
    "GitIssue": GitIssue,
    "CodeInterface": CodeInterface,
    "TypeDefinition": TypeDefinition,
    "APIEndpoint": APIEndpoint,
    "DataModel": DataModel,
    "PackageDependency": PackageDependency,
    "License": License,
    "SecurityAdvisory": SecurityAdvisory,
    "CIWorkflow": CIWorkflow,
    "CIJob": CIJob,
    "TestSuite": TestSuite,
    "DeploymentTarget": DeploymentTarget,
    "GitBranch": GitBranch,
    "GitRelease": GitRelease,
}


class ReportsTo(GraphitiElement):
    """Reporting line between two people."""


class ManagedBy(GraphitiElement):
    """Ownership or stewardship of a system by a person or team."""


class DependsOn(GraphitiElement):
    """A technical or operational dependency."""


class Uses(GraphitiElement):
    """Consumption of a technology, product or service."""


class BelongsTo(GraphitiElement):
    """Membership of a person or component within a larger unit."""


class Contains(GraphitiElement):
    """A container holding an item (e.g., GitRepository contains GitFile)."""


class HasCommit(GraphitiElement):
    """A repository contains a commit."""


class Modifies(GraphitiElement):
    """A commit modifies a file."""


class AuthoredBy(GraphitiElement):
    """A commit or file is authored by a developer."""


class Imports(GraphitiElement):
    """A source file imports another file or module."""


class Affects(GraphitiElement):
    """An issue or PR affects a code component or file."""


class ReportedBy(GraphitiElement):
    """An issue or PR is reported/created by a developer."""


class Calls(BaseModel):
    """Function/method-level invocation edge. Static call-graph reference,
    not runtime trace."""
    call_site_line: int | None = Field(default=None, description="Line number of call site, if known")


class Extends(BaseModel):
    """Class or interface inheritance edge (subclassing)."""
    pass


class Implements(BaseModel):
    """Conformance edge: a class implements an interface/protocol."""
    pass


class Exposes(BaseModel):
    """A function/method is bound to and exposes a given API endpoint."""
    pass


class Queries(BaseModel):
    """Code reads/writes a given data model (ORM/table/event schema)."""
    operation: Literal["read", "write", "read_write"] | None = Field(default=None, description="Nature of the data access")


class Requires(BaseModel):
    """A repository or file declares a dependency on a package."""
    pass


class HasLicense(BaseModel):
    """A package or repository is governed by a given license."""
    pass


class HasAdvisory(BaseModel):
    """A package version is affected by a security advisory."""
    pass


class HasJob(BaseModel):
    """A CI workflow contains a given job."""
    pass


class TriggeredBy(BaseModel):
    """A CI workflow run was triggered by a commit or pull request."""
    pass


class Tests(BaseModel):
    """A test suite exercises a given file, class, function, or method."""
    pass


class DeploysTo(BaseModel):
    """A CI job/workflow deploys artifacts to a deployment target."""
    pass


class BranchedFrom(BaseModel):
    """A branch was forked/created from another branch."""
    pass


class MergedInto(BaseModel):
    """A branch or pull request was merged into a target branch."""
    pass


class HasTag(BaseModel):
    """A commit is tagged as a specific release."""
    pass


class ReviewedBy(BaseModel):
    """A pull request was reviewed by a developer (comment-level review, any state)."""
    review_state: Literal["commented", "changes_requested", "approved"] | None = Field(default=None)


class ApprovedBy(BaseModel):
    """A pull request received a formal approval from a developer."""
    pass


EDGE_TYPES: dict[str, type[BaseModel]] = {
    "REPORTS_TO": ReportsTo,
    "MANAGED_BY": ManagedBy,
    "DEPENDS_ON": DependsOn,
    "USES": Uses,
    "BELONGS_TO": BelongsTo,
    "CONTAINS": Contains,
    "HAS_COMMIT": HasCommit,
    "MODIFIES": Modifies,
    "AUTHORED_BY": AuthoredBy,
    "IMPORTS": Imports,
    "AFFECTS": Affects,
    "REPORTED_BY": ReportedBy,
    "CALLS": Calls,
    "EXTENDS": Extends,
    "IMPLEMENTS": Implements,
    "EXPOSES": Exposes,
    "QUERIES": Queries,
    "REQUIRES": Requires,
    "HAS_LICENSE": HasLicense,
    "HAS_ADVISORY": HasAdvisory,
    "HAS_JOB": HasJob,
    "TRIGGERED_BY": TriggeredBy,
    "TESTS": Tests,
    "DEPLOYS_TO": DeploysTo,
    "BRANCHED_FROM": BranchedFrom,
    "MERGED_INTO": MergedInto,
    "HAS_TAG": HasTag,
    "REVIEWED_BY": ReviewedBy,
    "APPROVED_BY": ApprovedBy,
}

# Which relations are legal between which entity types, enforced in
# `kg_graphiti.llm_stub._permitted_pairs`.
#
# DIRECTION CONVENTION: each pair reads as (source, target) in the direction the
# relation *name* implies. MANAGED_BY is the one that catches people out — it
# reads "<source> is managed by <target>", so the steward is the target and the
# managed thing is the source, which is also what the extractor emits.
#
# ("Entity", "Entity") is the catch-all for nodes that came back untyped; a node
# that does have a type is held to its own signature rather than falling back to it.
EDGE_TYPE_MAP: dict[tuple[str, str], list[str]] = {
    ("Employee", "Employee"): ["REPORTS_TO"],
    ("Repository", "Employee"): ["MANAGED_BY"],
    ("Product", "Employee"): ["MANAGED_BY"],
    ("Organization", "Employee"): ["MANAGED_BY"],
    ("Document", "Employee"): ["MANAGED_BY"],
    ("Repository", "Organization"): ["MANAGED_BY", "BELONGS_TO"],
    ("Product", "Organization"): ["MANAGED_BY"],
    ("Employee", "Organization"): ["BELONGS_TO"],
    ("Organization", "Organization"): ["BELONGS_TO", "DEPENDS_ON"],
    ("Repository", "Repository"): ["DEPENDS_ON"],
    ("Repository", "Product"): ["DEPENDS_ON", "USES"],
    ("Product", "Product"): ["DEPENDS_ON", "USES"],
    ("Product", "Repository"): ["DEPENDS_ON", "USES"],
    ("Employee", "Product"): ["USES"],
    ("Employee", "Repository"): ["USES"],
    ("Organization", "Repository"): ["USES"],
    ("GitRepository", "GitFile"): ["CONTAINS"],
    ("GitRepository", "GitCommit"): ["HAS_COMMIT"],
    ("GitRepository", "GitPullRequest"): ["CONTAINS"],
    ("GitRepository", "GitIssue"): ["CONTAINS"],
    ("GitCommit", "GitFile"): ["MODIFIES"],
    ("GitCommit", "Developer"): ["AUTHORED_BY"],
    ("GitFile", "GitFile"): ["IMPORTS"],
    ("GitFile", "CodeClass"): ["CONTAINS"],
    ("GitFile", "CodeFunction"): ["CONTAINS"],
    ("CodeClass", "CodeMethod"): ["CONTAINS"],
    ("GitPullRequest", "GitFile"): ["MODIFIES", "AFFECTS"],
    ("GitPullRequest", "Developer"): ["AUTHORED_BY"],
    ("GitIssue", "GitFile"): ["AFFECTS"],
    ("GitIssue", "CodeClass"): ["AFFECTS"],
    ("GitIssue", "CodeFunction"): ["AFFECTS"],
    ("GitIssue", "Developer"): ["REPORTED_BY"],

    # Domain A
    ("GitFile", "CodeInterface"): ["CONTAINS"],
    ("GitFile", "TypeDefinition"): ["CONTAINS"],
    ("GitFile", "DataModel"): ["CONTAINS"],
    ("CodeFunction", "CodeFunction"): ["CALLS"],
    ("CodeMethod", "CodeMethod"): ["CALLS"],
    ("CodeFunction", "CodeMethod"): ["CALLS"],
    ("CodeMethod", "CodeFunction"): ["CALLS"],
    ("CodeClass", "CodeClass"): ["EXTENDS"],
    ("CodeInterface", "CodeInterface"): ["EXTENDS"],
    ("CodeClass", "CodeInterface"): ["IMPLEMENTS"],
    ("CodeFunction", "APIEndpoint"): ["EXPOSES"],
    ("CodeMethod", "APIEndpoint"): ["EXPOSES"],
    ("CodeFunction", "DataModel"): ["QUERIES"],
    ("CodeMethod", "DataModel"): ["QUERIES"],
    ("APIEndpoint", "DataModel"): ["QUERIES"],

    # Domain B
    ("GitRepository", "PackageDependency"): ["REQUIRES"],
    ("GitFile", "PackageDependency"): ["REQUIRES"],
    ("PackageDependency", "License"): ["HAS_LICENSE"],
    ("GitRepository", "License"): ["HAS_LICENSE"],
    ("PackageDependency", "SecurityAdvisory"): ["HAS_ADVISORY"],

    # Domain C
    ("CIWorkflow", "CIJob"): ["HAS_JOB"],
    ("CIWorkflow", "GitCommit"): ["TRIGGERED_BY"],
    ("CIWorkflow", "GitPullRequest"): ["TRIGGERED_BY"],
    ("TestSuite", "GitFile"): ["TESTS"],
    ("TestSuite", "CodeClass"): ["TESTS"],
    ("TestSuite", "CodeFunction"): ["TESTS"],
    ("TestSuite", "CodeMethod"): ["TESTS"],
    ("CIJob", "DeploymentTarget"): ["DEPLOYS_TO"],
    ("CIWorkflow", "DeploymentTarget"): ["DEPLOYS_TO"],

    # Domain D
    ("GitBranch", "GitBranch"): ["BRANCHED_FROM", "MERGED_INTO"],
    ("GitPullRequest", "GitBranch"): ["MERGED_INTO"],
    ("GitCommit", "GitRelease"): ["HAS_TAG"],
    ("GitPullRequest", "Developer"): ["REVIEWED_BY", "APPROVED_BY"],

    ("Entity", "Entity"): list(EDGE_TYPES),
}

# Defaults seeded into the ontology registry the console reads.
RELATION_DEFAULTS: list[dict[str, object]] = [
    {
        "relation_type": "REPORTS_TO",
        "version": 1,
        "source_types": ["Employee"],
        "target_types": ["Employee"],
        "max_active_outgoing": 1,
        "temporal": True,
        "overlap_allowed": False,
    },
    {
        "relation_type": "MANAGED_BY",
        "version": 1,
        "source_types": ["Repository", "Product", "Organization", "Document"],
        "target_types": ["Employee", "Organization"],
        "max_active_outgoing": 1,
        "temporal": True,
        "overlap_allowed": False,
    },
    {
        "relation_type": "DEPENDS_ON",
        "version": 1,
        "source_types": ["Repository", "Product", "Organization"],
        "target_types": ["Repository", "Product"],
        "max_active_outgoing": 8,
        "temporal": True,
        "overlap_allowed": True,
    },
    {
        "relation_type": "USES",
        "version": 1,
        "source_types": ["Employee", "Organization", "Repository", "Product"],
        "target_types": ["Product", "Repository"],
        "max_active_outgoing": 8,
        "temporal": True,
        "overlap_allowed": True,
    },
    {
        "relation_type": "BELONGS_TO",
        "version": 1,
        "source_types": ["Employee", "Repository", "Organization"],
        "target_types": ["Organization"],
        "max_active_outgoing": 1,
        "temporal": True,
        "overlap_allowed": False,
    },
    {
        "relation_type": "AWARDED_AT",
        "version": 1,
        "source_types": ["Employee", "Document", "Organization"],
        "target_types": ["Organization", "Document"],
        "max_active_outgoing": 5,
        "temporal": True,
        "overlap_allowed": True,
    },
    {
        "relation_type": "AWARDED_BY",
        "version": 1,
        "source_types": ["Employee", "Document", "Organization"],
        "target_types": ["Organization"],
        "max_active_outgoing": 5,
        "temporal": True,
        "overlap_allowed": True,
    },
    {
        "relation_type": "LOCATED_IN",
        "version": 1,
        "source_types": ["Employee", "Organization", "Repository"],
        "target_types": ["Organization"],
        "max_active_outgoing": 5,
        "temporal": True,
        "overlap_allowed": True,
    },
    {
        "relation_type": "CONTAINS",
        "version": 1,
        "source_types": ["GitRepository", "GitFile", "CodeClass"],
        "target_types": ["GitFile", "CodeClass", "CodeFunction", "CodeMethod", "GitPullRequest", "GitIssue", "CodeInterface", "TypeDefinition", "DataModel"],
        "max_active_outgoing": 1000,
        "temporal": True,
        "overlap_allowed": True,
    },
    {
        "relation_type": "HAS_COMMIT",
        "version": 1,
        "source_types": ["GitRepository"],
        "target_types": ["GitCommit"],
        "max_active_outgoing": 1000,
        "temporal": True,
        "overlap_allowed": True,
    },
    {
        "relation_type": "MODIFIES",
        "version": 1,
        "source_types": ["GitCommit", "GitPullRequest"],
        "target_types": ["GitFile"],
        "max_active_outgoing": 1000,
        "temporal": True,
        "overlap_allowed": True,
    },
    {
        "relation_type": "AUTHORED_BY",
        "version": 1,
        "source_types": ["GitCommit", "GitPullRequest"],
        "target_types": ["Developer"],
        "max_active_outgoing": 10,
        "temporal": True,
        "overlap_allowed": True,
    },
    {
        "relation_type": "IMPORTS",
        "version": 1,
        "source_types": ["GitFile"],
        "target_types": ["GitFile"],
        "max_active_outgoing": 1000,
        "temporal": True,
        "overlap_allowed": True,
    },
    {
        "relation_type": "AFFECTS",
        "version": 1,
        "source_types": ["GitPullRequest", "GitIssue"],
        "target_types": ["GitFile", "CodeClass", "CodeFunction"],
        "max_active_outgoing": 1000,
        "temporal": True,
        "overlap_allowed": True,
    },
    {
        "relation_type": "REPORTED_BY",
        "version": 1,
        "source_types": ["GitIssue"],
        "target_types": ["Developer"],
        "max_active_outgoing": 1,
        "temporal": True,
        "overlap_allowed": False,
    },
    {"relation_type": "CALLS",         "version": 1, "source_types": ["CodeFunction", "CodeMethod"], "target_types": ["CodeFunction", "CodeMethod"], "max_active_outgoing": 1000, "temporal": True,  "overlap_allowed": True},
    {"relation_type": "EXTENDS",       "version": 1, "source_types": ["CodeClass", "CodeInterface"], "target_types": ["CodeClass", "CodeInterface"], "max_active_outgoing": 5,    "temporal": True,  "overlap_allowed": True},
    {"relation_type": "IMPLEMENTS",    "version": 1, "source_types": ["CodeClass"], "target_types": ["CodeInterface"], "max_active_outgoing": 20,   "temporal": True,  "overlap_allowed": True},
    {"relation_type": "EXPOSES",       "version": 1, "source_types": ["CodeFunction", "CodeMethod"], "target_types": ["APIEndpoint"], "max_active_outgoing": 5,    "temporal": True,  "overlap_allowed": True},
    {"relation_type": "QUERIES",       "version": 1, "source_types": ["CodeFunction", "CodeMethod", "APIEndpoint"], "target_types": ["DataModel"], "max_active_outgoing": 50,   "temporal": True,  "overlap_allowed": True},

    {"relation_type": "REQUIRES",      "version": 1, "source_types": ["GitRepository", "GitFile"], "target_types": ["PackageDependency"], "max_active_outgoing": 1000, "temporal": True,  "overlap_allowed": True},
    {"relation_type": "HAS_LICENSE",   "version": 1, "source_types": ["PackageDependency", "GitRepository"], "target_types": ["License"], "max_active_outgoing": 1,    "temporal": True,  "overlap_allowed": False},
    {"relation_type": "HAS_ADVISORY",  "version": 1, "source_types": ["PackageDependency"], "target_types": ["SecurityAdvisory"], "max_active_outgoing": 20,   "temporal": True,  "overlap_allowed": True},

    {"relation_type": "HAS_JOB",       "version": 1, "source_types": ["CIWorkflow"], "target_types": ["CIJob"], "max_active_outgoing": 100,  "temporal": True,  "overlap_allowed": True},
    {"relation_type": "TRIGGERED_BY",  "version": 1, "source_types": ["CIWorkflow"], "target_types": ["GitCommit", "GitPullRequest"], "max_active_outgoing": 1000, "temporal": True,  "overlap_allowed": True},
    {"relation_type": "TESTS",         "version": 1, "source_types": ["TestSuite"], "target_types": ["GitFile", "CodeClass", "CodeFunction", "CodeMethod"], "max_active_outgoing": 1000, "temporal": True,  "overlap_allowed": True},
    {"relation_type": "DEPLOYS_TO",    "version": 1, "source_types": ["CIJob", "CIWorkflow"], "target_types": ["DeploymentTarget"], "max_active_outgoing": 10,   "temporal": True,  "overlap_allowed": True},

    {"relation_type": "BRANCHED_FROM", "version": 1, "source_types": ["GitBranch"], "target_types": ["GitBranch"], "max_active_outgoing": 1,    "temporal": True,  "overlap_allowed": False},
    {"relation_type": "MERGED_INTO",   "version": 1, "source_types": ["GitBranch", "GitPullRequest"], "target_types": ["GitBranch"], "max_active_outgoing": 1,    "temporal": True,  "overlap_allowed": False},
    {"relation_type": "HAS_TAG",       "version": 1, "source_types": ["GitCommit"], "target_types": ["GitRelease"], "max_active_outgoing": 1,    "temporal": True,  "overlap_allowed": False},
    {"relation_type": "REVIEWED_BY",   "version": 1, "source_types": ["GitPullRequest"], "target_types": ["Developer"], "max_active_outgoing": 20,   "temporal": True,  "overlap_allowed": True},
    {"relation_type": "APPROVED_BY",   "version": 1, "source_types": ["GitPullRequest"], "target_types": ["Developer"], "max_active_outgoing": 10,   "temporal": True,  "overlap_allowed": True},
]

ENTITY_TYPE_DESCRIPTIONS: list[dict[str, str]] = [
    {
        "id": "Employee",
        "name": "Employee",
        "description": "A person: employee, intern, candidate or student. Not emails or phones.",
    },
    {
        "id": "Organization",
        "name": "Organization",
        "description": "A company, school, team, competition or external partner.",
    },
    {
        "id": "Repository",
        "name": "Repository",
        "description": "A source-code repository or software component.",
    },
    {
        "id": "Product",
        "name": "Product",
        "description": "A product, project, platform or technology (React, Kafka, SmartIn, ...).",
    },
    {
        "id": "Document",
        "name": "Document",
        "description": "A document, policy, runbook, certification or ticket.",
    },
    {
        "id": "GitRepository",
        "name": "GitRepository",
        "description": "A GitHub source-code repository.",
    },
    {
        "id": "GitFile",
        "name": "GitFile",
        "description": "A source code file within a GitHub repository.",
    },
    {
        "id": "GitCommit",
        "name": "GitCommit",
        "description": "A revision/commit in a Git repository.",
    },
    {
        "id": "Developer",
        "name": "Developer",
        "description": "A software developer or GitHub user.",
    },
    {
        "id": "CodeClass",
        "name": "CodeClass",
        "description": "A class defined in source code.",
    },
    {
        "id": "CodeFunction",
        "name": "CodeFunction",
        "description": "A function defined in source code.",
    },
    {
        "id": "CodeMethod",
        "name": "CodeMethod",
        "description": "A method defined in a class in source code.",
    },
    {
        "id": "GitPullRequest",
        "name": "GitPullRequest",
        "description": "A pull request in a GitHub repository.",
    },
    {
        "id": "GitIssue",
        "name": "GitIssue",
        "description": "An issue in a GitHub repository.",
    },
    {"id": "CodeInterface", "name": "CodeInterface", "description": "Abstract interface, protocol, or trait defining a contract without implementation."},
    {"id": "TypeDefinition", "name": "TypeDefinition", "description": "Named type alias, enum, union, or generic type declaration."},
    {"id": "APIEndpoint", "name": "APIEndpoint", "description": "REST, GraphQL, or gRPC endpoint exposed by a function or controller."},
    {"id": "DataModel", "name": "DataModel", "description": "ORM model, database table, or event/message schema."},
    {"id": "PackageDependency", "name": "PackageDependency", "description": "External third-party package declared in a manifest, with ecosystem and dependency type."},
    {"id": "License", "name": "License", "description": "Software license identified by SPDX ID."},
    {"id": "SecurityAdvisory", "name": "SecurityAdvisory", "description": "Known vulnerability advisory (CVE/GHSA) affecting a dependency."},
    {"id": "CIWorkflow", "name": "CIWorkflow", "description": "CI/CD pipeline definition, e.g. a GitHub Actions workflow file."},
    {"id": "CIJob", "name": "CIJob", "description": "A job within a CI workflow."},
    {"id": "TestSuite", "name": "TestSuite", "description": "A test file or suite, mapped to the code it exercises."},
    {"id": "DeploymentTarget", "name": "DeploymentTarget", "description": "Runtime deployment environment: Docker, Kubernetes, cloud service, etc."},
    {"id": "GitBranch", "name": "GitBranch", "description": "A named Git branch/ref."},
    {"id": "GitRelease", "name": "GitRelease", "description": "A tagged release or version milestone."},
]
