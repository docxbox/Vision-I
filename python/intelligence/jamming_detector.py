"""
intelligence/jamming_detector.py
--------------------------------
GPS jamming heuristics derived from ADS-B/asset anomalies.
Designed to be conservative and degrade gracefully when data is sparse.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from storage.database import AssetModel, get_session


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _grid_key(lat: float, lon: float, size_deg: float) -> str:
    lat_bin = round(lat / size_deg) * size_deg
    lon_bin = round(lon / size_deg) * size_deg
    return f"{lat_bin:.2f}:{lon_bin:.2f}"


async def detect_jamming_heatmap(
    window_hours: int = 3,
    tile_size_deg: float = 1.0,
    min_count: int = 3,
) -> Dict[str, Any]:
    """
    Produce heat tiles where ADS-B data looks degraded.
    Heuristic: airborne aircraft with missing altitude or speed.
    """
    cutoff = _utcnow() - timedelta(hours=window_hours)
    tiles: Dict[str, Dict[str, Any]] = {}

    async with get_session() as session:
        rows = (
            await session.execute(
                AssetModel.__table__.select()
                .where(AssetModel.asset_type == "aircraft")
                .where(AssetModel.last_seen >= cutoff)
            )
        ).mappings().all()

    for row in rows:
        lat = row.get("last_lat")
        lon = row.get("last_lon")
        if lat is None or lon is None:
            continue
        on_ground = row.get("on_ground")
        if on_ground:
            continue

        missing_alt = row.get("last_altitude") is None
        missing_speed = row.get("last_speed") is None
        if not (missing_alt or missing_speed):
            continue

        key = _grid_key(float(lat), float(lon), tile_size_deg)
        tile = tiles.setdefault(key, {
            "lat": float(lat),
            "lon": float(lon),
            "count": 0,
        })
        tile["count"] += 1

    heat = []
    max_count = max((t["count"] for t in tiles.values()), default=1)
    for tile in tiles.values():
        if tile["count"] < min_count:
            continue
        intensity = math.log(tile["count"] + 1) / math.log(max_count + 1)
        heat.append({
            "lat": tile["lat"],
            "lon": tile["lon"],
            "count": tile["count"],
            "intensity": round(intensity, 4),
        })

    heat.sort(key=lambda t: t["intensity"], reverse=True)
    return {
        "generated_at": _utcnow().isoformat(),
        "window_hours": window_hours,
        "tiles": heat,
    }
