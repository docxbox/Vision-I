"""
intelligence/airspace.py
------------------------
Lightweight airspace intelligence helpers (NOTAMs, closures).

Designed to work in degraded mode when no NOTAM provider is configured.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

from config.settings import settings

logger = logging.getLogger("vision_i.intelligence.airspace")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_local_notams(path: str) -> List[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            return data.get("notams", []) or data.get("items", []) or []
        if isinstance(data, list):
            return data
    except Exception as exc:
        logger.warning("Failed to load local NOTAM feed: %s", exc)
    return []


def fetch_notams(
    bbox: Optional[Tuple[float, float, float, float]] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """
    Fetch NOTAMs from a configured provider.
    Returns an empty list when not configured.
    """
    path = settings.notam_feed_path.strip()
    if path:
        return _load_local_notams(path)[:limit]

    url = settings.notam_api_url.strip()
    if not url:
        return []

    params: Dict[str, Any] = {"limit": limit}
    if bbox:
        lat_min, lon_min, lat_max, lon_max = bbox
        params.update(
            lat_min=lat_min,
            lon_min=lon_min,
            lat_max=lat_max,
            lon_max=lon_max,
        )

    headers = {}
    header_name = settings.notam_api_header.strip() or "X-API-Key"
    key = settings.notam_api_key.strip()
    if key:
        headers[header_name] = key

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=12)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            return data.get("notams", []) or data.get("items", []) or []
        if isinstance(data, list):
            return data
    except Exception as exc:
        logger.warning("NOTAM fetch failed: %s", exc)
    return []


def detect_airspace_closures(
    notams: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Normalize NOTAMs into closures/no-fly zones.
    Works with generic fields and degrades gracefully.
    """
    closures: List[Dict[str, Any]] = []
    for item in notams:
        title = (item.get("title") or item.get("subject") or "Airspace Notice").strip()
        text = (item.get("text") or item.get("body") or item.get("message") or "").strip()
        status = (item.get("status") or "active").lower()
        start = item.get("start") or item.get("start_time")
        end = item.get("end") or item.get("end_time")
        area = item.get("area") or item.get("geometry") or item.get("polygon")

        closures.append({
            "id": item.get("id") or item.get("notam_id"),
            "title": title,
            "description": text[:600],
            "status": status,
            "start": start,
            "end": end,
            "area": area,
        })

    return closures


def build_airspace_response(
    closures: List[Dict[str, Any]],
    note: Optional[str] = None,
) -> Dict[str, Any]:
    response = {
        "generated_at": _utcnow_iso(),
        "total": len(closures),
        "closures": closures,
    }
    if note:
        response["note"] = note
    return response
