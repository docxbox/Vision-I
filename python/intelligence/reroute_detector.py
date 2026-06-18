"""
intelligence/reroute_detector.py
--------------------------------
Detects potential flight reroutes or holding patterns from asset tracks.
Uses asset history when available; degrades gracefully otherwise.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from storage.database import AssetModel, get_session


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _bearing_delta(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    delta = abs(a - b) % 360
    if delta > 180:
        delta = 360 - delta
    return delta


async def detect_reroutes(
    window_hours: int = 6,
    min_turn_deg: float = 60.0,
    min_history: int = 2,
    limit: int = 50,
) -> Dict[str, Any]:
    cutoff = _utcnow() - timedelta(hours=window_hours)
    results: List[Dict[str, Any]] = []

    async with get_session() as session:
        rows = (
            await session.execute(
                AssetModel.__table__.select()
                .where(AssetModel.asset_type == "aircraft")
                .where(AssetModel.last_seen >= cutoff)
                .order_by(AssetModel.last_seen.desc())
                .limit(500)
            )
        ).mappings().all()

    for row in rows:
        history = row.get("track_history") or []
        if len(history) < min_history:
            continue
        last = history[-1]
        prev = history[-2]
        delta = _bearing_delta(last.get("heading"), prev.get("heading"))
        if delta is None or delta < min_turn_deg:
            continue

        results.append({
            "asset_id": row.get("asset_id"),
            "callsign": row.get("callsign"),
            "identifier": row.get("identifier"),
            "last_seen": row.get("last_seen").isoformat() if row.get("last_seen") else None,
            "turn_deg": round(delta, 1),
            "last_lat": row.get("last_lat"),
            "last_lon": row.get("last_lon"),
            "note": "abrupt_heading_change",
        })

    return {
        "generated_at": _utcnow().isoformat(),
        "window_hours": window_hours,
        "total": len(results),
        "events": results[:limit],
        "note": "Reroute detection is heuristic and requires track_history on assets.",
    }
