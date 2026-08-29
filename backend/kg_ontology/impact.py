"""Impact assessment for proposed ontology changes.

A constraint change is a migration, so the console shows what it would break
before a steward approves it.
"""

from __future__ import annotations


def assess_impact(
    field: str, old_value: object, new_value: object, affected_edges: list[dict]
) -> tuple[str, str]:
    """Return `(severity, human-readable report)`.

    Severity is one of High / Medium / Low, which is what the Ontology screen
    renders as a badge.
    """
    count = len(affected_edges)

    if field == "max_active_outgoing":
        old = _as_int(old_value)
        new = _as_int(new_value)
        if new < old:
            severity = "High" if count else "Medium"
            report = (
                f"Tightening max_active_outgoing from {old} to {new} makes any node with more "
                f"than {new} active outgoing edge(s) invalid. {count} edge(s) would need to be "
                "closed or re-parented before this can be enforced."
            )
        elif new > old:
            severity = "Low"
            report = (
                f"Relaxing max_active_outgoing from {old} to {new} cannot invalidate existing "
                "data; it only permits more concurrent edges."
            )
        else:
            severity = "Low"
            report = "No change to max_active_outgoing."
        return severity, report

    if field == "temporal":
        if _as_bool(new_value) is False:
            return (
                "High",
                "Disabling temporal tracking stops recording valid/invalid intervals for this "
                "relation. Existing history is retained but new contradictions will overwrite "
                "rather than supersede.",
            )
        return ("Medium", "Enabling temporal tracking starts recording validity intervals.")

    if field == "overlap_allowed":
        if _as_bool(new_value) is False:
            return (
                "High",
                f"Disallowing overlap means concurrent edges of this type become conflicts. "
                f"{count} existing edge pair(s) would be flagged for review.",
            )
        return ("Low", "Allowing overlap relaxes an existing constraint; nothing is invalidated.")

    return ("Medium", f"Change to '{field}' has no automated impact model; review manually.")


def _as_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _as_bool(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)
