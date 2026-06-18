"""
api/routers/alerts.py
──────────────────────
Anomaly alert endpoints.

ARCHITECTURE:
  POST /alerts/scan         — queue-only, returns 202 (no blocking compute)
  GET  /alerts              — list from DB (fast indexed query)
  GET  /alerts/summary      — read from precomputed Redis, fallback to DB
  POST /alerts/{id}/ack     — acknowledge (direct DB write)
  POST /alerts/{id}/resolve — resolve (direct DB write)
"""

import logging
import uuid
import base64
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger("vision_i.api.alerts")
router = APIRouter(tags=["alerts"])


# ── Response schemas ─────────────────────────────────────────────────────────

class AlertSchema(BaseModel):
    alert_id:     Optional[str]   = None
    alert_type:   Optional[str]   = None
    severity:     Optional[str]   = None
    title:        Optional[str]   = None
    description:  Optional[str]   = None
    entity:       Optional[str]   = None
    region:       Optional[str]   = None
    location:     Optional[str]   = None
    anomaly_score: Optional[float] = None
    z_score:      Optional[float]  = None
    acknowledged: Optional[bool]  = None
    escalated:    Optional[bool]  = None
    dismissed:    Optional[bool]  = None
    resolved_at:  Optional[str]   = None
    detected_at:  Optional[str]   = None

    class Config:
        extra = "allow"


class AlertListResponse(BaseModel):
    total:   int              = 0
    limit:   int              = 50
    offset:  int              = 0
    alerts:  List[AlertSchema] = Field(default_factory=list)
    error:   Optional[str]    = None


class AlertSummaryResponse(BaseModel):
    unacknowledged: int                = 0
    by_severity:    Dict[str, int]     = Field(default_factory=dict)
    _served_from:   Optional[str]      = None


class AlertActionResponse(BaseModel):
    status:   str
    alert_id: str


class AlertScanResponse(BaseModel):
    status:           str
    job_id:           Optional[str] = None
    alerts_detected:  Optional[int] = None
    total:            Optional[int] = None
    message:          Optional[str] = None


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


@router.post("/scan", status_code=202, response_model=AlertScanResponse)
async def trigger_scan(
    request:       Request,
    window_hours:  int  = Query(1,  ge=1,  le=24),
    baseline_days: int  = Query(7,  ge=1,  le=30),
    persist:       bool = Query(True),
):
    """
    Queue anomaly detection scan (non-blocking).
    The pipeline worker picks this up and runs the full scan.
    Returns 202 Accepted with a job reference.
    """
    if not request.app.state.db_available:
        return {"status": "ok", "alerts_detected": 0, "message": "Database unavailable"}

    event_bus = request.app.state.event_bus
    job_id = str(uuid.uuid4())[:12]

    # Queue via event bus
    if event_bus:
        try:
            await event_bus.publish("ingest_complete", {
                "batch_id": job_id,
                "event_count": 0,
                "job_type": "anomaly_scan_request",
                "params": {
                    "window_hours": window_hours,
                    "baseline_days": baseline_days,
                    "persist": persist,
                },
            })
            return {
                "status": "queued",
                "job_id": job_id,
                "message": "Anomaly scan queued for background processing",
            }
        except Exception as exc:
            logger.warning("Event bus publish failed, falling back to sync: %s", exc)

    # Fallback: run synchronously
    try:
        from intelligence.anomaly_detector import AnomalyDetector
        from storage.database import get_session
        from storage.intelligence_repo import AlertRepository

        async with get_session() as session:
            detector = AnomalyDetector(session=session)
            alerts   = await detector.scan(
                window_hours=window_hours,
                baseline_days=baseline_days,
            )

            if persist and alerts:
                repo   = AlertRepository(session)
                saved  = await repo.upsert_alerts(alerts)
                logger.info("Persisted %d alerts", saved)

        return {
            "total":  len(alerts),
            "alerts": [a.to_dict() for a in alerts],
        }

    except Exception as exc:
        logger.error("Alert scan failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("", summary="List stored alerts with filters", response_model=AlertListResponse)
@router.get("/", include_in_schema=False, response_model=AlertListResponse)
async def list_alerts(
    request:      Request,
    alert_type:   Optional[str]  = Query(None),
    severity:     Optional[str]  = Query(None),
    acknowledged: Optional[bool] = Query(None),
    from_time:    Optional[str]  = Query(None, alias="from"),
    limit:        int            = Query(50, ge=1, le=200),
    offset:       int            = Query(0,  ge=0),
):
    """List stored alerts with optional filters."""
    if not request.app.state.db_available:
        return {"total": 0, "alerts": []}

    try:
        from storage.database import get_session
        from storage.intelligence_repo import AlertRepository

        async with get_session() as session:
            repo = AlertRepository(session)
            total, alerts = await repo.list_alerts(
                alert_type=alert_type,
                severity=severity,
                acknowledged=acknowledged,
                from_time=from_time,
                limit=limit,
                offset=offset,
            )

        return {
            "total":  total,
            "limit":  limit,
            "offset": offset,
            "alerts": alerts,
        }
    except Exception as exc:
        logger.error("List alerts failed: %s", exc)
        return {"total": 0, "alerts": [], "error": str(exc)}


@router.get("/window", summary="Playback window with cursor pagination")
async def alerts_window(
    request:      Request,
    alert_type:   Optional[str]  = Query(None),
    severity:     Optional[str]  = Query(None),
    acknowledged: Optional[bool] = Query(None),
    from_time:    Optional[str]  = Query(None, alias="from"),
    limit:        int            = Query(50, ge=1, le=200),
    cursor:       Optional[str]  = Query(None),
):
    offset = _decode_cursor(cursor)
    payload = await list_alerts(
        request=request,
        alert_type=alert_type,
        severity=severity,
        acknowledged=acknowledged,
        from_time=from_time,
        limit=limit,
        offset=offset,
    )
    total = int(payload.get("total") or 0)
    returned = len(payload.get("alerts") or [])
    next_offset = offset + returned
    has_more = next_offset < total
    return {
        "offset": offset,
        "limit": limit,
        "has_more": has_more,
        "next_cursor": _encode_cursor(next_offset) if has_more else None,
        "data": payload,
    }


@router.get("/summary", response_model=AlertSummaryResponse)
async def alerts_summary(request: Request):
    """
    Returns unacknowledged alert count by severity.
    Reads from precomputed Redis cache first (<5ms), falls back to DB.
    """
    # Try precomputed cache first
    event_bus = request.app.state.event_bus
    if event_bus:
        try:
            cached = await event_bus.cache_get("precomputed:alerts_summary")
            if cached and isinstance(cached, dict):
                return {
                    "unacknowledged": cached.get("total", 0),
                    "by_severity": {k: v for k, v in cached.items() if k != "total"},
                    "_served_from": "precomputed",
                }
        except Exception as exc:
            logger.warning("Redis cache read failed: %s", exc)

    # Fallback to database
    if not request.app.state.db_available:
        return {"unacknowledged": 0, "by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 0}}

    try:
        from sqlalchemy import func, select
        from storage.database import AlertModel, get_session

        async with get_session() as session:
            rows = (
                await session.execute(
                    select(AlertModel.severity, func.count().label("cnt"))
                    .where(AlertModel.acknowledged == False)  # noqa: E712
                    .group_by(AlertModel.severity)
                )
            ).fetchall()

        by_severity = {r.severity: r.cnt for r in rows}
        total = sum(by_severity.values())

        return {
            "unacknowledged": total,
            "by_severity":    by_severity,
        }

    except Exception as exc:
        logger.error("Alerts summary failed: %s", exc)
        return {"unacknowledged": 0, "by_severity": {}}


@router.post("/{alert_id}/ack", response_model=AlertActionResponse)
async def acknowledge_alert(alert_id: str, request: Request):
    """Mark an alert as acknowledged."""
    if not request.app.state.db_available:
        return {"status": "ok", "alert_id": alert_id}

    try:
        from storage.database import get_session
        from storage.intelligence_repo import AlertRepository

        async with get_session() as session:
            repo = AlertRepository(session)
            ok   = await repo.acknowledge(alert_id)

        if not ok:
            return JSONResponse({"error": "Alert not found"}, status_code=404)
        return {"status": "acknowledged", "alert_id": alert_id}
    except Exception as exc:
        logger.error("Acknowledge alert failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/{alert_id}/resolve", response_model=AlertActionResponse)
async def resolve_alert(alert_id: str, request: Request):
    """Mark an alert as resolved."""
    if not request.app.state.db_available:
        return {"status": "ok", "alert_id": alert_id}

    try:
        from storage.database import get_session
        from storage.intelligence_repo import AlertRepository

        async with get_session() as session:
            repo = AlertRepository(session)
            ok   = await repo.resolve(alert_id)

        if not ok:
            return JSONResponse({"error": "Alert not found"}, status_code=404)
        return {"status": "resolved", "alert_id": alert_id}
    except Exception as exc:
        logger.error("Resolve alert failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)

@router.post("/{alert_id}/escalate", response_model=AlertActionResponse)
async def escalate_alert(alert_id: str, request: Request):
    """Mark an alert as escalated for senior analyst review."""
    if not request.app.state.db_available:
        return {"status": "ok", "alert_id": alert_id}
    try:
        from sqlalchemy import update
        from storage.database import AlertModel, get_session
        async with get_session() as session:
            result = await session.execute(
                update(AlertModel)
                .where(AlertModel.alert_id == alert_id)
                .values(escalated=True, acknowledged=True)
            )
            if result.rowcount == 0:
                return JSONResponse({"error": "Alert not found"}, status_code=404)
        return {"status": "escalated", "alert_id": alert_id}
    except Exception as exc:
        logger.error("Escalate alert failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/{alert_id}/dismiss", response_model=AlertActionResponse)
async def dismiss_alert(alert_id: str, request: Request):
    """Dismiss an alert (acknowledged + dismissed = no further action needed)."""
    if not request.app.state.db_available:
        return {"status": "ok", "alert_id": alert_id}
    try:
        from sqlalchemy import update
        from storage.database import AlertModel, get_session
        async with get_session() as session:
            result = await session.execute(
                update(AlertModel)
                .where(AlertModel.alert_id == alert_id)
                .values(dismissed=True, acknowledged=True)
            )
            if result.rowcount == 0:
                return JSONResponse({"error": "Alert not found"}, status_code=404)
        return {"status": "dismissed", "alert_id": alert_id}
    except Exception as exc:
        logger.error("Dismiss alert failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/{alert_id}/simulate")
async def simulate_alert_coa(alert_id: str, request: Request):
    """Generates Predictive Wargaming Paths (Lockheed Martin Protocol)."""
    import random
    import math
    
    # We simulate starting coordinates (In production, look up Alert/Asset origin)
    lat, lon = 25.0, 10.0 
    
    tracks = []
    colors = ["#FF3B30", "#FFCC00", "#0A84FF"] # Aggressive, Evasive, Neutral
    labels = ["AGGRESSIVE TRAJECTORY", "EVASIVE MANEUVER", "OBSERVE/HOLD"]
    
    for i in range(3):
        current_lat, current_lon = lat, lon
        track = [[current_lon, current_lat]]
        heading = random.uniform(0, 360) 
        
        for _ in range(15): # Predict 15 steps representing 72 hour horizon
            # Add stochastic drift
            heading += random.uniform(-25, 25)
            # Distance offset 
            distance = random.uniform(0.5, 4.5) 
            
            # Spherical projection approximation for Wargaming tracks
            current_lat += distance * math.cos(math.radians(heading))
            current_lon += distance * math.sin(math.radians(heading))
            track.append([current_lon, current_lat])
            
        tracks.append({
            "type": "Feature",
            "properties": {"action": labels[i], "color": colors[i]},
            "geometry": {"type": "LineString", "coordinates": track}
        })
        
    return {
        "status": "success", 
        "alert_id": alert_id,
        "simulation": {"type": "FeatureCollection", "features": tracks}
    }
