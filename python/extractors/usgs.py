"""
extractors/usgs.py
──────────────────
Fetches recent significant earthquakes from the USGS Earthquake Hazards API.

Docs: https://earthquake.usgs.gov/fdsnws/event/1/
"""

from datetime import datetime, timedelta
from typing import Any, List, Optional, Tuple

import requests

from core.base import BaseExtractor
from core.schema import VisionEvent
from core.utils import stable_id, to_iso, utcnow_iso

_API_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"


class USGSExtractor(BaseExtractor):
    """
    Pulls earthquakes from the USGS GeoJSON feed.

    fetch() params:
        limit        int   max records to return (default 10)
        min_mag      float minimum magnitude (default 4.0)
        hours_back   int   how many hours of history to fetch (default 24)
        bbox         optional (lat_min, lon_min, lat_max, lon_max) bounding box
    """

    source_name = "usgs"

    def fetch(
        self,
        limit: int = 10,
        min_mag: float = 4.0,
        hours_back: int = 24,
        bbox: Optional[Tuple[float, float, float, float]] = None,
        **_,
    ) -> List[Any]:
        end_time   = datetime.utcnow()
        begin_time = end_time - timedelta(hours=hours_back)

        params: dict = {
            "format":       "geojson",
            "starttime":    begin_time.isoformat() + "Z",
            "endtime":      end_time.isoformat() + "Z",
            "minmagnitude": min_mag,
            "orderby":      "time",
            "limit":        limit,
        }

        if bbox:
            lat_min, lon_min, lat_max, lon_max = bbox
            params.update(
                minlatitude=lat_min,
                minlongitude=lon_min,
                maxlatitude=lat_max,
                maxlongitude=lon_max,
            )

        try:
            resp = requests.get(_API_URL, params=params, timeout=10)
            resp.raise_for_status()
            return resp.json().get("features", [])
        except requests.RequestException as exc:
            self.logger.error("USGS fetch failed: %s", exc)
            return []

    def normalize(self, item: Any) -> VisionEvent:
        props  = item.get("properties", {}) or {}
        coords = (item.get("geometry") or {}).get("coordinates") or []

        lon   = coords[0] if len(coords) > 0 else None
        lat   = coords[1] if len(coords) > 1 else None
        depth = coords[2] if len(coords) > 2 else None

        mag   = props.get("mag")
        place = props.get("place") or "Unknown location"
        sid   = item.get("id") or stable_id(self.source_name, str(props.get("time", utcnow_iso())))

        return VisionEvent(
            event_id   = stable_id(self.source_name, sid),
            source     = self.source_name,
            source_id  = sid,
            event_type = "disaster",
            title      = f"M{mag} Earthquake near {place}",
            description= place,
            body       = (
                f"Magnitude {mag} earthquake struck near {place} "
                f"at a depth of {depth}km. "
                f"Type: {props.get('type', 'earthquake')}."
            ),
            url        = props.get("url"),
            language   = "en",
            timestamp  = to_iso(props.get("time")),
            ingest_time= utcnow_iso(),
            actors     = [],
            location   = {"lat": lat, "lon": lon, "name": place},
            sentiment  = {"label": "NEGATIVE", "score": 0.9},
            tags       = ["earthquake", "disaster", "geophysical"],
            extras     = {
                "magnitude": mag,
                "depth_km":  depth,
                "quake_type": props.get("type"),
                "status":    props.get("status"),
                "tsunami":   props.get("tsunami"),
            },
            raw = item,
        )
