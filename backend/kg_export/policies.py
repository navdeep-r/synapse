"""Access-control policies applied when a snapshot leaves the platform.

The export boundary is where access control actually matters: once a snapshot is
delivered downstream, the platform no longer controls who reads it.
"""

from __future__ import annotations

# Ordered from most to least restrictive.
TAG_RANK = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}

ACL_POLICIES: dict[str, str] = {
    "strict": (
        "Only facts whose every supporting source is public are exported. "
        "Any unknown or missing tag is treated as restricted."
    ),
    "balanced": (
        "Public and internal facts are exported. Confidential and restricted "
        "facts are removed, as are facts with an unknown tag."
    ),
    "permissive": (
        "Everything except explicitly restricted facts is exported. "
        "Intended for trusted internal consumers only."
    ),
}

_MAX_ALLOWED = {"strict": 0, "balanced": 1, "permissive": 2}


def apply_acl_policy(
    edges: list[dict], policy: str, tag_for_edge
) -> tuple[list[dict], list[dict]]:
    """Split edges into `(kept, removed)` under an ACL policy.

    `tag_for_edge` maps an edge to its effective access tag, which is the most
    restrictive tag among its supporting sources.
    """
    ceiling = _MAX_ALLOWED.get(policy, 1)

    kept: list[dict] = []
    removed: list[dict] = []

    for edge in edges:
        tag = tag_for_edge(edge)
        rank = TAG_RANK.get(tag, TAG_RANK["restricted"])
        if rank <= ceiling:
            kept.append(edge)
        else:
            removed.append({**edge, "_removed_reason": f"access_tag={tag}"})

    return kept, removed


def effective_tag(tags: list[str]) -> str:
    """The most restrictive tag wins; absence of evidence is not public."""
    if not tags:
        return "restricted"
    return max(tags, key=lambda t: TAG_RANK.get(t, TAG_RANK["restricted"]))
