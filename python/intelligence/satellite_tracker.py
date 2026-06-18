"""
intelligence/satellite_tracker.py
---------------------------------
Optional satellite pass correlation using TLEs.
Requires skyfield (optional). If unavailable, returns empty results.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from config.settings import settings


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


_TLE_CACHE: dict = {"ts": 0.0, "lines": []}


def _load_tles() -> List[Tuple[str, str, str]]:
    path = settings.sat_tle_path.strip()
    raw = settings.sat_tle_text.strip()
    url = settings.sat_tle_url.strip()
    lines: List[str] = []

    if path:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                lines = [line.strip() for line in handle if line.strip()]
        except Exception:
            lines = []
    elif raw:
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
    elif url:
        # Best-effort remote fetch + in-memory cache.
        import time
        now = time.time()
        ttl_s = max(1, int(settings.sat_tle_cache_ttl_hours)) * 3600
        if _TLE_CACHE["lines"] and (now - float(_TLE_CACHE["ts"])) < ttl_s:
            lines = list(_TLE_CACHE["lines"])
        else:
            try:
                import requests
                resp = requests.get(
                    url,
                    timeout=10,
                    headers={"User-Agent": "Vision-I/1.0 (+https://localhost)"},
                )
                resp.raise_for_status()
                lines = [ln.strip() for ln in resp.text.splitlines() if ln.strip()]
                _TLE_CACHE["ts"] = now
                _TLE_CACHE["lines"] = list(lines)
            except Exception:
                lines = list(_TLE_CACHE["lines"]) if _TLE_CACHE["lines"] else []

    triples = []
    for i in range(0, len(lines) - 2, 3):
        triples.append((lines[i], lines[i + 1], lines[i + 2]))
    return triples


def compute_passes(
    bbox: Tuple[float, float, float, float],
    hours_ahead: int = 6,
    step_seconds: int = 60,
    limit: int = 50,
) -> Dict[str, Any]:
    try:
        from skyfield.api import EarthSatellite, load, wgs84  # type: ignore
    except Exception:
        return {
            "generated_at": _utcnow().isoformat(),
            "passes": [],
            "note": "skyfield not installed (pip install skyfield).",
        }

    tles = _load_tles()
    if not tles:
        return {
            "generated_at": _utcnow().isoformat(),
            "passes": [],
            "note": "No TLEs configured (SAT_TLE_PATH or SAT_TLE_TEXT).",
        }

    lat_min, lon_min, lat_max, lon_max = bbox
    ts = load.timescale()
    start = _utcnow()
    end = start + timedelta(hours=hours_ahead)

    passes: List[Dict[str, Any]] = []
    for name, l1, l2 in tles:
        sat = EarthSatellite(l1, l2, name, ts)
        t = start
        while t <= end and len(passes) < limit:
            t_sf = ts.from_datetime(t)
            subpoint = sat.at(t_sf).subpoint()
            lat = subpoint.latitude.degrees
            lon = subpoint.longitude.degrees
            if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
                passes.append({
                    "satellite": name,
                    "timestamp": t.isoformat(),
                    "lat": round(lat, 4),
                    "lon": round(lon, 4),
                    "alt_km": round(subpoint.elevation.km, 2),
                })
            t += timedelta(seconds=step_seconds)

    return {
        "generated_at": _utcnow().isoformat(),
        "passes": passes[:limit],
    }
