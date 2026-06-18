"""
api/routers/delta.py
─────────────────────
GET /delta — what changed recently vs the previous equivalent window.

Compares the last N hours against the N hours before that to compute
direction and magnitude of change for events, alerts, and narratives.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from sqlalchemy import text

logger = logging.getLogger("vision_i.api.delta")
router = APIRouter(tags=["Delta"])


class DeltaResponse(BaseModel):
    new_events:      int           = 0
    new_alerts:      int           = 0
    new_narratives:  int           = 0
    prev_events:     int           = 0
    prev_alerts:     int           = 0
    prev_narratives: int           = 0
    direction:       str           = "flat"   # "up" | "down" | "flat"
    delta_count:     int           = 0
    summary:         str           = ""
    hours:           int           = 6
    generated_at:    Optional[str] = None
    error:           Optional[str] = None

    class Config:
        extra = "allow"


@router.get("", response_model=DeltaResponse, summary="Recent change delta")
async def get_delta(
    request: Request,
    hours:   int = Query(6, ge=1, le=72, description="Window size in hours"),
):
    """
    Compares the last `hours` vs the previous `hours` window.

    Returns direction (up/down/flat), absolute delta_count, and per-entity
    new/previous counts for events, alerts, and narratives.
    """
    from core.utils import utcnow_iso

    db_available = getattr(request.app.state, "db_available", False)
    if not db_available:
        return DeltaResponse(error="database_unavailable", generated_at=utcnow_iso())

    try:
        from storage.database import get_session

        async with get_session() as session:
            new_events, prev_events         = await _count_window_pair(
                session, "events", "timestamp", hours
            )
            new_alerts, prev_alerts         = await _count_window_pair(
                session, "alerts", "detected_at", hours
            )
            new_narratives, prev_narratives = await _count_window_pair(
                session, "narratives", "detected_at", hours
            )

        # Direction is driven by total event volume change
        delta = new_events - prev_events
        if delta > 0:
            direction = "up"
        elif delta < 0:
            direction = "down"
        else:
            direction = "flat"

        summary = _build_summary(
            direction, delta, new_events, new_alerts, new_narratives, hours
        )

        return DeltaResponse(
            new_events=new_events,
            new_alerts=new_alerts,
            new_narratives=new_narratives,
            prev_events=prev_events,
            prev_alerts=prev_alerts,
            prev_narratives=prev_narratives,
            direction=direction,
            delta_count=delta,
            summary=summary,
            hours=hours,
            generated_at=utcnow_iso(),
        )

    except Exception as exc:
        logger.error("get_delta failed: %s", exc, exc_info=True)
        return DeltaResponse(error=str(exc), generated_at=utcnow_iso())


# ── helpers ───────────────────────────────────────────────────────────────────

async def _count_window_pair(session, table: str, ts_col: str, hours: int):
    """
    Return (current_window_count, previous_window_count) for a table.

    current  = [NOW - hours,   NOW]
    previous = [NOW - 2*hours, NOW - hours]
    """
    try:
        result = await session.execute(
            text(f"""
                SELECT
                    COUNT(*) FILTER (
                        WHERE {ts_col} >= NOW() - INTERVAL '{hours} hours'
                    ) AS current_count,
                    COUNT(*) FILTER (
                        WHERE {ts_col} >= NOW() - INTERVAL '{hours * 2} hours'
                          AND {ts_col} <  NOW() - INTERVAL '{hours} hours'
                    ) AS prev_count
                FROM {table}
                WHERE {ts_col} >= NOW() - INTERVAL '{hours * 2} hours'
            """)
        )
        row = result.one_or_none()
        if row:
            return int(row[0] or 0), int(row[1] or 0)
        return 0, 0
    except Exception as exc:
        logger.debug("_count_window_pair(%s) failed: %s", table, exc)
        return 0, 0


def _build_summary(
    direction: str,
    delta: int,
    new_events: int,
    new_alerts: int,
    new_narratives: int,
    hours: int,
) -> str:
    arrow = {"up": "↑", "down": "↓", "flat": "→"}.get(direction, "→")
    abs_delta = abs(delta)
    if direction == "flat" or abs_delta == 0:
        return (
            f"No change in event volume over the last {hours}h. "
            f"{new_events} events, {new_alerts} alerts, {new_narratives} narratives."
        )
    verb = "up" if direction == "up" else "down"
    return (
        f"{arrow} Event volume {verb} by {abs_delta} vs previous {hours}h window. "
        f"{new_events} new events, {new_alerts} new alerts, {new_narratives} new narratives."
    )
