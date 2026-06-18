"""
extractors/firms.py
────────────────────
Fetches active fire hotspot data from NASA FIRMS (Fire Information
for Resource Management System).

API docs: https://firms.modaps.eosdis.nasa.gov/api/
Auth: Free API key (register at firms.modaps.eosdis.nasa.gov)
Data: VIIRS satellite fire detections — lat, lon, brightness, confidence,
      fire radiative power, acquisition time.
"""

import csv
import io
import logging
from typing import Any, Dict, List, Optional

import requests

from config.settings import settings
from core.base import BaseExtractor
from core.schema import VisionEvent
from core.utils import stable_id, utcnow_iso


class FIRMSExtractor(BaseExtractor):
    """
    Fetches active fire data from NASA FIRMS VIIRS satellite.

    fetch() params:
        limit     int     max records (default 100)
        area      str     bounding box "lat_min,lon_min,lat_max,lon_max"
                          or country code (default: "world")
        days      int     how many days of data (default: 1)
    """

    source_name = "firms"

    _BASE_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"

    def fetch(
        self,
        limit: int = 100,
        area: str = "world",
        days: int = 1,
        **_,
    ) -> List[Any]:
        api_key = settings.nasa_firms_key
        if not api_key:
            self.logger.info("FIRMS: no NASA_FIRMS_KEY configured — skipping")
            return []

        url = f"{self._BASE_URL}/{api_key}/VIIRS_SNPP_NRT/{area}/{days}"
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 401:
                self.logger.warning("FIRMS: invalid API key (401)")
                return []
            if resp.status_code == 429:
                self.logger.warning("FIRMS: rate-limited (429)")
                return []
            resp.raise_for_status()
        except requests.RequestException as exc:
            self.logger.error("FIRMS fetch failed: %s", exc)
            return []

        # Parse CSV response
        reader = csv.DictReader(io.StringIO(resp.text))
        records = []
        for row in reader:
            records.append(row)
            if len(records) >= limit:
                break

        self.logger.info("FIRMS: fetched %d fire hotspots", len(records))
        return records

    def normalize(self, item: Any) -> VisionEvent:
        lat = float(item.get("latitude", 0))
        lon = float(item.get("longitude", 0))
        brightness = float(item.get("bright_ti4", 0) or item.get("brightness", 0))
        confidence = item.get("confidence", "nominal")
        frp = float(item.get("frp", 0) or 0)
        acq_date = item.get("acq_date", "")
        acq_time = item.get("acq_time", "")

        ts = utcnow_iso()
        if acq_date:
            ts = f"{acq_date}T{acq_time or '0000'}Z" if acq_time else f"{acq_date}T00:00:00Z"
            # Fix format: "2026-04-01T1430Z" → "2026-04-01T14:30:00Z"
            if len(acq_time) == 4:
                ts = f"{acq_date}T{acq_time[:2]}:{acq_time[2:]}:00Z"

        day_key = acq_date.replace("-", "") if acq_date else utcnow_iso()[:10].replace("-", "")
        eid = stable_id(self.source_name, f"{lat:.3f}_{lon:.3f}_{day_key}")

        # Map confidence to a score
        conf_map = {"low": 0.3, "nominal": 0.6, "high": 0.9}
        conf_score = conf_map.get(str(confidence).lower(), 0.6)

        title = f"Fire detected ({lat:.2f}, {lon:.2f})"
        description = (
            f"Satellite fire detection at ({lat:.4f}, {lon:.4f}). "
            f"Brightness: {brightness:.0f}K, FRP: {frp:.1f}MW, "
            f"Confidence: {confidence}."
        )

        return VisionEvent(
            event_id    = eid,
            source      = self.source_name,
            source_id   = f"{lat:.3f}_{lon:.3f}_{day_key}",
            event_type  = "disaster",
            title       = title,
            description = description,
            body        = description,
            url         = f"https://firms.modaps.eosdis.nasa.gov/map/#{lat},{lon},10",
            language    = "en",
            timestamp   = ts,
            ingest_time = utcnow_iso(),
            actors      = [],
            location    = {"lat": lat, "lon": lon, "name": f"Fire at ({lat:.2f}, {lon:.2f})"},
            sentiment   = {"label": "NEGATIVE", "score": 0.2},
            tags        = ["fire", "wildfire", "satellite", "disaster", "firms"],
            extras      = {
                "brightness":  brightness,
                "frp":         frp,
                "confidence":  confidence,
                "satellite":   item.get("satellite", "VIIRS"),
                "daynight":    item.get("daynight", ""),
            },
            raw = item,
        )

    def health(self) -> Dict[str, Any]:
        if not settings.nasa_firms_key:
            return {"source": self.source_name, "status": "unconfigured", "detail": "No NASA_FIRMS_KEY"}
        return super().health()
