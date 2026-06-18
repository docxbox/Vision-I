"""
api/routers/narratives.py
──────────────────────────
Narrative detection endpoints.

ARCHITECTURE:
  POST /narratives/detect        — queue-only, returns 202 (no blocking compute)
  GET  /narratives/              — list from DB (fast indexed query)
  GET  /narratives/summary       — read from precomputed Redis, fallback to DB
  GET  /narratives/influence     — read from precomputed Redis, fallback to Neo4j
  POST /narratives/influence/update — queue-only, returns 202
"""

import logging
import uuid
import base64
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from storage.database import get_session
from storage.intelligence_repo import NarrativeRepository

logger = logging.getLogger("vision_i.api.narratives")
router = APIRouter(tags=["narratives"])

# ── Response schemas ───────────────────────────────────────────────────────

class NarrativeItemSchema(BaseModel):
    model_config = ConfigDict(extra="allow")
    narrative_id: Optional[str] = None
    topic: Optional[str] = None
    signal_type: Optional[str] = None
    severity: Optional[str] = None
    strength: float = 0.0
    confidence: float = 0.0
    source_count: int = 0

class DetectNarrativesResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    status: Optional[str] = None
    job_id: Optional[str] = None
    message: Optional[str] = None
    total: Optional[int] = None
    narratives_detected: Optional[int] = None

class NarrativeListResponse(BaseModel):
    total: int = 0
    limit: int = 50
    offset: int = 0
    narratives: List[Any] = Field(default_factory=list)

class NarrativeWindowResponse(BaseModel):
    offset: int = 0
    limit: int = 50
    has_more: bool = False
    next_cursor: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)

class NarrativeSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    total: int = 0
    by_type: Dict[str, Any] = Field(default_factory=dict)
    by_severity: Dict[str, Any] = Field(default_factory=dict)

class NarrativeTimelineResponse(BaseModel):
    generated_at: Optional[str] = None
    bucket: str = "day"
    topic: Optional[str] = None
    data: List[Any] = Field(default_factory=list)
    count: int = 0

class InfluenceNetworkResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    nodes: List[Any] = Field(default_factory=list)
    edges: List[Any] = Field(default_factory=list)
    node_count: int = 0
    edge_count: int = 0

class InfluenceUpdateResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    status: Optional[str] = None
    job_id: Optional[str] = None
    message: Optional[str] = None
    updated: Optional[int] = None

class NarrativeForecastResponse(BaseModel):
    narrative_id: str = ""
    horizon: int = 0
    method: str = ""
    confidence: float = 0.0
    history: List[float] = Field(default_factory=list)
    forecast: List[float] = Field(default_factory=list)
    lower: List[float] = Field(default_factory=list)
    upper: List[float] = Field(default_factory=list)


def _encode_cursor(offset: int) -> str:
    return base64.b64encode(str(max(0, int(offset))).encode("utf-8")).decode("utf-8")


def _decode_cursor(cursor: Optional[str]) -> int:
    if not cursor:
        return 0
    try:
        raw = base64.b64decode(cursor.encode("utf-8")).decode("utf-8")
        value = int(raw)
        return value if value >= 0 else 0
    except Exception:
        return 0


@router.post("/detect", status_code=202, response_model=DetectNarrativesResponse)
async def trigger_detection(
    request:       Request,
    window_hours:  int = Query(6,  ge=1,  le=168),
    baseline_days: int = Query(7,  ge=1,  le=30),
    persist:       bool = Query(True),
):
    """
    Queue narrative detection (non-blocking).
    The pipeline worker picks this up and runs the full detection cycle.
    Returns 202 Accepted with a job reference.
    """
    if not request.app.state.db_available:
        return {"status": "ok", "narratives_detected": 0, "message": "Database unavailable"}

    event_bus = request.app.state.event_bus
    job_id = str(uuid.uuid4())[:12]

    # If event bus available, publish request for pipeline worker
    if event_bus:
        try:
            await event_bus.publish("ingest_complete", {
                "batch_id": job_id,
                "event_count": 0,
                "job_type": "narrative_detect_request",
                "params": {
                    "window_hours": window_hours,
                    "baseline_days": baseline_days,
                    "persist": persist,
                },
            })
            return {
                "status": "queued",
                "job_id": job_id,
                "message": "Narrative detection queued for background processing",
            }
        except Exception as exc:
            logger.warning("Event bus publish failed, falling back to sync: %s", exc)

    # Fallback: run synchronously if no event bus
    try:
        from intelligence.narrative_detector import NarrativeDetector

        async with get_session() as session:
            detector = NarrativeDetector(
                session=session,
                graph=request.app.state.graph,
            )
            signals = await detector.detect(
                window_hours=window_hours,
                baseline_days=baseline_days,
            )

            if persist and signals:
                repo = NarrativeRepository(session)
                saved = await repo.upsert_signals(signals)

                if request.app.state.graph.available:
                    request.app.state.graph.write_narrative_nodes(
                        [s.to_dict() for s in signals]
                    )

        return {
            "total":   len(signals),
            "signals": [s.to_dict() for s in signals],
        }

    except Exception as exc:
        logger.error("Narrative detection failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("", summary="List stored narrative signals", response_model=NarrativeListResponse)
@router.get("/", include_in_schema=False)
async def list_narratives(
    request:      Request,
    signal_type:  Optional[str] = Query(None),
    severity:     Optional[str] = Query(None),
    status:       Optional[str] = Query("active"),
    from_time:    Optional[str] = Query(None),
    limit:        int           = Query(50, ge=1, le=200),
    offset:       int           = Query(0,  ge=0),
):
    """List stored narrative signals with optional filters."""
    if not request.app.state.db_available:
        return {"total": 0, "narratives": []}

    async with get_session() as session:
        repo = NarrativeRepository(session)
        total, narratives = await repo.list_narratives(
            signal_type=signal_type,
            severity=severity,
            status=status if status != "all" else None,
            from_time=from_time,
            limit=limit,
            offset=offset,
        )

    return {
        "total":      total,
        "limit":      limit,
        "offset":     offset,
        "narratives": narratives,
    }


@router.get("/window", summary="Playback window with cursor pagination", response_model=NarrativeWindowResponse)
async def narratives_window(
    request:      Request,
    signal_type:  Optional[str] = Query(None),
    severity:     Optional[str] = Query(None),
    status:       Optional[str] = Query("active"),
    from_time:    Optional[str] = Query(None),
    limit:        int           = Query(50, ge=1, le=200),
    cursor:       Optional[str] = Query(None),
):
    offset = _decode_cursor(cursor)
    payload = await list_narratives(
        request=request,
        signal_type=signal_type,
        severity=severity,
        status=status,
        from_time=from_time,
        limit=limit,
        offset=offset,
    )
    total = int(payload.get("total") or 0)
    returned = len(payload.get("narratives") or [])
    next_offset = offset + returned
    has_more = next_offset < total
    return {
        "offset": offset,
        "limit": limit,
        "has_more": has_more,
        "next_cursor": _encode_cursor(next_offset) if has_more else None,
        "data": payload,
    }


@router.get("/summary", response_model=NarrativeSummaryResponse)
async def narratives_summary(request: Request):
    """
    Returns count breakdown by signal_type and severity.
    Reads from precomputed Redis cache first (<5ms), falls back to DB.
    """
    # Try precomputed cache first
    event_bus = request.app.state.event_bus
    if event_bus:
        try:
            cached = await event_bus.cache_get("precomputed:narratives_summary")
            if cached:
                # Reshape to expected format
                total = sum(v.get("total", 0) for v in cached.values()) if isinstance(cached, dict) else 0
                by_type = {k: v.get("total", 0) for k, v in cached.items()} if isinstance(cached, dict) else {}
                by_severity = {}
                if isinstance(cached, dict):
                    for type_data in cached.values():
                        for sev, cnt in type_data.get("by_severity", {}).items():
                            by_severity[sev] = by_severity.get(sev, 0) + cnt
                return {
                    "total": total,
                    "by_type": by_type,
                    "by_severity": by_severity,
                    "_served_from": "precomputed",
                }
        except Exception as exc:
            logger.warning("Redis cache read failed: %s", exc)

    # Fallback to database query
    if not request.app.state.db_available:
        return {"by_type": {}, "by_severity": {}, "total": 0}

    try:
        from sqlalchemy import func, select
        from storage.database import NarrativeModel

        async with get_session() as session:
            by_type_rows = (
                await session.execute(
                    select(NarrativeModel.signal_type, func.count().label("cnt"))
                    .where(NarrativeModel.status == "active")
                    .group_by(NarrativeModel.signal_type)
                )
            ).fetchall()

            by_sev_rows = (
                await session.execute(
                    select(NarrativeModel.severity, func.count().label("cnt"))
                    .where(NarrativeModel.status == "active")
                    .group_by(NarrativeModel.severity)
                )
            ).fetchall()

            total = sum(r.cnt for r in by_type_rows)

        return {
            "total":       total,
            "by_type":     {r.signal_type: r.cnt for r in by_type_rows},
            "by_severity": {r.severity:    r.cnt for r in by_sev_rows},
        }

    except Exception as exc:
        logger.error("Narratives summary failed: %s", exc)
        return {"by_type": {}, "by_severity": {}, "total": 0}


@router.get("/timeline", response_model=NarrativeTimelineResponse)
async def narratives_timeline(
    request: Request,
    topic: Optional[str] = Query(None),
    bucket: str = Query("day", description="hour | day | week"),
    days_back: int = Query(7, ge=1, le=90),
):
    """Time series counts for narrative evolution."""
    if not request.app.state.db_available:
        return {"generated_at": None, "bucket": bucket, "topic": topic, "data": [], "count": 0}

    try:
        from intelligence.narrative_timeline import get_narrative_timeline
        return await get_narrative_timeline(
            topic=topic,
            bucket=bucket,
            days_back=days_back,
        )
    except Exception as exc:
        logger.error("Narrative timeline failed: %s", exc)
        return {"generated_at": None, "bucket": bucket, "topic": topic, "data": [], "count": 0}


@router.get("/influence", response_model=InfluenceNetworkResponse)
async def get_influence_network(
    request:      Request,
    limit:        int   = Query(200, ge=10, le=500),
    min_strength: float = Query(0.1, ge=0.0, le=1.0),
):
    """
    Returns the actor influence network graph.
    Reads from precomputed Redis cache first, falls back to Neo4j.
    """
    # Try precomputed cache first
    event_bus = request.app.state.event_bus
    if event_bus:
        try:
            cached = await event_bus.cache_get("precomputed:influence_network")
            if cached and isinstance(cached, dict):
                cached["_served_from"] = "precomputed"
                return cached
        except Exception as exc:
            logger.warning("Redis cache read failed: %s", exc)

    # Fallback to Neo4j
    if not request.app.state.graph.available:
        return {"nodes": [], "edges": [], "node_count": 0, "edge_count": 0}

    try:
        result = request.app.state.graph.get_influence_network(
            limit=limit,
            min_strength=min_strength,
        )
        if isinstance(result, dict):
            result.setdefault("node_count", len(result.get("nodes", [])))
            result.setdefault("edge_count", len(result.get("edges", [])))
        return result
    except Exception as exc:
        logger.error("Influence network failed: %s", exc)
        return {"nodes": [], "edges": [], "node_count": 0, "edge_count": 0}


@router.post("/influence/update", status_code=202, response_model=InfluenceUpdateResponse)
async def update_influence_scores(request: Request):
    """Queue influence score recalculation (non-blocking)."""
    if not request.app.state.db_available:
        return {"status": "ok", "updated": 0, "message": "Database unavailable"}

    event_bus = request.app.state.event_bus
    job_id = str(uuid.uuid4())[:12]

    if event_bus:
        try:
            await event_bus.publish("ingest_complete", {
                "batch_id": job_id,
                "event_count": 0,
                "job_type": "influence_update_request",
            })
            return {
                "status": "queued",
                "job_id": job_id,
                "message": "Influence update queued for background processing",
            }
        except Exception as exc:
            logger.warning("Event bus publish failed, falling back to sync: %s", exc)

    # Fallback: run synchronously
    try:
        from intelligence.influence_scorer import InfluenceScorer

        async with get_session() as session:
            scorer = InfluenceScorer(session=session, graph=request.app.state.graph)
            scores = await scorer.update_scores(top_k=500)

        return {"updated": len(scores), "top_5": dict(list(scores.items())[:5])}

    except Exception as exc:
        logger.error("Influence update failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/{narrative_id}/forecast", response_model=NarrativeForecastResponse)
async def forecast_narrative_endpoint(
    narrative_id: str,
    request: Request,
    horizon: int = Query(12, ge=1, le=72),
):
    """Forecast future strength of a narrative time series via ARIMA (with fallback)."""
    from intelligence.predictor import forecast_series

    history: list[float] = []
    if request.app.state.db_available:
        try:
            async with get_session() as session:
                repo = NarrativeRepository(session)
                narrative = await repo.get(narrative_id)
                if narrative is not None:
                    raw = getattr(narrative, "strength_history", None) or []
                    history = [float(v) for v in raw if v is not None]
        except Exception as exc:
            logger.warning("forecast: history lookup failed for %s: %s", narrative_id, exc)

    result = forecast_series(history, horizon=horizon)
    return {
        "narrative_id": narrative_id,
        "horizon": result.horizon,
        "method": result.method,
        "confidence": result.confidence,
        "history": result.history,
        "forecast": result.forecast,
        "lower": result.lower,
        "upper": result.upper,
    }
