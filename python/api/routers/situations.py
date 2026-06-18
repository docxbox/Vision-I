"""
api/routers/situations.py
──────────────────────────
GET /situations           — list active situations
GET /situations/{id}      — get single situation with member events
POST /situations/detect   — trigger manual situation detection on recent events

A Situation groups multiple related events into a higher-level intelligence object:
  (Event A + Event B + Event C) → Situation: "China Naval Activity — South China Sea"
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, List, Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("vision_i.api.situations")
router = APIRouter(tags=["situations"])

# ── Response schemas ───────────────────────────────────────────────────────

class SituationItemSchema(BaseModel):
    model_config = ConfigDict(extra="allow")
    situation_id: Optional[str] = None
    title: Optional[str] = None
    severity: Optional[str] = None
    status: Optional[str] = None

class SituationListResponse(BaseModel):
    total: int = 0
    limit: int = 50
    situations: List[Any] = Field(default_factory=list)

class SituationDetectResponse(BaseModel):
    total: int = 0
    situations: List[Any] = Field(default_factory=list)
    events_scanned: int = 0


@router.get("", summary="List situations", response_model=SituationListResponse)
async def list_situations(
    request:  Request,
    limit:    int            = Query(50, ge=1, le=200),
    severity: Optional[str] = Query(None),
    status:   Optional[str] = Query(None),
):
    """Return detected situations, newest first."""
    if not request.app.state.db_available:
        return {"total": 0, "situations": []}

    try:
        from storage.database import get_session
        from storage.situation_repo import list_situations as repo_list

        async with get_session() as session:
            situations = await repo_list(session, limit=limit, severity=severity, status=status)

        return {"total": len(situations), "limit": limit, "situations": situations}

    except Exception as exc:
        logger.error("List situations failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/detect", summary="Situation detect info", include_in_schema=False)
async def detect_situations_info():
    return JSONResponse(
        {"method": "POST", "detail": "Use POST /situations/detect to trigger detection"},
        status_code=405,
    )


@router.get("/{situation_id}", summary="Get a single situation", response_model=SituationItemSchema)
async def get_situation(situation_id: str, request: Request):
    if not request.app.state.db_available:
        return JSONResponse({"error": "Database unavailable"}, status_code=503)

    try:
        from storage.database import get_session
        from storage.situation_repo import get_situation as repo_get

        async with get_session() as session:
            situation = await repo_get(session, situation_id)

        if situation is None:
            return JSONResponse({"error": "Situation not found"}, status_code=404)

        return situation

    except Exception as exc:
        logger.error("Get situation %s failed: %s", situation_id, exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/detect", summary="Trigger manual situation detection", response_model=SituationDetectResponse)
async def detect_situations_endpoint(
    request:      Request,
    window_hours: int = Query(6, ge=1, le=48),
):
    """
    Run situation detection on events from the past window_hours.
    Persists results to PostgreSQL and Neo4j. Returns detected situations.
    """
    if not request.app.state.db_available:
        return JSONResponse({"error": "Database unavailable"}, status_code=503)

    try:
        from sqlalchemy import select, desc as sa_desc
        from storage.database import get_session, EventModel
        from storage.situation_repo import sync_active_situations, upsert_situation
        from intelligence.situation_detector import detect_situations

        cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)

        async with get_session() as session:
            rows = (await session.execute(
                select(
                    EventModel.event_id.label("event_id"),
                    EventModel.title.label("title"),
                    EventModel.description.label("description"),
                    EventModel.body.label("body"),
                    EventModel.source.label("source"),
                    EventModel.event_type.label("event_type"),
                    EventModel.timestamp.label("timestamp"),
                    EventModel.actors.label("actors"),
                    EventModel.tags.label("tags"),
                    EventModel.location_lat.label("location_lat"),
                    EventModel.location_lon.label("location_lon"),
                    EventModel.location_name.label("location_name"),
                    EventModel.sentiment_label.label("sentiment_label"),
                    EventModel.sentiment_score.label("sentiment_score"),
                    EventModel.risk_score.label("risk_score"),
                    EventModel.signal_count.label("signal_count"),
                    EventModel.extras.label("extras"),
                )
                .where(EventModel.ingest_time >= cutoff)
                .where(EventModel.source.not_in(("ais", "opensky")))
                .order_by(sa_desc(EventModel.ingest_time))
                .limit(800)
            )).all()

        if not rows:
            return {"total": 0, "situations": [], "events_scanned": 0}

        events = [_row_to_dict(r) for r in rows]
        import asyncio
        loop = asyncio.get_running_loop()
        situations = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: detect_situations(events, window_hours=window_hours)),
            timeout=80.0,
        )

        saved: list = []
        async with get_session() as session:
            for sit in situations:
                await upsert_situation(session, sit)
                saved.append(sit)
            await sync_active_situations(
                session,
                [sit.get("situation_id") for sit in situations if sit.get("situation_id")],
            )

        if situations:
            graph = request.app.state.graph
            if graph and graph.available:
                import asyncio
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, graph.write_situation_nodes, situations)

            event_bus = request.app.state.event_bus
            if event_bus:
                await event_bus.publish("situation_updated", {
                    "situation_count": len(saved),
                    "window_hours": window_hours,
                })

        return {
            "total":          len(saved),
            "situations":     saved,
            "events_scanned": len(events),
        }

    except Exception as exc:
        logger.error("Situation detection endpoint failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


def _row_to_dict(row) -> dict:
    from core.entity_normalizer import sanitize_event_text

    data = dict(row._mapping) if hasattr(row, "_mapping") else row
    payload = {
        "event_id":    data.get("event_id"),
        "title":       data.get("title") or "",
        "description": data.get("description"),
        "body":        data.get("body"),
        "source":      data.get("source"),
        "event_type":  data.get("event_type"),
        "timestamp":   data.get("timestamp").isoformat() if data.get("timestamp") else None,
        "actors":      data.get("actors") or [],
        "tags":        data.get("tags") or [],
        "location": {
            "lat":  data.get("location_lat"),
            "lon":  data.get("location_lon"),
            "name": data.get("location_name"),
        } if (data.get("location_name") or data.get("location_lat")) else {},
        "sentiment": {
            "label": data.get("sentiment_label"),
            "score": data.get("sentiment_score") or 0.5,
        } if data.get("sentiment_label") else None,
        "risk_score":      data.get("risk_score"),
        "signal_count":    data.get("signal_count"),
        "extras":          data.get("extras") or {},
    }
    return sanitize_event_text(payload)
