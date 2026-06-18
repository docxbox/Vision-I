"""
api/routers/assets.py
──────────────────────
Asset endpoints — list, detail, track history.

GET  /assets              — list tracked assets
GET  /assets/counts       — counts by type
GET  /assets/{asset_id}   — single asset detail
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("vision_i.api.assets")
router = APIRouter(tags=["Assets"])

_VESSELFINDER_REFERENCE: Dict[str, Dict[str, Any]] = {
    # User-provided VesselFinder reference for CAGLA.
    "314103000": {
        "imo": "9489417",
        "mmsi": "314103000",
        "name": "CAGLA",
        "ship_type": "Bulk Carrier",
        "ais_type": "Cargo ship",
        "flag": "Barbados",
        "built_year": "2010",
        "age": "16 years old",
        "destination": "Batumi, Georgia",
        "destination_status": "ARRIVED",
        "arrival": "May 20, 14:27 UTC",
        "last_port": "Batumi Anch., Georgia",
        "last_port_atd": "May 20, 13:01 UTC",
        "draught": "6.6 m",
        "length_beam": "180 / 30 m",
        "callsign": "8POY",
        "registry_source": "VesselFinder reference",
        "registry_url": "https://www.vesselfinder.com/vessels/details/9489417",
    },
}

# ── Response schemas ───────────────────────────────────────────────────────

class AssetItemSchema(BaseModel):
    model_config = ConfigDict(extra="allow")
    asset_id: Optional[str] = None
    asset_type: Optional[str] = None

class AssetListResponse(BaseModel):
    total: int = 0
    assets: List[Any] = Field(default_factory=list)

class AssetCountsResponse(BaseModel):
    counts: Dict[str, Any] = Field(default_factory=dict)
    total: int = 0

class AssetSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    snapshot_at: str = ""
    total: int = 0
    assets: List[Any] = Field(default_factory=list)


def _asset_profile(asset: Dict[str, Any]) -> Dict[str, Any]:
    asset_type = (asset.get("asset_type") or "asset").lower()
    display = asset.get("callsign") or asset.get("name") or asset.get("identifier") or asset.get("asset_id") or "Unknown asset"
    origin = asset.get("origin_country") or "unknown origin"
    identifier = str(asset.get("identifier") or asset.get("asset_id") or "").replace("vessel:", "").replace("aircraft:", "")
    callsign = asset.get("callsign")
    meta = asset.get("meta") or {}
    registry = _vesselfinder_reference(identifier, asset, meta) if asset_type == "vessel" else {}
    if registry:
        display = registry.get("name") or display
        callsign = callsign or registry.get("callsign")
        origin = registry.get("flag") or origin
    lat = asset.get("last_lat")
    lon = asset.get("last_lon")
    speed = asset.get("last_speed")
    altitude = asset.get("last_altitude")
    heading = asset.get("last_heading")
    seen = asset.get("last_seen")
    links: List[Dict[str, str]] = []
    image_url: Optional[str] = None
    visual_kind = asset_type

    if asset_type == "aircraft":
        status = "on the ground" if asset.get("on_ground") is True else "airborne or recently airborne"
        callsign_label = str(callsign or display or "").strip()
        if callsign_label and not callsign_label.lower().startswith("flight "):
            display = f"Flight {callsign_label}"
        description = f"OpenSky-style aviation telemetry profile for {display}"
        vertical_rate = meta.get("vertical_rate")
        squawk = meta.get("squawk")
        altitude_text = f" at {float(altitude):.0f} m" if altitude is not None else ""
        speed_text = f", moving {float(speed):.0f} kt" if speed is not None else ""
        heading_text = f" on heading {float(heading):.0f} deg" if heading is not None else ""
        extract = (
            f"{display} is an aircraft track from {origin}. It is currently {status}{altitude_text}{speed_text}{heading_text}. "
            f"Last reported position is {lat:.2f}, {lon:.2f}." if lat is not None and lon is not None
            else f"{display} is an aircraft track from {origin}. Position is not currently available."
        )
        if squawk:
            extract += f" Transponder squawk {squawk} is reported in the latest telemetry."
        if callsign:
            links.append({
                "label": "FlightAware",
                "url": f"https://www.flightaware.com/live/flight/{str(callsign).strip()}",
                "kind": "aircraft_lookup",
            })
            links.append({
                "label": "FlightRadar24",
                "url": f"https://www.flightradar24.com/{str(callsign).strip()}",
                "kind": "aircraft_lookup",
            })
        if identifier:
            links.append({
                "label": "OpenSky",
                "url": f"https://opensky-network.org/aircraft/{identifier.lower()}",
                "kind": "aircraft_lookup",
            })
    elif asset_type == "vessel":
        description = f"VesselFinder-style maritime profile for {display}"
        motion = "stationary or slow-moving" if (speed or 0) < 1 else f"moving at about {speed:.0f} kt"
        nav_status = meta.get("nav_status")
        if registry:
            registry_bits = [
                f"{display} is listed by VesselFinder as a {registry.get('ship_type', 'vessel')}",
                f"Sailing under the flag of {registry.get('flag')}" if registry.get("flag") else "",
                f"Built in {registry.get('built_year')} ({registry.get('age')})" if registry.get("built_year") else "",
            ]
            extract = ". ".join(bit for bit in registry_bits if bit) + "."
            if registry.get("destination"):
                extract += f" Voyage data shows destination {registry['destination']}"
                if registry.get("destination_status"):
                    extract += f" ({registry['destination_status']})"
                extract += "."
            extract += f" Live AIS telemetry currently reports {motion}"
            if lat is not None and lon is not None:
                extract += f" near {lat:.2f}, {lon:.2f}."
            else:
                extract += "."
        else:
            extract = (
                f"{display} is a vessel track with {origin}. It is {motion}. "
                f"Last reported position is {lat:.2f}, {lon:.2f}. "
                f"Open VesselFinder for registry dimensions, build year, port calls, and public photos." if lat is not None and lon is not None
                else f"{display} is a vessel track with {origin}. Position is not currently available."
            )
        if identifier or registry.get("imo"):
            vf_target = registry.get("imo") or identifier
            links.extend([
                {
                    "label": "VesselFinder",
                    "url": registry.get("registry_url") or f"https://www.vesselfinder.com/vessels/details/{vf_target}",
                    "kind": "maritime_registry",
                },
                {
                    "label": "MyShipTracking",
                    "url": f"https://www.myshiptracking.com/vessels?mmsi={identifier}",
                    "kind": "maritime_registry",
                },
                {
                    "label": "MarineTraffic",
                    "url": f"https://www.marinetraffic.com/en/ais/details/ships/mmsi:{identifier}",
                    "kind": "maritime_registry",
                },
            ])
        if nav_status is not None:
            meta["nav_status_label"] = _nav_status_label(nav_status)
    else:
        description = f"Tracked {asset_type} profile"
        extract = f"{display} is a tracked {asset_type} asset in the Vision-I mobility layer."

    facts = [
        {"label": "Type", "value": asset_type.upper()},
        {"label": "Identifier", "value": identifier or str(asset.get("asset_id") or "--")},
        {"label": "Origin", "value": str(origin)},
    ]
    if callsign:
        facts.append({"label": "Callsign", "value": str(callsign)})
    if asset_type == "aircraft":
        facts.append({"label": "Flight status", "value": "On ground" if asset.get("on_ground") is True else "Airborne / recent"})
        if identifier:
            facts.append({"label": "ICAO24", "value": str(identifier).lower()})
        if meta.get("squawk"):
            facts.append({"label": "Squawk", "value": str(meta.get("squawk"))})
        if meta.get("vertical_rate") is not None:
            try:
                facts.append({"label": "Vertical rate", "value": f"{float(meta.get('vertical_rate')):.0f} m/s"})
            except Exception:
                facts.append({"label": "Vertical rate", "value": str(meta.get("vertical_rate"))})
    if asset_type == "vessel" and meta.get("mmsi"):
        facts.append({"label": "MMSI", "value": str(meta.get("mmsi"))})
    if asset_type == "vessel" and registry.get("imo"):
        facts.append({"label": "IMO", "value": str(registry.get("imo"))})
    if asset_type == "vessel" and registry.get("flag"):
        facts.append({"label": "Flag", "value": str(registry.get("flag"))})
    if asset_type == "vessel" and registry.get("ship_type"):
        facts.append({"label": "Ship type", "value": str(registry.get("ship_type"))})
    if asset_type == "vessel" and registry.get("built_year"):
        facts.append({"label": "Built", "value": f"{registry.get('built_year')} ({registry.get('age', '').strip()})".strip()})
    if asset_type == "vessel" and registry.get("destination"):
        destination = str(registry.get("destination"))
        if registry.get("destination_status"):
            destination += f" / {registry.get('destination_status')}"
        facts.append({"label": "Destination", "value": destination})
    if asset_type == "vessel" and registry.get("draught"):
        facts.append({"label": "Draught", "value": str(registry.get("draught"))})
    if asset_type == "vessel" and registry.get("length_beam"):
        facts.append({"label": "Length / Beam", "value": str(registry.get("length_beam"))})
    if asset_type == "vessel" and registry.get("last_port"):
        facts.append({"label": "Last port", "value": str(registry.get("last_port"))})
    if asset_type == "vessel" and meta.get("nav_status_label"):
        facts.append({"label": "Navigation", "value": str(meta.get("nav_status_label"))})
    if speed is not None:
        facts.append({"label": "Speed", "value": f"{float(speed):.0f} kt"})
    if altitude is not None:
        facts.append({"label": "Altitude", "value": f"{float(altitude):.0f} m"})
    if heading is not None:
        facts.append({"label": "Heading", "value": f"{float(heading):.0f} deg"})
    if seen:
        facts.append({"label": "Last seen", "value": str(seen)})

    return {
        "title": display,
        "description": description,
        "extract": extract,
        "source": registry.get("registry_source") or "Vision-I mobility telemetry",
        "image_url": image_url,
        "visual_label": _visual_label(display),
        "visual_kind": visual_kind,
        "external_links": links,
        "facts": facts,
    }


def _vesselfinder_reference(identifier: str, asset: Dict[str, Any], meta: Dict[str, Any]) -> Dict[str, Any]:
    keys = {
        str(identifier or ""),
        str(meta.get("mmsi") or ""),
        str(meta.get("imo") or ""),
        str(asset.get("name") or "").strip().upper(),
    }
    for key in keys:
        if key and key in _VESSELFINDER_REFERENCE:
            return dict(_VESSELFINDER_REFERENCE[key])

    profile: Dict[str, Any] = {}
    for source_key, target_key in {
        "imo": "imo",
        "mmsi": "mmsi",
        "flag": "flag",
        "destination": "destination",
        "draught": "draught",
        "ship_type": "ship_type",
        "vessel_type": "ship_type",
        "ais_type": "ais_type",
        "length": "length",
        "beam": "beam",
        "last_port": "last_port",
        "built_year": "built_year",
    }.items():
        value = meta.get(source_key) or asset.get(source_key)
        if value:
            profile[target_key] = value

    if profile.get("length") and profile.get("beam") and not profile.get("length_beam"):
        profile["length_beam"] = f"{profile['length']} / {profile['beam']}"
    if profile:
        imo = profile.get("imo")
        profile["registry_source"] = "VesselFinder-ready AIS metadata"
        if imo:
            profile["registry_url"] = f"https://www.vesselfinder.com/vessels/details/{imo}"
    return profile


def _visual_label(value: str) -> str:
    parts = [p for p in "".join(ch if ch.isalnum() else " " for ch in value).split() if p]
    if not parts:
        return "VI"
    if len(parts) == 1:
        return parts[0][:3].upper()
    return "".join(p[0] for p in parts[:3]).upper()


def _nav_status_label(value: Any) -> str:
    labels = {
        0: "Under way using engine",
        1: "At anchor",
        2: "Not under command",
        3: "Restricted manoeuvrability",
        4: "Constrained by draught",
        5: "Moored",
        6: "Aground",
        7: "Fishing",
        8: "Under way sailing",
    }
    try:
        return labels.get(int(value), f"AIS status {value}")
    except Exception:
        return str(value)


@router.get("", summary="List tracked assets", response_model=AssetListResponse)
async def list_assets(
    request: Request,
    asset_type: Optional[str] = Query(None, description="aircraft|vessel|facility"),
    limit: int = Query(50, ge=1, le=30000),
):
    """List tracked physical assets."""
    from storage.asset_repo import AssetRepository
    repo = AssetRepository()
    try:
        assets = await repo.get_assets(asset_type=asset_type, limit=limit)
        return {"total": len(assets), "assets": assets}
    except Exception as exc:
        logger.error("list_assets failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/counts", summary="Asset counts by type", response_model=AssetCountsResponse)
async def asset_counts(request: Request):
    """Count of tracked assets per type."""
    import asyncio
    from storage.asset_repo import AssetRepository
    repo = AssetRepository()
    try:
        counts = await asyncio.wait_for(repo.count_assets(), timeout=8.0)
        return {"counts": counts, "total": sum(counts.values())}
    except asyncio.TimeoutError:
        logger.warning("count_assets timed out")
        return {"counts": {}, "total": 0}
    except Exception as exc:
        logger.error("asset_counts failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/snapshot/latest", summary="Latest asset snapshot", response_model=AssetSnapshotResponse)
async def latest_asset_snapshot(
    request: Request,
    asset_type: Optional[str] = Query(None, description="aircraft|vessel|facility"),
    limit: int = Query(500, ge=1, le=30000),
):
    event_bus = request.app.state.event_bus
    key = f"snapshot:assets:latest:v2:{asset_type or 'all'}:{limit}"
    if event_bus:
        try:
            cached = await event_bus.cache_get(key)
            if isinstance(cached, dict):
                return cached
        except Exception:
            pass

    from storage.asset_repo import AssetRepository
    repo = AssetRepository()
    assets = await repo.get_assets(asset_type=asset_type, limit=limit)
    payload = {
        "snapshot_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "asset_type": asset_type,
        "total": len(assets),
        "assets": assets,
    }
    if event_bus:
        try:
            await event_bus.cache_set(key, payload, ttl_seconds=120)
            minute_key = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
            await event_bus.cache_set(
                f"snapshot:assets:v2:{minute_key}:{asset_type or 'all'}:{limit}",
                payload,
                ttl_seconds=6 * 3600,
            )
        except Exception:
            pass
    return payload


@router.get("/snapshot", summary="Asset snapshot by minute", response_model=AssetSnapshotResponse)
async def asset_snapshot(
    request: Request,
    at: str = Query(..., description="ISO timestamp"),
    asset_type: Optional[str] = Query(None, description="aircraft|vessel|facility"),
    limit: int = Query(500, ge=1, le=30000),
):
    event_bus = request.app.state.event_bus
    if not event_bus:
        raise HTTPException(status_code=404, detail="Snapshots unavailable (redis cache offline)")

    try:
        dt = datetime.fromisoformat(at.replace("Z", "+00:00")).astimezone(timezone.utc)
        minute_key = dt.strftime("%Y%m%d%H%M")
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid 'at' timestamp")

    key = f"snapshot:assets:v2:{minute_key}:{asset_type or 'all'}:{limit}"
    cached = await event_bus.cache_get(key)
    if not isinstance(cached, dict):
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return cached


@router.get("/in-bounds", summary="Assets within a viewport bounding box", response_model=AssetListResponse)
async def assets_in_bounds(
    request: Request,
    min_lat: float = Query(..., ge=-90, le=90),
    max_lat: float = Query(..., ge=-90, le=90),
    min_lon: float = Query(..., ge=-180, le=180),
    max_lon: float = Query(..., ge=-180, le=180),
    asset_type: Optional[str] = Query(None, description="aircraft|vessel|facility"),
    limit: int = Query(2000, ge=1, le=10000),
):
    """Viewport-driven asset fetch: only assets whose last position is inside the map bounds.

    Cached briefly (30s) on a coarse bbox key so panning small distances reuses results.
    """
    event_bus = request.app.state.event_bus
    # Snap to 1-degree grid so nearby viewports share a cache entry.
    key = (
        f"assets:bounds:{asset_type or 'all'}:{limit}:"
        f"{round(min_lat)}:{round(max_lat)}:{round(min_lon)}:{round(max_lon)}"
    )
    if event_bus:
        try:
            cached = await event_bus.cache_get(key)
            if isinstance(cached, dict):
                return cached
        except Exception:
            pass

    from storage.asset_repo import AssetRepository
    repo = AssetRepository()
    assets = await repo.get_assets_in_bbox(
        min_lat=min_lat, max_lat=max_lat, min_lon=min_lon, max_lon=max_lon,
        asset_type=asset_type, limit=limit,
    )
    payload = {"total": len(assets), "assets": assets}
    if event_bus:
        try:
            await event_bus.cache_set(key, payload, ttl_seconds=30)
        except Exception:
            pass
    return payload


@router.get("/{asset_id}", summary="Single asset detail", response_model=AssetItemSchema)
async def get_asset(asset_id: str, request: Request):
    """Fetch a single asset by ID with track history."""
    from storage.asset_repo import AssetRepository
    repo = AssetRepository()
    try:
        asset = await repo.get_asset(asset_id)
        if not asset:
            raise HTTPException(status_code=404, detail=f"Asset {asset_id} not found")
        asset["profile"] = _asset_profile(asset)
        return asset
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("get_asset failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
