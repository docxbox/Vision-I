"""
api/routers/threatboard.py
──────────────────────────
Global threat assessment board.

  GET /threatboard          — aggregated threat zones with severity, score, trend
  GET /threatboard/summary  — quick counts by level (cached)
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select, text

from storage.database import AlertModel, NarrativeModel, get_session
from core.utils import utcnow_iso

logger = logging.getLogger("vision_i.api.threatboard")
router = APIRouter(tags=["threatboard"])


# ── Response schemas ─────────────────────────────────────────────────────────

class ThreatZoneSchema(BaseModel):
    name:              str             = ""
    threat_level:      str             = "monitoring"
    dominant_severity: str             = "medium"
    score:             float           = 0.0
    trend:             str             = "stable"
    alert_count:       int             = 0
    narrative_count:   int             = 0
    event_count:       int             = 0
    top_signals:       List[str]       = Field(default_factory=list)
    top_actors:        List[str]       = Field(default_factory=list)
    location:          Optional[str]   = None


class ThreatSummarySchema(BaseModel):
    critical:   int = 0
    high:       int = 0
    medium:     int = 0
    low:        int = 0
    monitoring: int = 0


class ThreatBoardResponse(BaseModel):
    generated_at:  str
    hours:         int
    overall_level: str
    zones:         List[ThreatZoneSchema]  = Field(default_factory=list)
    summary:       ThreatSummarySchema     = Field(default_factory=ThreatSummarySchema)
    db_available:  Optional[bool]          = None
    error:         Optional[str]           = None


class ThreatBoardSummaryResponse(BaseModel):
    overall_level: str
    summary:       ThreatSummarySchema     = Field(default_factory=ThreatSummarySchema)
    zone_count:    int                     = 0
    generated_at:  str


def _threat_level(score: float) -> str:
    if score >= 15:
        return "critical"
    if score >= 8:
        return "high"
    if score >= 3:
        return "medium"
    if score >= 1:
        return "low"
    return "monitoring"


def _overall_level(zones: List[Dict]) -> str:
    if not zones:
        return "monitoring"
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for z in zones:
        lvl = z.get("threat_level", "monitoring")
        if lvl in counts:
            counts[lvl] += 1
    if counts["critical"] >= 1:
        return "critical"
    if counts["high"] >= 2:
        return "high"
    if counts["high"] >= 1 or counts["medium"] >= 3:
        return "elevated"
    return "normal"


@router.get("", response_model=ThreatBoardResponse)
async def get_threatboard(
    request: Request,
    hours: int = Query(24, ge=1, le=168, description="Look-back window in hours"),
    limit: int = Query(30, ge=1, le=100),
):
    """
    Aggregate alerts + narrative signals into per-zone threat assessment.
    Each zone = a unique (entity OR topic) with score, level, trend, top signals.
    """
    if not getattr(request.app.state, "db_available", False):
        return {
            "generated_at": utcnow_iso(),
            "hours": hours,
            "overall_level": "monitoring",
            "zones": [],
            "summary": {"critical": 0, "high": 0, "medium": 0, "low": 0, "monitoring": 0},
            "db_available": False,
        }

    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=hours)
    prev_start   = now - timedelta(hours=hours * 2)  # previous period for trend

    zones: Dict[str, Dict[str, Any]] = {}

    try:
        async with get_session() as session:
            # ── Alerts ──────────────────────────────────────────────────
            alert_rows = (await session.execute(
                select(
                    AlertModel.entity,
                    AlertModel.alert_type,
                    AlertModel.severity,
                    AlertModel.z_score,
                    AlertModel.location,
                    AlertModel.detected_at,
                )
                .where(and_(
                    AlertModel.detected_at >= window_start,
                    AlertModel.resolved_at.is_(None),
                ))
                .order_by(AlertModel.detected_at.desc())
                .limit(500)
            )).all()

            # Prev-period alert counts per entity for trend calc
            prev_alert_counts: Dict[str, int] = {}
            prev_rows = (await session.execute(
                select(AlertModel.entity, func.count().label("cnt"))
                .where(and_(
                    AlertModel.detected_at >= prev_start,
                    AlertModel.detected_at < window_start,
                ))
                .group_by(AlertModel.entity)
            )).all()
            for row in prev_rows:
                if row.entity:
                    prev_alert_counts[row.entity] = row.cnt

            for row in alert_rows:
                key = (row.entity or row.location or "unknown").strip()
                if not key or key == "unknown":
                    continue
                if key not in zones:
                    zones[key] = {
                        "name": key,
                        "alert_count": 0,
                        "narrative_count": 0,
                        "event_count": 0,
                        "score": 0.0,
                        "top_signals": [],
                        "top_actors": [],
                        "location": row.location,
                        "severities": [],
                    }
                z = zones[key]
                z["alert_count"] += 1
                z["severities"].append(row.severity or "medium")
                sev_score = {"critical": 3, "high": 2, "medium": 1, "low": 0.5}.get(
                    (row.severity or "medium").lower(), 0.5
                )
                z["score"] += sev_score + max(0, row.z_score or 0) * 0.3
                sig = row.alert_type or "anomaly"
                if sig not in z["top_signals"]:
                    z["top_signals"].append(sig)

            # ── Narrative signals ────────────────────────────────────────
            narrative_rows = (await session.execute(
                select(
                    NarrativeModel.topic,
                    NarrativeModel.signal_type,
                    NarrativeModel.severity,
                    NarrativeModel.strength,
                    NarrativeModel.actors,
                    NarrativeModel.detected_at,
                )
                .where(NarrativeModel.detected_at >= window_start)
                .order_by(NarrativeModel.detected_at.desc())
                .limit(300)
            )).all()

            for row in narrative_rows:
                key = (row.topic or "unknown").strip()
                if not key or key == "unknown":
                    continue
                if key not in zones:
                    zones[key] = {
                        "name": key,
                        "alert_count": 0,
                        "narrative_count": 0,
                        "event_count": 0,
                        "score": 0.0,
                        "top_signals": [],
                        "top_actors": [],
                        "location": None,
                        "severities": [],
                    }
                z = zones[key]
                z["narrative_count"] += 1
                z["severities"].append(row.severity or "medium")
                sev_score = {"critical": 3, "high": 2, "medium": 1, "low": 0.5}.get(
                    (row.severity or "medium").lower(), 0.5
                )
                z["score"] += (row.strength or 0.5) * sev_score * 1.5
                sig = row.signal_type or "narrative"
                if sig not in z["top_signals"]:
                    z["top_signals"].append(sig)
                if row.actors:
                    for actor in (row.actors or [])[:3]:
                        if actor not in z["top_actors"]:
                            z["top_actors"].append(actor)

    except Exception as exc:
        logger.warning("Threatboard DB query failed: %s", exc)
        return {
            "generated_at": utcnow_iso(),
            "hours": hours,
            "overall_level": "monitoring",
            "zones": [],
            "summary": {"critical": 0, "high": 0, "medium": 0, "low": 0, "monitoring": 0},
            "error": str(exc),
        }

    # ── Finalise zones ───────────────────────────────────────────────────────
    result_zones = []
    for key, z in zones.items():
        level = _threat_level(z["score"])
        prev_count = prev_alert_counts.get(key, 0)
        curr_count = z["alert_count"]
        if prev_count == 0:
            trend = "new" if curr_count > 0 else "stable"
        elif curr_count > prev_count * 1.3:
            trend = "rising"
        elif curr_count < prev_count * 0.7:
            trend = "falling"
        else:
            trend = "stable"

        # Dominant severity
        sev_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for s in z["severities"]:
            k = s.lower()
            if k in sev_counts:
                sev_counts[k] += 1
        dom_sev = max(sev_counts, key=sev_counts.get) if any(sev_counts.values()) else "medium"

        result_zones.append({
            "name":            key,
            "threat_level":    level,
            "dominant_severity": dom_sev,
            "score":           round(z["score"], 2),
            "trend":           trend,
            "alert_count":     z["alert_count"],
            "narrative_count": z["narrative_count"],
            "event_count":     z["event_count"],
            "top_signals":     z["top_signals"][:4],
            "top_actors":      z["top_actors"][:5],
            "location":        z["location"],
        })

    result_zones.sort(key=lambda x: x["score"], reverse=True)
    result_zones = result_zones[:limit]

    summary = {"critical": 0, "high": 0, "medium": 0, "low": 0, "monitoring": 0}
    for z in result_zones:
        lvl = z["threat_level"]
        if lvl in summary:
            summary[lvl] += 1
        else:
            summary["monitoring"] += 1

    return {
        "generated_at":  utcnow_iso(),
        "hours":         hours,
        "overall_level": _overall_level(result_zones),
        "zones":         result_zones,
        "summary":       summary,
    }


@router.get("/summary", response_model=ThreatBoardSummaryResponse)
async def get_threatboard_summary(request: Request):
    """Quick threat count summary — served from Redis cache when available."""
    event_bus = getattr(request.app.state, "event_bus", None)
    if event_bus:
        try:
            cached = await event_bus.cache_get("precomputed:threatboard_summary")
            if cached:
                import json
                return json.loads(cached) if isinstance(cached, str) else cached
        except Exception:
            pass

    # Fallback: live query
    result = await get_threatboard(request, hours=24, limit=30)
    summary = result.get("summary", {})
    return {
        "overall_level": result.get("overall_level", "monitoring"),
        "summary":       summary,
        "zone_count":    len(result.get("zones", [])),
        "generated_at":  utcnow_iso(),
    }
