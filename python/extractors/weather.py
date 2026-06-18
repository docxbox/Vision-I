"""
extractors/weather.py
──────────────────────
Fetches active severe weather alerts from the US National Weather Service.

API docs: https://www.weather.gov/documentation/services-web-api
Auth: None required (just needs a User-Agent header).
Rate limits: Very generous — no explicit limit documented.
"""

from typing import Any, Dict, List, Optional

import requests

from core.base import BaseExtractor
from core.schema import VisionEvent
from core.utils import stable_id, utcnow_iso


_ALERTS_URL = "https://api.weather.gov/alerts/active"

# NWS severity → our sentiment score
_SEVERITY_MAP = {
    "Extreme":  0.05,
    "Severe":   0.15,
    "Moderate":  0.35,
    "Minor":     0.50,
    "Unknown":   0.50,
}


class WeatherExtractor(BaseExtractor):
    """
    Fetches active weather alerts from the US National Weather Service.

    fetch() params:
        limit       int     max alerts (default 50)
        severity    str     filter by severity (Extreme, Severe, Moderate, Minor)
        event_type  str     filter by event type (e.g. "Tornado Warning")
    """

    source_name = "nws"

    def fetch(
        self,
        limit: int = 50,
        severity: Optional[str] = None,
        event_type: Optional[str] = None,
        **_,
    ) -> List[Any]:
        headers = {
            "User-Agent": "Vision-I Intelligence Platform (research)",
            "Accept": "application/geo+json",
        }
        # NWS free tier rejects very small limits; 25 is fine, but
        # send no limit param if caller passed default — let NWS paginate.
        params: dict = {}
        if limit < 500:
            params["limit"] = min(limit, 500)
        if severity:
            params["severity"] = severity

        try:
            resp = requests.get(_ALERTS_URL, headers=headers, params=params, timeout=15)
            if resp.status_code in (400, 503):
                self.logger.warning("NWS: HTTP %d — skipping", resp.status_code)
                return []
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            self.logger.error("NWS fetch failed: %s", exc)
            return []

        features = data.get("features", [])

        if event_type:
            features = [f for f in features if event_type.lower() in (f.get("properties", {}).get("event", "")).lower()]

        self.logger.info("NWS: fetched %d weather alerts", len(features[:limit]))
        return features[:limit]

    def normalize(self, item: Any) -> VisionEvent:
        props = item.get("properties", {})

        alert_id = props.get("id", "")
        event = props.get("event", "Weather Alert")
        headline = props.get("headline", event)
        description = props.get("description", "")[:2000]
        severity = props.get("severity", "Unknown")
        urgency = props.get("urgency", "Unknown")
        certainty = props.get("certainty", "Unknown")
        area_desc = props.get("areaDesc", "")
        sender = props.get("senderName", "NWS")
        onset = props.get("onset") or props.get("effective") or utcnow_iso()
        expires = props.get("expires")

        eid = stable_id(self.source_name, alert_id or f"{event}_{onset}")

        # Try to extract coordinates from geometry
        lat, lon = None, None
        geometry = item.get("geometry")
        if geometry and geometry.get("type") == "Polygon":
            coords = geometry.get("coordinates", [[]])
            if coords and coords[0]:
                # Centroid of first polygon ring
                ring = coords[0]
                lat = sum(c[1] for c in ring) / len(ring)
                lon = sum(c[0] for c in ring) / len(ring)

        sentiment_score = _SEVERITY_MAP.get(severity, 0.5)

        title = headline[:300] if headline else f"{event} — {area_desc[:100]}"
        body = f"{headline}\n\n{description}" if description else headline

        tags = ["weather", "alert", severity.lower()]
        if "tornado" in event.lower():
            tags.append("tornado")
        elif "hurricane" in event.lower() or "tropical" in event.lower():
            tags.append("hurricane")
        elif "flood" in event.lower():
            tags.append("flood")
        elif "winter" in event.lower() or "blizzard" in event.lower():
            tags.append("winter_storm")
        elif "fire" in event.lower():
            tags.append("wildfire")

        return VisionEvent(
            event_id    = eid,
            source      = self.source_name,
            source_id   = alert_id,
            event_type  = "weather",
            title       = title,
            description = headline,
            body        = body,
            url         = props.get("@id", ""),
            language    = "en",
            timestamp   = onset,
            ingest_time = utcnow_iso(),
            actors      = [{"name": sender, "type": "ORG"}],
            location    = {
                "lat":  lat,
                "lon":  lon,
                "name": area_desc[:200] if area_desc else None,
            },
            sentiment   = {"label": "NEGATIVE", "score": sentiment_score},
            tags        = tags,
            extras      = {
                "severity":  severity,
                "urgency":   urgency,
                "certainty": certainty,
                "expires":   expires,
                "area":      area_desc,
            },
            raw = item,
        )
