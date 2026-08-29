"""Knowledge-graph access control layer.

Provides identity, session management, role-based authorization, and
scope-filtered graph reads for Synapse v2.
"""

from kg_acl.models import AccessContext, Principal, Sensitivity

__all__ = ["Sensitivity", "Principal", "AccessContext"]
