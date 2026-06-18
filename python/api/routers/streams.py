"""
api/routers/streams.py
----------------------
GET /streams/live - latest events from live sources (USGS, OpenSky, Stocks).

ARCHITECTURE: Reads from precomputed Redis cache (written by live_ingest_job
every 15 min). Falls back to in-memory cache, then returns empty degraded
payload. Live fetch removed from request path - was 15s+ and blocked workers.
Target: <200ms response time.
"""

import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("vision_i.api.streams")
router = APIRouter(tags=["Streams"])

# ── Response schemas ───────────────────────────────────────────────────────

class LiveStreamsResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    total: int = 0
    source_counts: Dict[str, Any] = Field(default_factory=dict)
    source_errors: Dict[str, Any] = Field(default_factory=dict)
    events: List[Any] = Field(default_factory=list)
_fallback_cache: Dict[str, dict] = {}
_fallback_ts: Dict[str, float] = {}
_FALLBACK_TTL = 25


def _cache_key(limit: int, sources: Optional[str]) -> str:
    return f"live:{limit}:{sources or 'all'}"


@router.get("/live", summary="Latest events from live data sources", response_model=LiveStreamsResponse)
async def live_streams(
    request: Request,
    limit:   int            = Query(20, ge=1, le=100),
    sources: Optional[str]  = Query(
        None,
        description="Comma-separated subset: usgs,stocks,opensky  (default: all three)"
    ),
):
    key = _cache_key(limit, sources)
    event_bus = request.app.state.event_bus
    if event_bus:
        try:
            cached = await event_bus.cache_get("precomputed:live_streams")
            if cached:
                events = cached if isinstance(cached, list) else cached.get("events", [])
                if sources:
                    src_set = {s.strip() for s in sources.split(",") if s.strip()}
                    events = [e for e in events if e.get("source", "") in src_set]
                events = events[:limit]

                source_counts = {}
                for e in events:
                    s = e.get("source", "unknown")
                    source_counts[s] = source_counts.get(s, 0) + 1

                return {
                    "total": len(events),
                    "source_counts": source_counts,
                    "source_errors": {},
                    "events": events,
                    "_served_from": "precomputed",
                }
        except Exception as exc:
            logger.warning("Redis cache read failed: %s", exc)
    now = time.monotonic()
    if key in _fallback_cache and (now - _fallback_ts.get(key, 0)) < _FALLBACK_TTL:
        return _fallback_cache[key]

    # Both caches cold. Live fetch is too slow for the request path (15s+ blocks
    # Uvicorn workers). Return empty immediately - the live ingest job populates
    # precomputed:live_streams within its next run cycle.
    logger.info("live_streams cache cold - returning empty degraded payload")
    return {"total": 0, "source_counts": {}, "source_errors": {"cache": "warming"}, "events": [], "_served_from": "degraded"}


@router.get("/active", summary="Alias: latest live events", response_model=LiveStreamsResponse)
async def active_streams(
    request: Request,
    limit:   int            = Query(20, ge=1, le=100),
    sources: Optional[str]  = Query(
        None,
        description="Comma-separated subset: usgs,stocks,opensky  (default: all three)"
    ),
):
    return await live_streams(request=request, limit=limit, sources=sources)
