"""
extractors/ais.py
-----------------
AIS/vessel tracking extractor.

Priority:
  1. aisstream.io WebSocket API (free, real-time, global) â€” set AISSTREAM_API_KEY
  2. Generic HTTP AIS endpoint (legacy) â€” set AIS_API_URL

aisstream.io: https://aisstream.io  (free API key, no cost for basic access)
WebSocket:    wss://stream.aisstream.io/v0/stream
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

import requests

from config.settings import settings
from core.base import BaseExtractor
from core.schema import VisionEvent
from core.utils import stable_id, to_iso, utcnow_iso
from extractors.anomaly_detector import HybridAnomalyDetector

logger = logging.getLogger("vision_i.extractors.ais")

async def _collect_aisstream(api_key: str, limit: int, timeout_seconds: int = 30) -> List[Dict]:
    """
    Connect to aisstream.io WebSocket, collect up to `limit` position reports,
    disconnect after `timeout_seconds` or when limit is reached.
    """
    import aiohttp

    url = "wss://stream.aisstream.io/v0/stream"
    subscribe_msg = {
        "APIKey": api_key,
        "BoundingBoxes": [[[-90, -180], [90, 180]]],  # global
        "FiltersShipMMSI": [],
        "FilterMessageTypes": ["PositionReport"],
    }

    items: List[Dict] = []
    try:
        import ssl as ssl_mod
        ssl_ctx = ssl_mod.create_default_context()
        conn_timeout = aiohttp.ClientTimeout(total=timeout_seconds + 10, sock_connect=15)
        async with aiohttp.ClientSession(timeout=conn_timeout) as session:
            async with session.ws_connect(url, ssl=ssl_ctx, heartbeat=20) as ws:
                await ws.send_str(json.dumps(subscribe_msg))
                logger.info("aisstream.io: subscription sent, waiting for vesselsâ€¦")

                import time as _time
                deadline = _time.monotonic() + timeout_seconds
                while len(items) < limit:
                    remaining = deadline - _time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        msg = await asyncio.wait_for(ws.receive(), timeout=min(remaining, 8.0))
                        # aisstream.io sends BINARY frames (JSON encoded as bytes)
                        raw_bytes = None
                        if msg.type == aiohttp.WSMsgType.BINARY:
                            raw_bytes = msg.data
                        elif msg.type == aiohttp.WSMsgType.TEXT:
                            raw_bytes = msg.data.encode("utf-8")
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR,
                                          aiohttp.WSMsgType.CLOSING):
                            logger.warning("aisstream.io ws closed: type=%s", msg.type)
                            break
                        else:
                            continue

                        if raw_bytes:
                            try:
                                data = json.loads(raw_bytes)
                            except json.JSONDecodeError:
                                continue
                            if data.get("MessageType") == "PositionReport":
                                items.append(data)
                            else:
                                logger.debug("aisstream.io non-position msg: %s", data.get("MessageType"))
                    except asyncio.TimeoutError:
                        break
    except Exception as exc:
        logger.warning("aisstream.io WebSocket error: %s", exc)

    logger.info("aisstream.io: collected %d position reports", len(items))
    return items


class AISExtractor(BaseExtractor):
    """
    Fetch vessel telemetry from aisstream.io (WebSocket) or a legacy HTTP AIS API.
    Normalizes vessel positions and anomalies into VisionEvents and asset records.
    """

    source_name = "ais"

    def __init__(self):
        super().__init__()
        self._anomaly_detector = HybridAnomalyDetector(domain="sea")

    def fetch(self, limit: int = 50, **_) -> List[Any]:
        if settings.aisstream_api_key:
            try:
                return asyncio.run(
                    _collect_aisstream(settings.aisstream_api_key, limit=limit, timeout_seconds=20)
                )
            except RuntimeError:
                # Already inside an event loop (shouldn't happen in thread pool, but be safe)
                loop = asyncio.new_event_loop()
                try:
                    return loop.run_until_complete(
                        _collect_aisstream(settings.aisstream_api_key, limit=limit, timeout_seconds=20)
                    )
                finally:
                    loop.close()
        if settings.ais_api_url:
            headers = {}
            if settings.ais_api_key:
                headers["Authorization"] = f"Bearer {settings.ais_api_key}"
            try:
                resp = requests.get(
                    settings.ais_api_url,
                    headers=headers,
                    params={"limit": limit},
                    timeout=15,
                )
                resp.raise_for_status()
                payload = resp.json()
                if isinstance(payload, dict):
                    items = payload.get("items") or payload.get("vessels") or payload.get("data") or []
                elif isinstance(payload, list):
                    items = payload
                else:
                    items = []
                return items[:limit]
            except requests.RequestException as exc:
                self.logger.error("AIS HTTP fetch failed: %s", exc)
                return []

        self.logger.info("AIS: no AISSTREAM_API_KEY or AIS_API_URL configured â€” skipping")
        return []

    def _is_aisstream_format(self, item: Any) -> bool:
        return isinstance(item, dict) and "MessageType" in item and "MetaData" in item

    def normalize(self, item: Any) -> VisionEvent:
        if self._is_aisstream_format(item):
            return self._normalize_aisstream(item)
        return self._normalize_generic(item)

    def normalize_asset(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if self._is_aisstream_format(item):
            return self._normalize_aisstream_asset(item)
        return self._normalize_generic_asset(item)

    def _normalize_aisstream(self, item: Dict) -> VisionEvent:
        meta = item.get("MetaData", {})
        msg  = item.get("Message", {}).get("PositionReport", {})

        mmsi    = str(meta.get("MMSI", "unknown"))
        name    = (meta.get("ShipName") or f"Vessel {mmsi}").strip()
        lat     = meta.get("latitude")
        lon     = meta.get("longitude")
        speed   = msg.get("Sog")
        # TrueHeading=511 is AIS "not available" sentinel â€” fall back to CoG
        raw_hdg = msg.get("TrueHeading")
        heading = msg.get("Cog") if (raw_hdg is None or raw_hdg >= 511) else raw_hdg
        nav_st  = msg.get("NavigationalStatus", 0)
        ts      = to_iso(meta.get("time_utc"))
        
        has_latlon = lat is not None and lon is not None
        anomaly = self._anomaly_detector.check_sea_anomaly(speed, nav_st, has_latlon)

        title = f"{name} vessel update"
        if anomaly:
            title = f"[ANOMALY] {name}: {anomaly}"

        return VisionEvent(
            event_id   = stable_id(self.source_name, f"{mmsi}:{(ts or '')[:13]}"),
            source     = self.source_name,
            source_id  = mmsi,
            event_type = "maritime_anomaly" if anomaly else "maritime",
            title      = title,
            description= anomaly or f"Vessel {name} position update",
            body       = anomaly or f"Vessel {name} at {lat},{lon}",
            url        = None,
            language   = "en",
            author     = "aisstream.io",
            timestamp  = ts,
            ingest_time= utcnow_iso(),
            actors     = [{"name": name, "type": "VEHICLE"}],
            location   = {"lat": lat, "lon": lon, "name": name},
            sentiment  = {"label": "NEGATIVE" if anomaly else "NEUTRAL", "score": 0.25 if anomaly else 0.55},
            tags       = ["ship", "maritime", "ais", "live"] + (["anomaly"] if anomaly else []),
            extras     = {
                "mmsi": mmsi,
                "speed_knots": speed,
                "heading_deg": heading,
                "nav_status": nav_st,
                "anomaly": anomaly,
            },
            raw = item,
        )

    def _normalize_aisstream_asset(self, item: Dict) -> Optional[Dict[str, Any]]:
        meta = item.get("MetaData", {})
        msg  = item.get("Message", {}).get("PositionReport", {})
        mmsi = str(meta.get("MMSI", ""))
        if not mmsi:
            return None
        name    = (meta.get("ShipName") or f"Vessel {mmsi}").strip()
        lat     = meta.get("latitude")
        lon     = meta.get("longitude")
        speed   = msg.get("Sog")
        raw_hdg = msg.get("TrueHeading")
        heading = msg.get("Cog") if (raw_hdg is None or raw_hdg >= 511) else raw_hdg
        return {
            "asset_id":       f"vessel:{mmsi}",
            "asset_type":     "vessel",
            "name":           name,
            "callsign":       None,
            "identifier":     mmsi,
            "origin_country": None,
            "last_lat":       lat,
            "last_lon":       lon,
            "last_speed":     speed,
            "last_heading":   heading,
            "last_seen":      to_iso(meta.get("time_utc")),
            "on_ground":      False,
            "meta":           {"mmsi": mmsi, "nav_status": msg.get("NavigationalStatus")},
        }

    def _normalize_generic(self, item: Any) -> VisionEvent:
        vessel_id = str(
            item.get("mmsi") or item.get("imo") or item.get("id") or item.get("ship_id") or "unknown"
        )
        name    = item.get("name") or item.get("vessel_name") or item.get("shipname") or f"Vessel {vessel_id}"
        lat     = item.get("lat") or item.get("latitude")
        lon     = item.get("lon") or item.get("longitude")
        speed   = item.get("speed") or item.get("sog")
        heading = item.get("heading") or item.get("cog")
        ts      = to_iso(item.get("timestamp") or item.get("last_seen"))
        nav_status = item.get("status") or item.get("nav_status") or 0
        has_latlon = lat is not None and lon is not None
        
        anomaly = self._anomaly_detector.check_sea_anomaly(speed, nav_status, has_latlon)
        title = f"{name} vessel update"
        if anomaly:
            title = f"[ANOMALY] {name}: {anomaly}"
        return VisionEvent(
            event_id   = stable_id(self.source_name, f"{vessel_id}:{(ts or '')[:13]}"),
            source     = self.source_name,
            source_id  = vessel_id,
            event_type = "maritime_anomaly" if anomaly else "maritime",
            title      = title,
            description= item.get("status_text") or anomaly or f"Vessel {name} update",
            body       = item.get("status_text") or anomaly or f"Vessel {name} update",
            url        = item.get("url"),
            language   = "en",
            author     = settings.ais_provider,
            timestamp  = ts,
            ingest_time= utcnow_iso(),
            actors     = [{"name": name, "type": "VEHICLE"}],
            location   = {"lat": lat, "lon": lon, "name": item.get("port_name") or item.get("destination") or name},
            sentiment  = {"label": "NEGATIVE" if anomaly else "NEUTRAL", "score": 0.25 if anomaly else 0.55},
            tags       = ["ship", "maritime", "ais", "live"] + (["anomaly"] if anomaly else []),
            extras     = {
                "mmsi": item.get("mmsi"),
                "imo": item.get("imo"),
                "destination": item.get("destination"),
                "status": item.get("status"),
                "speed_knots": speed,
                "heading_deg": heading,
                "anomaly": anomaly,
            },
            raw = item,
        )

    def _normalize_generic_asset(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        vessel_id = str(
            item.get("mmsi") or item.get("imo") or item.get("id") or item.get("ship_id") or ""
        )
        if not vessel_id:
            return None
        name = item.get("name") or item.get("vessel_name") or item.get("shipname") or f"Vessel {vessel_id}"
        return {
            "asset_id":       f"vessel:{vessel_id}",
            "asset_type":     "vessel",
            "name":           name,
            "callsign":       item.get("callsign"),
            "identifier":     vessel_id,
            "origin_country": item.get("flag") or item.get("country"),
            "last_lat":       item.get("lat") or item.get("latitude"),
            "last_lon":       item.get("lon") or item.get("longitude"),
            "last_speed":     item.get("speed") or item.get("sog"),
            "last_heading":   item.get("heading") or item.get("cog"),
            "last_seen":      to_iso(item.get("timestamp") or item.get("last_seen")),
            "on_ground":      False,
            "meta":           {"destination": item.get("destination"), "status": item.get("status"), "imo": item.get("imo"), "mmsi": item.get("mmsi")},
        }

    def health(self) -> Dict:
        if settings.aisstream_api_key:
            return {"source": self.source_name, "status": "ok", "provider": "aisstream.io"}
        if settings.ais_api_url:
            return {"source": self.source_name, "status": "ok", "provider": settings.ais_provider}
        return {"source": self.source_name, "status": "unconfigured",
                "detail": "Set AISSTREAM_API_KEY (aisstream.io) or AIS_API_URL"}

