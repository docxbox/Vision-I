"""
intelligence/jarvis.py
----------------------
Rule-based tactical summary + COA suggestions.
Designed to run without an LLM (deterministic, fast).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from sqlalchemy import text
from storage.database import get_session


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def build_tactical_summary(window_hours: int = 6) -> Dict[str, Any]:
    cutoff = _utcnow() - timedelta(hours=window_hours)
    async with get_session() as session:
        alerts = (await session.execute(text("""
            SELECT severity, title, detected_at
            FROM alerts
            WHERE detected_at >= :cutoff
            ORDER BY detected_at DESC
            LIMIT 10
        """), {"cutoff": cutoff})).mappings().all()

        narratives = (await session.execute(text("""
            SELECT topic, severity, detected_at
            FROM narratives
            WHERE detected_at >= :cutoff
            ORDER BY detected_at DESC
            LIMIT 10
        """), {"cutoff": cutoff})).mappings().all()

        events = (await session.execute(text("""
            SELECT source, event_type, title, timestamp
            FROM events
            WHERE timestamp >= :cutoff
            ORDER BY timestamp DESC
            LIMIT 10
        """), {"cutoff": cutoff})).mappings().all()

    risk_score = min(1.0, 0.1 * len(alerts) + 0.05 * len(narratives))

    coas = []
    if alerts:
        coas.append("Increase monitoring on active alert zones and verify anomalous assets.")
    if narratives:
        coas.append("Review narrative spikes for coordinated amplification and validate sources.")
    if not coas:
        coas.append("Maintain baseline monitoring; no critical spikes detected.")

    return {
        "generated_at": _utcnow().isoformat(),
        "window_hours": window_hours,
        "risk_score": round(risk_score, 3),
        "alerts": [{"severity": a["severity"], "title": a["title"]} for a in alerts],
        "narratives": [{"topic": n["topic"], "severity": n["severity"]} for n in narratives],
        "recent_events": [{"source": e["source"], "event_type": e["event_type"], "title": e["title"]} for e in events],
        "coas": coas,
    }
