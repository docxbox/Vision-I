"""
api/routers/airspace.py
-----------------------
Airspace intelligence endpoints: closures, reroutes, jamming heatmaps, satellites.
"""

import hashlib
import json
import logging
from typing import Optional

import asyncio
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict

from config.settings import settings

# ── Response schemas ───────────────────────────────────────────────────────

class AirspaceClosuresResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

class AirspaceReroutesResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

class JammingHeatmapResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

class SatellitePassesResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
from intelligence.airspace import (
    build_airspace_response,
    detect_airspace_closures,
    fetch_notams,
)
from intelligence.jamming_detector import detect_jamming_heatmap
from intelligence.reroute_detector import detect_reroutes
from intelligence.satellite_tracker import compute_passes

logger = logging.getLogger("vision_i.api.airspace")
router = APIRouter(tags=["airspace"])


def _cache_key(name: str, payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str)
    return f"airspace:cache:{name}:" + hashlib.md5(raw.encode("utf-8")).hexdigest()


def _cache_ttl(request: Request, default_ttl: int = 30) -> int:
    raw = request.query_params.get("cache_ttl")
    if raw is None:
        return default_ttl
    try:
        ttl = int(raw)
    except ValueError:
        return 0
    return max(0, min(ttl, 600))


@router.get("/closures", summary="Airspace closures / no-fly zones (NOTAM)", response_model=AirspaceClosuresResponse)
async def airspace_closures(
    request: Request,
    limit: int = Query(200, ge=1, le=500),
    lat_min: Optional[float] = Query(None),
    lon_min: Optional[float] = Query(None),
    lat_max: Optional[float] = Query(None),
    lon_max: Optional[float] = Query(None),
):
    bbox = (lat_min, lon_min, lat_max, lon_max) \
        if all(v is not None for v in [lat_min, lon_min, lat_max, lon_max]) \
        else None

    cache_ttl = _cache_ttl(request, default_ttl=45)
    event_bus = request.app.state.event_bus
    cache_key = _cache_key("closures", {"bbox": bbox, "limit": limit})

    if cache_ttl > 0 and event_bus:
        cached = await event_bus.cache_get(cache_key)
        if cached:
            cached["_cached"] = True
            return cached

    def _work():
        notams = fetch_notams(bbox=bbox, limit=limit)
        closures = detect_airspace_closures(notams)
        return notams, closures

    try:
        loop = asyncio.get_running_loop()
        notams, closures = await asyncio.wait_for(
            loop.run_in_executor(None, _work),
            timeout=4.0,
        )
    except asyncio.TimeoutError:
        return build_airspace_response(
            [],
            note="Airspace closures timed out (NOTAM provider slow/unavailable).",
        )

    note = None
    if not notams and not (settings.notam_feed_path or settings.notam_api_url):
        note = "No NOTAM provider configured. Set NOTAM_FEED_PATH or NOTAM_API_URL."

    payload = build_airspace_response(closures, note=note)
    if cache_ttl > 0 and event_bus:
        await event_bus.cache_set(cache_key, payload, ttl_seconds=cache_ttl)
    return payload


@router.get("/reroutes", summary="Detect rerouting / holding patterns (heuristic)", response_model=AirspaceReroutesResponse)
async def airspace_reroutes(
    request: Request,
    window_hours: int = Query(6, ge=1, le=72),
    min_turn_deg: float = Query(60.0, ge=10.0, le=180.0),
    min_history: int = Query(2, ge=2, le=10),
    limit: int = Query(50, ge=1, le=200),
):
    if not request.app.state.db_available:
        return {"generated_at": None, "total": 0, "events": [], "note": "Database unavailable"}

    cache_ttl = _cache_ttl(request, default_ttl=20)
    event_bus = request.app.state.event_bus
    cache_key = _cache_key("reroutes", {
        "window_hours": window_hours,
        "min_turn_deg": min_turn_deg,
        "min_history": min_history,
        "limit": limit,
    })

    if cache_ttl > 0 and event_bus:
        cached = await event_bus.cache_get(cache_key)
        if cached:
            cached["_cached"] = True
            return cached

    payload = await detect_reroutes(
        window_hours=window_hours,
        min_turn_deg=min_turn_deg,
        min_history=min_history,
        limit=limit,
    )

    if cache_ttl > 0 and event_bus:
        await event_bus.cache_set(cache_key, payload, ttl_seconds=cache_ttl)
    return payload


@router.get("/jamming-heatmap", summary="GPS jamming heuristic heatmap", response_model=JammingHeatmapResponse)
async def jamming_heatmap(
    request: Request,
    window_hours: int = Query(3, ge=1, le=24),
    tile_size_deg: float = Query(1.0, ge=0.1, le=5.0),
    min_count: int = Query(3, ge=1, le=50),
):
    if not request.app.state.db_available:
        return {"generated_at": None, "window_hours": window_hours, "tiles": [], "note": "Database unavailable"}

    cache_ttl = _cache_ttl(request, default_ttl=20)
    event_bus = request.app.state.event_bus
    cache_key = _cache_key("jamming", {
        "window_hours": window_hours,
        "tile_size_deg": tile_size_deg,
        "min_count": min_count,
    })

    if cache_ttl > 0 and event_bus:
        cached = await event_bus.cache_get(cache_key)
        if cached:
            cached["_cached"] = True
            return cached

    payload = await detect_jamming_heatmap(
        window_hours=window_hours,
        tile_size_deg=tile_size_deg,
        min_count=min_count,
    )

    if cache_ttl > 0 and event_bus:
        await event_bus.cache_set(cache_key, payload, ttl_seconds=cache_ttl)
    return payload


@router.get("/satellite-passes", summary="Satellite pass correlation (requires TLEs)", response_model=SatellitePassesResponse)
async def satellite_passes(
    request: Request,
    lat_min: float = Query(...),
    lon_min: float = Query(...),
    lat_max: float = Query(...),
    lon_max: float = Query(...),
    hours_ahead: int = Query(6, ge=1, le=24),
    step_seconds: int = Query(60, ge=10, le=600),
    limit: int = Query(50, ge=1, le=200),
):
    bbox = (lat_min, lon_min, lat_max, lon_max)

    cache_ttl = _cache_ttl(request, default_ttl=60)
    event_bus = request.app.state.event_bus
    cache_key = _cache_key("satellite", {
        "bbox": bbox,
        "hours_ahead": hours_ahead,
        "step_seconds": step_seconds,
        "limit": limit,
    })

    if cache_ttl > 0 and event_bus:
        cached = await event_bus.cache_get(cache_key)
        if cached:
            cached["_cached"] = True
            return cached

    def _work():
        return compute_passes(
            bbox=bbox,
            hours_ahead=hours_ahead,
            step_seconds=step_seconds,
            limit=limit,
        )

    try:
        loop = asyncio.get_running_loop()
        payload = await asyncio.wait_for(
            loop.run_in_executor(None, _work),
            timeout=4.0,
        )
    except asyncio.TimeoutError:
        payload = {
            "generated_at": None,
            "total": 0,
            "passes": [],
            "note": "Satellite pass computation timed out (TLE provider slow/unavailable).",
        }

    if cache_ttl > 0 and event_bus:
        await event_bus.cache_set(cache_key, payload, ttl_seconds=cache_ttl)
    return payload

