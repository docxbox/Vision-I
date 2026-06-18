"""
api/routers/event_detail.py
---------------------------
Singular event-centric war-room endpoint.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vision_i.api.event_detail")
router = APIRouter(tags=["Event Intelligence"])

# ── Response schemas ───────────────────────────────────────────────────────

class EventIntelligenceSchema(BaseModel):
    model_config = ConfigDict(extra="allow")

class EventContextSchema(BaseModel):
    model_config = ConfigDict(extra="allow")
    event: Dict[str, Any] = Field(default_factory=dict)
    situation: Dict[str, Any] = Field(default_factory=dict)
    divergence: Dict[str, Any] = Field(default_factory=dict)
    amplification: Dict[str, Any] = Field(default_factory=dict)
    actors: List[Any] = Field(default_factory=list)
    related_events: List[Any] = Field(default_factory=list)
    narratives: List[Any] = Field(default_factory=list)
    alerts: List[Any] = Field(default_factory=list)
    playbooks: List[Any] = Field(default_factory=list)
    forecast: Optional[Dict[str, Any]] = None


@router.get("/event/{event_id}", summary="Event-centric intelligence view", response_model=EventIntelligenceSchema)
async def get_event_intelligence(event_id: str, request: Request):
    if not request.app.state.db_available:
        raise HTTPException(status_code=503, detail="Event intelligence requires PostgreSQL")

    try:
        from intelligence.event_intelligence import EventIntelligenceService
        from storage.database import get_session

        async with get_session() as session:
            payload = await EventIntelligenceService(session).build_event_view(event_id)
        if not payload:
            raise HTTPException(status_code=404, detail=f"Event '{event_id}' not found")
        return payload
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("get_event_intelligence failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/event/{event_id}/context", summary="Decision-OS context envelope", response_model=EventContextSchema)
async def get_event_context(event_id: str, request: Request):
    """
    Returns the report §10 context envelope for an event:
        {
          event: {...},
          situation: {...},
          divergence: {...},
          amplification: {...},
          actors: [...],
          related_events: [...],
          narratives: [...],
          alerts: [...],
          playbooks: [...],
          forecast: {...}
        }
    """
    if not request.app.state.db_available:
        raise HTTPException(status_code=503, detail="Event context requires PostgreSQL")

    try:
        from intelligence.event_intelligence import EventIntelligenceService
        from intelligence.predictor import forecast_series
        from playbook import PlaybookEngine
        from storage.database import get_session

        import asyncio
        async with get_session() as session:
            base = await asyncio.wait_for(
                EventIntelligenceService(session).build_event_view(event_id),
                timeout=15.0,
            )
        if not base:
            raise HTTPException(status_code=404, detail=f"Event '{event_id}' not found")

        narratives = base.get("narratives") or []
        alerts = base.get("alerts") or []
        actors = base.get("actors") or []
        related_events = base.get("related_events") or base.get("related") or []
        situation = base.get("situation") or {}
        event_payload = base.get("event") or {}

        # Divergence: spread of sentiment / source families across the related cluster.
        sentiments = [r.get("sentiment_score") for r in related_events if r.get("sentiment_score") is not None]
        if sentiments:
            mean = sum(sentiments) / len(sentiments)
            variance = sum((s - mean) ** 2 for s in sentiments) / len(sentiments)
            divergence_score = round(min(variance, 1.0), 4)
        else:
            divergence_score = 0.0

        amplification = {
            "source_families": len({r.get("source") for r in related_events if r.get("source")}),
            "event_count": len(related_events),
            "narrative_strength": max((n.get("strength", 0.0) for n in narratives), default=0.0),
            "tier": "viral" if len(related_events) >= 25 else "trending" if len(related_events) >= 8 else "normal",
        }

        # Playbook matches.
        engine = PlaybookEngine()
        ctx = {
            "event_type": event_payload.get("event_type"),
            "severity": situation.get("severity"),
            "priority_score": situation.get("priority_score", 0.0),
            "risk_score": situation.get("risk_score", 0.0),
            "confidence": event_payload.get("confidence_score", 0.0),
            "narrative_strength": amplification["narrative_strength"],
            "signal_type": (narratives[0].get("signal_type") if narratives else None),
        }
        playbook_matches = [p.to_dict() for p in engine.matches(ctx)]

        # Strength forecast on the strongest narrative attached to the event.
        forecast_payload = None
        if narratives:
            history = narratives[0].get("strength_history") or []
            try:
                forecast = forecast_series([float(v) for v in history], horizon=12)
                forecast_payload = {
                    "narrative_id": narratives[0].get("narrative_id"),
                    "method": forecast.method,
                    "confidence": forecast.confidence,
                    "history": forecast.history,
                    "forecast": forecast.forecast,
                    "lower": forecast.lower,
                    "upper": forecast.upper,
                }
            except Exception as exc:
                logger.warning("forecast in /context failed: %s", exc)

        return {
            "event": event_payload,
            "situation": situation,
            "divergence": {"score": divergence_score, "samples": len(sentiments)},
            "amplification": amplification,
            "actors": actors,
            "related_events": related_events,
            "narratives": narratives,
            "alerts": alerts,
            "playbooks": playbook_matches,
            "forecast": forecast_payload,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("get_event_context failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
