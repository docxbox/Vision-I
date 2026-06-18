"""
extractors/opensky.py
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Fetches live aircraft positions from the OpenSky Network public API.

Docs: https://openskynetwork.github.io/opensky-api/
No authentication required for the public endpoint (rate-limited to ~10 req/min).
"""

import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from core.base import BaseExtractor
from core.schema import VisionEvent
from core.utils import stable_id, to_iso, utcnow_iso
from extractors.anomaly_detector import HybridAnomalyDetector

_STATES_URL = "https://opensky-network.org/api/states/all"
_OAUTH_TOKEN_URL = (
    "https://auth.opensky-network.org/auth/realms/opensky-network/"
    "protocol/openid-connect/token"
)

# Process-level OAuth2 token cache (shared across all OpenSkyExtractor instances)
_oauth_token: Optional[str] = None
_oauth_token_expires_at: float = 0.0


def _get_oauth_bearer_token() -> Optional[str]:
    """
    Client-credentials flow for OpenSky OAuth2.
    Returns a cached bearer token if still valid (60s safety margin),
    otherwise fetches a new one. Returns None if creds missing or fetch fails.
    """
    import os
    global _oauth_token, _oauth_token_expires_at

    cid  = os.getenv("OPENSKY_CLIENT_ID", "").strip()
    csec = os.getenv("OPENSKY_CLIENT_SECRET", "").strip()
    if not cid or not csec:
        return None

    now = time.time()
    if _oauth_token and now < _oauth_token_expires_at - 60:
        return _oauth_token

    try:
        resp = requests.post(
            _OAUTH_TOKEN_URL,
            data={
                "grant_type":    "client_credentials",
                "client_id":     cid,
                "client_secret": csec,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        _oauth_token = data.get("access_token")
        ttl = int(data.get("expires_in") or 1800)
        _oauth_token_expires_at = now + ttl
        return _oauth_token
    except requests.RequestException:
        _oauth_token = None
        _oauth_token_expires_at = 0.0
        return None

# OpenSky state vector field positions
_F_ICAO24         = 0
_F_CALLSIGN       = 1
_F_ORIGIN_COUNTRY = 2
_F_TIME_POSITION  = 3
_F_LAST_CONTACT   = 4
_F_LON            = 5
_F_LAT            = 6
_F_BARO_ALT       = 7
_F_ON_GROUND      = 8
_F_VELOCITY       = 9
_F_HEADING        = 10
_F_VERT_RATE      = 11
_F_SENSORS        = 12
_F_GEO_ALT        = 13
_F_SQUAWK         = 14
_F_SPI            = 15
_F_POS_SOURCE     = 16


class OpenSkyExtractor(BaseExtractor):
    """
    Fetches live aircraft state vectors.

    fetch() params:
        limit    int   max records after filtering (default 50)
        bbox     optional (lat_min, lon_min, lat_max, lon_max) geographic filter
        callsign optional string partial match on callsign
        icao24   optional string exact match on ICAO 24-bit address
        on_ground_only  bool include only grounded aircraft (default False)
        airborne_only   bool include only airborne aircraft (default False)
    """

    source_name = "opensky"

    def __init__(self):
        super().__init__()
        self._anomaly_detector = HybridAnomalyDetector(domain="air")
        self._last_states: List[Any] = []
        self._last_states_ts: float = 0.0
        self._cache_ttl_s: int = 120
        self._last_fetch_attempt_ts: float = 0.0
        self._cooldown_until_ts: float = 0.0

    def fetch(
        self,
        limit: int = 50,
        bbox: Optional[Tuple[float, float, float, float]] = None,
        callsign: Optional[str] = None,
        icao24: Optional[str] = None,
        on_ground_only: bool = False,
        airborne_only: bool = False,
        **_,
    ) -> List[Any]:
        import os
        now = time.time()
        params: dict = {}
        if bbox:
            lat_min, lon_min, lat_max, lon_max = bbox
            params.update(laMin=lat_min, loMin=lon_min, laMax=lat_max, loMax=lon_max)

        # Auth resolution (priority: OAuth2 bearer â†’ basic auth â†’ anonymous)
        force_anon = os.getenv("OPENSKY_FORCE_ANON", "").lower() in {"1", "true", "yes"}
        headers: Dict[str, str] = {}
        auth = None
        auth_mode = "anon"
        if not force_anon:
            bearer = _get_oauth_bearer_token()
            if bearer:
                headers["Authorization"] = f"Bearer {bearer}"
                auth_mode = "oauth2"
            else:
                user = os.getenv("OPENSKY_USER", "")
                pwd  = os.getenv("OPENSKY_PASS", "")
                if user and pwd:
                    auth = (user, pwd)
                    auth_mode = "basic"

        # Throttle: authenticated = 10s floor, anon = 60s floor.
        is_auth = auth_mode != "anon"
        min_interval = int(os.getenv("OPENSKY_MIN_INTERVAL_SECONDS", "10" if is_auth else "60"))
        if self._cooldown_until_ts and now < self._cooldown_until_ts:
            return self._cached_states()
        if self._last_fetch_attempt_ts and (now - self._last_fetch_attempt_ts) < max(1, min_interval):
            return self._cached_states()
        self._last_fetch_attempt_ts = now

        try:
            resp = requests.get(
                _STATES_URL, params=params, timeout=10,
                auth=auth, headers=headers,
            )
            if resp.status_code == 401 and auth_mode == "oauth2":
                # Token may have expired mid-flight â€” force refresh on next call
                global _oauth_token, _oauth_token_expires_at
                _oauth_token = None
                _oauth_token_expires_at = 0.0
                self.logger.warning("OpenSky: 401 on OAuth2 â€” invalidating token cache")
                self._cooldown_until_ts = now + 10
                return self._cached_states()
            if resp.status_code == 429:
                self.logger.warning("OpenSky: rate-limited (429) â€” returning empty")
                self._cooldown_until_ts = now + 90
                return self._cached_states()
            if resp.status_code == 503:
                self.logger.warning("OpenSky: service unavailable (503)")
                self._cooldown_until_ts = now + 30
                return self._cached_states()
            resp.raise_for_status()
            states = resp.json().get("states") or []
        except requests.RequestException as exc:
            self.logger.error("OpenSky fetch failed: %s", exc)
            self._cooldown_until_ts = now + 15
            return self._cached_states()

        if not states:
            return self._cached_states()

        self._last_states = states
        self._last_states_ts = time.time()

        results: List[Any] = []
        for s in states:
            if len(s) < 17:
                continue

            cs         = (s[_F_CALLSIGN] or "").strip()
            code       = (s[_F_ICAO24]   or "").lower()
            on_ground  = bool(s[_F_ON_GROUND])
            lat        = s[_F_LAT]
            lon        = s[_F_LON]
            if callsign and callsign.upper() not in cs.upper():
                continue
            if icao24 and icao24.lower() != code:
                continue
            if on_ground_only and not on_ground:
                continue
            if airborne_only and on_ground:
                continue
            if bbox and (lat is None or lon is None):
                continue

            results.append(s)
            if len(results) >= limit:
                break

        return results

    def _cached_states(self) -> List[Any]:
        if self._last_states and (time.time() - self._last_states_ts) <= self._cache_ttl_s:
            self.logger.info("OpenSky: using cached state vectors")
            return self._last_states
        return []

    def normalize(self, item: Any) -> VisionEvent:
        """
        Normalize only anomalous flights into VisionEvents.
        Normal flights are tracked as assets via normalize_asset().

        Anomaly triggers:
          - Emergency squawk codes: 7500 (hijack), 7600 (radio failure), 7700 (emergency)
          - Unusually low altitude: < 300m while airborne
          - Very high speed deviation (placeholder â€” needs baseline per aircraft type)
        """
        if not item or len(item) < 12:
            return VisionEvent(
                event_id   = stable_id(self.source_name, utcnow_iso()),
                source     = self.source_name,
                event_type = "transport",
                title      = "Invalid OpenSky vector",
                timestamp  = utcnow_iso(),
                ingest_time= utcnow_iso(),
                actors     = [],
                location   = None,
                sentiment  = None,
                raw        = item,
            )

        icao24, callsign, country, lat, lon, alt, vel, heading, vert, grounded, ts, squawk = (
            self._parse_state(item)
        )
        day_key = ts[:10].replace("-", "")
        eid = stable_id(self.source_name, f"{callsign}_{icao24}_{day_key}")

        # Detect anomaly type (Hybrid ML/Rules)
        anomaly = self._anomaly_detector.check_air_anomaly(squawk, alt, grounded, vel)

        if anomaly:
            title = f"[ANOMALY] Flight {callsign}: {anomaly}"
            description = (
                f"Anomalous flight detected: {callsign} from {country}. "
                f"Anomaly: {anomaly}."
            )
            if alt and not grounded:
                description += f" Altitude {alt:.0f} m, speed {vel:.0f} m/s."
            sentiment_score = 0.2  # anomalies are concerning
        else:
            title = f"Flight {callsign}"
            description = f"Flight {callsign} from {country}."
            if alt and not grounded:
                description += f" Altitude {alt:.0f} m, speed {vel:.0f} m/s."
            sentiment_score = 0.6 if (not grounded and vel and vel > 50) else 0.5

        return VisionEvent(
            event_id   = eid,
            source     = self.source_name,
            source_id  = f"{callsign}_{icao24}",
            event_type = "transport_anomaly" if anomaly else "transport",
            title      = title,
            description= description,
            body       = description,
            url        = f"https://opensky-network.org/aircraft/{icao24}",
            language   = "en",
            timestamp  = ts,
            ingest_time= utcnow_iso(),
            actors     = [
                {"name": f"Flight {callsign}", "type": "VEHICLE"},
                {"name": country,              "type": "ORG"},
            ],
            location   = {
                "lat":  lat,
                "lon":  lon,
                "name": f"{callsign} over ({lat:.2f}, {lon:.2f})" if lat and lon else None,
            },
            sentiment  = {"label": "NEGATIVE" if anomaly else "NEUTRAL", "score": sentiment_score},
            tags       = ["flight", "aviation", "live"] + (["anomaly", "emergency"] if anomaly else []),
            extras     = {
                "callsign":        callsign,
                "icao24":          icao24,
                "origin_country":  country,
                "altitude_m":      alt,
                "speed_ms":        vel,
                "heading_deg":     heading,
                "vertical_rate":   vert,
                "on_ground":       grounded,
                "squawk":          squawk,
                "anomaly":         anomaly,
            },
            raw = item,
        )

    def normalize_asset(self, item: Any) -> Optional[dict]:
        """Convert a flight state vector to an asset record for the assets table."""
        if not item or len(item) < 12:
            return None

        icao24, callsign, country, lat, lon, alt, vel, heading, vert, grounded, ts, squawk = (
            self._parse_state(item)
        )

        return {
            "asset_id":       f"aircraft:{icao24}",
            "asset_type":     "aircraft",
            "name":           f"Flight {callsign}",
            "callsign":       callsign,
            "identifier":     icao24,
            "origin_country": country,
            "last_lat":       lat,
            "last_lon":       lon,
            "last_altitude":  alt,
            "last_speed":     vel,
            "last_heading":   heading,
            "last_seen":      ts,
            "on_ground":      grounded,
            "meta": {
                "vertical_rate": vert,
                "squawk":        squawk,
            },
        }

    def _parse_state(self, item: list) -> tuple:
        """Extract fields from an OpenSky state vector."""
        icao24   = (item[_F_ICAO24]         or "").strip()
        callsign = (item[_F_CALLSIGN]        or "Unknown").strip()
        country  = (item[_F_ORIGIN_COUNTRY]  or "Unknown")
        lat      = item[_F_LAT]
        lon      = item[_F_LON]
        alt      = item[_F_BARO_ALT]
        vel      = item[_F_VELOCITY]
        heading  = item[_F_HEADING]
        vert     = item[_F_VERT_RATE]
        grounded = bool(item[_F_ON_GROUND])
        ts       = to_iso(item[_F_LAST_CONTACT])
        squawk   = item[_F_SQUAWK] if len(item) > _F_SQUAWK else None
        return icao24, callsign, country, lat, lon, alt, vel, heading, vert, grounded, ts, squawk

        return icao24, callsign, country, lat, lon, alt, vel, heading, vert, grounded, ts, squawk

