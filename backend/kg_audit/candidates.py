"""Merge-candidate detection (v2 doc 3.8).

Graphiti exposes no confidence band on a resolution decision — a node was either
merged or it was not — so the review queue is reconstructed by auditing the
result: pairs that are *nearly* the same by the resolution core's own scoring,
but were not merged.

Every pair examined here is, by construction, a pair Graphiti left unmerged: if
it had merged them there would be one node, not two. So the audit asks a
different question than the resolver did — not "should these merge?" but "does
anything about these two suggest the resolver got it wrong?".

That makes an alias rule firing on a surviving pair the strongest signal
available, not a reason to stay quiet: `compare` says MSFT and Microsoft are the
same company, yet both are in the graph. Those lead the queue, followed by pairs
that merely score in the suspicious band.
"""

from __future__ import annotations

from dataclasses import dataclass

from kg_graphiti.resolution import REVIEW_THRESHOLD, compare, similarity


@dataclass(frozen=True)
class MergeCandidate:
    primary_uuid: str
    primary_name: str
    secondary_uuid: str
    secondary_name: str
    confidence: float
    reason: str
    entity_type: str
    group_id: str


def _entity_type(row: dict) -> str:
    labels = row.get("labels") or []
    if isinstance(labels, str):
        labels = [labels]
    for label in labels:
        if label and label != "Entity":
            return str(label)
    return "Entity"


def find_merge_candidates(
    entities: list[dict], *, threshold: float = REVIEW_THRESHOLD, limit: int = 100
) -> list[MergeCandidate]:
    """Pairs that look like the same entity but were left distinct.

    Only compares within a group, since Graphiti never resolves across groups
    and proposing a cross-tenant merge would be wrong by construction.
    """
    candidates: list[MergeCandidate] = []

    for index, left in enumerate(entities):
        if not left.get("name") or not isinstance(left.get("name"), str):
            continue
        for right in entities[index + 1 :]:
            if not right.get("name") or not isinstance(right.get("name"), str) or left.get("group_id") != right.get("group_id"):
                continue
            left_name, right_name = left["name"], right["name"]
            verdict = compare(left_name, right_name)
            score = similarity(left_name, right_name)

            if verdict.is_match:
                confidence = max(verdict.score, score)
                reason = (
                    f"The {verdict.reason} rule treats these as the same entity, but "
                    f"both survived as separate nodes. Likely a missed merge."
                )
            elif score >= threshold:
                confidence = score
                reason = (
                    f"Names are {score:.0%} similar but no alias rule matched "
                    f"({verdict.reason}). A steward should confirm."
                )
            else:
                continue

            # Present the longer/more complete name as the survivor.
            primary, secondary = (
                (left, right) if len(left_name) >= len(right_name) else (right, left)
            )
            candidates.append(
                MergeCandidate(
                    primary_uuid=primary["uuid"],
                    primary_name=primary["name"],
                    secondary_uuid=secondary["uuid"],
                    secondary_name=secondary["name"],
                    confidence=round(confidence, 4),
                    reason=reason,
                    entity_type=_entity_type(primary),
                    group_id=primary.get("group_id", "synapse"),
                )
            )

    candidates.sort(key=lambda c: c.confidence, reverse=True)
    return candidates[:limit]
