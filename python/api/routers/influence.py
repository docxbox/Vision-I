"""
api/routers/influence.py
------------------------
Influence + propaganda endpoints (actor influence, herd mentality, campaigns).
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from intelligence.influence_scorer import InfluenceScorer
from intelligence.propaganda_detector import detect_propaganda
from storage.database import get_session

logger = logging.getLogger("vision_i.api.influence")
router = APIRouter(tags=["influence"])

# ── Response schemas ───────────────────────────────────────────────────────

class InfluenceActorsResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    generated_at: str = ""
    source: str = ""
    actors: List[Any] = Field(default_factory=list)

class PropagandaResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    generated_at: str = ""
    campaigns: List[Any] = Field(default_factory=list)
    type: Optional[str] = None


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cache_key(name: str, payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str)
    return f"influence:cache:{name}:" + hashlib.md5(raw.encode("utf-8")).hexdigest()


def _cache_ttl(request: Request, default_ttl: int = 30) -> int:
    raw = request.query_params.get("cache_ttl")
    if raw is None:
        return default_ttl
    try:
        ttl = int(raw)
    except ValueError:
        return 0
    return max(0, min(ttl, 600))


@router.get("/actors", summary="Top actors by influence score", response_model=InfluenceActorsResponse)
async def influence_actors(
    request: Request,
    limit: int = Query(20, ge=5, le=200),
    entity_type: Optional[str] = Query(None),
    window_days: int = Query(30, ge=1, le=365),
    min_mentions: int = Query(3, ge=1, le=20),
):
    if not request.app.state.db_available:
        return {"generated_at": _utcnow_iso(), "actors": [], "note": "Database unavailable"}

    cache_ttl = _cache_ttl(request, default_ttl=30)
    event_bus = request.app.state.event_bus
    cache_key = _cache_key("actors", {
        "limit": limit,
        "entity_type": entity_type,
        "window_days": window_days,
        "min_mentions": min_mentions,
    })

    if cache_ttl > 0 and event_bus:
        cached = await event_bus.cache_get(cache_key)
        if cached:
            cached["_cached"] = True
            return cached

    async with get_session() as session:
        scorer = InfluenceScorer(session=session, graph=request.app.state.graph)

        if request.app.state.graph.available:
            actors = await scorer.get_top_influencers(limit=limit, entity_type=entity_type)
            payload = {
                "generated_at": _utcnow_iso(),
                "source": "neo4j",
                "actors": actors,
            }
        else:
            scores = await scorer.compute_scores_only(
                top_k=limit,
                window_days=window_days,
                min_mentions=min_mentions,
            )
            actors = [
                {"name": name, "score": score}
                for name, score in scores.items()
            ]
            payload = {
                "generated_at": _utcnow_iso(),
                "source": "events",
                "actors": actors,
                "note": "Neo4j unavailable - using event-frequency heuristic.",
            }

    if cache_ttl > 0 and event_bus:
        await event_bus.cache_set(cache_key, payload, ttl_seconds=cache_ttl)
    return payload


@router.get("/propaganda", summary="Detect coordinated messaging campaigns", response_model=PropagandaResponse)
async def influence_propaganda(
    request: Request,
    window_hours: int = Query(6, ge=1, le=72),
    min_count: int = Query(4, ge=2, le=50),
    min_sources: int = Query(2, ge=1, le=10),
):
    if not request.app.state.db_available:
        return {"generated_at": _utcnow_iso(), "campaigns": [], "note": "Database unavailable"}

    cache_ttl = _cache_ttl(request, default_ttl=45)
    event_bus = request.app.state.event_bus
    cache_key = _cache_key("propaganda", {
        "window_hours": window_hours,
        "min_count": min_count,
        "min_sources": min_sources,
    })

    if cache_ttl > 0 and event_bus:
        cached = await event_bus.cache_get(cache_key)
        if cached:
            cached["_cached"] = True
            return cached

    payload = await detect_propaganda(
        window_hours=window_hours,
        min_count=min_count,
        min_sources=min_sources,
    )
    payload["type"] = "propaganda"

    if cache_ttl > 0 and event_bus:
        await event_bus.cache_set(cache_key, payload, ttl_seconds=cache_ttl)
    return payload


@router.get("/herd", summary="Detect herd mentality / narrative convergence", response_model=PropagandaResponse)
async def influence_herd(
    request: Request,
    window_hours: int = Query(6, ge=1, le=72),
    min_count: int = Query(6, ge=2, le=50),
    min_sources: int = Query(3, ge=1, le=10),
):
    if not request.app.state.db_available:
        return {"generated_at": _utcnow_iso(), "campaigns": [], "note": "Database unavailable"}

    cache_ttl = _cache_ttl(request, default_ttl=45)
    event_bus = request.app.state.event_bus
    cache_key = _cache_key("herd", {
        "window_hours": window_hours,
        "min_count": min_count,
        "min_sources": min_sources,
    })

    if cache_ttl > 0 and event_bus:
        cached = await event_bus.cache_get(cache_key)
        if cached:
            cached["_cached"] = True
            return cached

    payload = await detect_propaganda(
        window_hours=window_hours,
        min_count=min_count,
        min_sources=min_sources,
    )
    payload["type"] = "herd"
    payload["note"] = "Herd detection uses coordinated-title signatures as a proxy."

    if cache_ttl > 0 and event_bus:
        await event_bus.cache_set(cache_key, payload, ttl_seconds=cache_ttl)
    return payload
