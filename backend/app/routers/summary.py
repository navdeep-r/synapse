"""Sidebar badge counts.

`AppLayout` hardcodes both counts to zero and hides a badge when it is not
greater than zero, so this endpoint is what makes them live.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.state import AppState, get_state

router = APIRouter()


@router.get("/summary/badges")
async def badges(state: AppState = Depends(get_state)) -> dict:
    open_candidates = int(
        await state.db.fetch_value(
            "SELECT COUNT(*) AS n FROM curation_candidates WHERE status = 'open'", (), 0
        )
        or 0
    )
    unfetched = int(
        await state.db.fetch_value(
            "SELECT COUNT(*) AS n FROM snapshots WHERE passed = 1 AND fetched_at IS NULL", (), 0
        )
        or 0
    )
    failed = int(
        await state.db.fetch_value(
            "SELECT COUNT(*) AS n FROM snapshots WHERE passed = 0", (), 0
        )
        or 0
    )

    return {
        "curation": open_candidates,
        "snapshots": unfetched,
        # Drives the `badgeAlert` styling on the Snapshot & Export item.
        "snapshots_alert": failed > 0,
    }
