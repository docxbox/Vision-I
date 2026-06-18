"""
api/routers/overview.py
────────────────────────
GET /overview          — full system state snapshot
GET /overview/source-health  — source health table only

Both endpoints are in _OPEN so they work without X-Internal-Key in dev.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

logger = logging.getLogger("vision_i.api.overview")
router = APIRouter(tags=["Overview"])


# ── Response schemas ──────────────────────────────────────────────────────────

class OverviewResponse(BaseModel):
    total_events:    int              = 0
    alert_count:     int              = 0
    narrative_count: int              = 0
    asset_count:     int              = 0
    top_events:      List[Any]        = []
    active_alerts:   List[Any]        = []
    source_health:   List[Any]        = []
    generated_at:    Optional[str]    = None
    window_hours:    int              = 24
    error:           Optional[str]    = None

    class Config:
        extra = "allow"


class SourceHealthResponse(BaseModel):
    sources: List[Any] = []
    total:   int       = 0
    error:   Optional[str] = None

    class Config:
        extra = "allow"


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=OverviewResponse, summary="System state overview")
async def get_overview(
    request:      Request,
    window_hours: int = Query(24, ge=1, le=168, description="Look-back window in hours"),
):
    """
    Aggregates counts and top events/alerts from the database.
    Returns a safe empty payload if the database is unavailable.
    """
    db_available = getattr(request.app.state, "db_available", False)
    if not db_available:
        logger.warning("get_overview: DB unavailable — returning empty payload")
        return OverviewResponse(error="database_unavailable")

    try:
        from intelligence.overview_builder import OverviewBuilder
        from storage.database import get_session

        async with get_session() as session:
            data = await OverviewBuilder().build(session, window_hours=window_hours)
        return data

    except Exception as exc:
        logger.error("get_overview failed: %s", exc, exc_info=True)
        return OverviewResponse(error=str(exc))


@router.get(
    "/source-health",
    response_model=SourceHealthResponse,
    summary="Per-source health status",
)
async def get_source_health(request: Request):
    """
    Returns health rows from source_health table (upserted by extractors)
    and augments them with source_checkpoints data.
    Falls back gracefully if either table is missing.
    """
    db_available = getattr(request.app.state, "db_available", False)
    if not db_available:
        return SourceHealthResponse(error="database_unavailable")

    try:
        from intelligence.overview_builder import OverviewBuilder
        from storage.source_health_repo import get_all_source_health
        from storage.database import get_session

        async with get_session() as session:
            rows = await get_all_source_health(session)
            if not rows:
                rows = await OverviewBuilder()._source_health(session)

        return SourceHealthResponse(sources=rows, total=len(rows))

    except Exception as exc:
        logger.error("get_source_health failed: %s", exc, exc_info=True)
        return SourceHealthResponse(error=str(exc))
