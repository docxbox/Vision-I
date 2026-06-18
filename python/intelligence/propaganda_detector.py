"""
intelligence/propaganda_detector.py
-----------------------------------
Detects coordinated messaging / herd mentality using simple text heuristics.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from storage.database import EventModel, get_session

_STOPWORDS = {
    "the", "and", "for", "with", "from", "this", "that", "into", "over", "under",
    "about", "after", "before", "today", "yesterday", "breaking", "update",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _signature(text: str, max_terms: int = 6) -> str:
    tokens = re.sub(r"[^a-z0-9]+", " ", text.lower()).split()
    tokens = [t for t in tokens if t and t not in _STOPWORDS]
    if not tokens:
        return ""
    return " ".join(tokens[:max_terms])


async def detect_propaganda(
    window_hours: int = 6,
    min_count: int = 4,
    min_sources: int = 2,
) -> Dict[str, Any]:
    cutoff = _utcnow() - timedelta(hours=window_hours)
    async with get_session() as session:
        rows = (
            await session.execute(
                EventModel.__table__.select()
                .where(EventModel.timestamp >= cutoff)
                .where(EventModel.title.is_not(None))
            )
        ).mappings().all()

    groups: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "count": 0,
        "sources": set(),
        "titles": [],
        "sentiments": [],
        "first_seen": None,
        "last_seen": None,
    })

    for row in rows:
        sig = _signature(row.get("title") or "")
        if not sig:
            continue
        grp = groups[sig]
        grp["count"] += 1
        grp["sources"].add(row.get("source") or "unknown")
        if len(grp["titles"]) < 5:
            grp["titles"].append(row.get("title"))
        if row.get("sentiment_score") is not None:
            grp["sentiments"].append(float(row.get("sentiment_score")))
        ts = row.get("timestamp")
        if ts:
            grp["first_seen"] = min(grp["first_seen"] or ts, ts)
            grp["last_seen"] = max(grp["last_seen"] or ts, ts)

    campaigns = []
    for sig, grp in groups.items():
        sources = list(grp["sources"])
        if grp["count"] < min_count or len(sources) < min_sources:
            continue
        avg_sent = (
            sum(grp["sentiments"]) / max(len(grp["sentiments"]), 1)
            if grp["sentiments"] else None
        )
        campaigns.append({
            "signature": sig,
            "count": grp["count"],
            "sources": sources,
            "sample_titles": grp["titles"],
            "first_seen": grp["first_seen"].isoformat() if grp["first_seen"] else None,
            "last_seen": grp["last_seen"].isoformat() if grp["last_seen"] else None,
            "avg_sentiment": round(avg_sent, 4) if avg_sent is not None else None,
        })

    campaigns.sort(key=lambda c: c["count"], reverse=True)
    return {
        "generated_at": _utcnow().isoformat(),
        "window_hours": window_hours,
        "campaigns": campaigns,
    }
