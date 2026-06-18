"""
intelligence/narrative_timeline.py
----------------------------------
Timeline aggregation for narrative evolution.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from storage.database import get_session


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def get_narrative_timeline(
    topic: Optional[str] = None,
    bucket: str = "day",
    days_back: int = 7,
) -> Dict[str, Any]:
    if bucket not in {"hour", "day", "week"}:
        bucket = "day"
    days_back = max(1, min(days_back, 90))

    where = ""
    params = {"days_back": days_back}
    if topic:
        where = "AND topic ILIKE :topic"
        params["topic"] = f"%{topic}%"

    async with get_session() as session:
        result = await session.execute(text(f"""
            SELECT date_trunc('{bucket}', detected_at) AS bucket,
                   COUNT(*) AS cnt
            FROM narratives
            WHERE detected_at >= NOW() - (INTERVAL '1 day' * :days_back)
            {where}
            GROUP BY bucket
            ORDER BY bucket
        """), params)
        rows = result.fetchall()

    data = [
        {
            "bucket": row.bucket.isoformat() + "Z" if row.bucket else None,
            "count": int(row.cnt),
        }
        for row in rows
    ]

    return {
        "generated_at": _utcnow().isoformat(),
        "bucket": bucket,
        "topic": topic,
        "data": data,
        "count": len(data),
    }
