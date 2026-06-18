"""
api/routers/sentiment.py
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
GET /sentiment/timeline â€” aggregated sentiment over time for a query or entity

DB-backed: queries the events table, groups by time bucket, returns a time series.
Memory fallback: derives a simple timeline from the in-memory job store.
"""

import logging
import math
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from api.routers.ingest import _jobs

logger = logging.getLogger("vision_i.api.sentiment")
router = APIRouter(tags=["Sentiment"])

# ── Response schemas ───────────────────────────────────────────────────────

class SentimentTimelineResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    bucket_size: str = "day"
    query: Optional[str] = None
    source: Optional[str] = None
    entity_id: Optional[str] = None
    data: List[Any] = Field(default_factory=list)
    count: int = 0

class SentimentCountryHeatmapResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    generated_at: str = ""
    countries: List[Any] = Field(default_factory=list)

def _memory_timeline(
    query: Optional[str],
    source: Optional[str],
    entity_id: Optional[str],
) -> List[Dict]:
    """
    Derives a day-bucketed sentiment timeline from in-memory events.
    Produced when PostgreSQL is unavailable.
    """
    buckets: Dict[str, Dict] = defaultdict(lambda: {
        "avg_score": 0.0, "event_count": 0,
        "positive": 0, "neutral": 0, "negative": 0, "_sum": 0.0,
    })

    for job in _jobs.values():
        for e in job.get("events") or []:
            if source and e.get("source") != source:
                continue
            if query:
                q = query.lower()
                if q not in (e.get("title") or "").lower() and q not in (e.get("body") or "").lower():
                    continue
            if entity_id:
                actor_ids = [
                    f"actor:{(a.get('name') or '').lower().replace(' ', '_')}"
                    for a in e.get("actors") or []
                ]
                if entity_id not in actor_ids:
                    continue

            sentiment = e.get("sentiment") or {}
            score     = sentiment.get("score")
            label     = (sentiment.get("label") or "").upper()
            ts        = (e.get("timestamp") or "")[:10]   # YYYY-MM-DD

            if not ts or score is None:
                continue

            b = buckets[ts]
            b["event_count"] += 1
            b["_sum"] += score
            b["avg_score"] = round(b["_sum"] / b["event_count"], 4)
            if label == "POSITIVE":  b["positive"] += 1
            elif label == "NEGATIVE": b["negative"] += 1
            else:                     b["neutral"]  += 1

    return [
        {
            "bucket":      f"{day}T00:00:00Z",
            "avg_score":   v["avg_score"],
            "event_count": v["event_count"],
            "positive":    v["positive"],
            "neutral":     v["neutral"],
            "negative":    v["negative"],
        }
        for day, v in sorted(buckets.items())
    ]


async def _country_sentiment_db(days_back: int = 7) -> List[Dict]:
    from sqlalchemy import select
    from core.geo import geocode, resolve_event_country
    from storage.database import EventModel, get_session

    social_sources = {"reddit", "youtube", "telegram", "hackernews", "twitter"}
    window_start = datetime.now(timezone.utc) - timedelta(days=max(days_back, 1))

    async with get_session() as session:
        result = await session.execute(
            select(
                EventModel.source,
                EventModel.sentiment_score,
                EventModel.sentiment_label,
                EventModel.location_name,
                EventModel.actors,
                EventModel.extras,
            ).where(
                EventModel.sentiment_score.isnot(None),
                EventModel.timestamp.isnot(None),
                EventModel.timestamp > window_start,
            )
        )
        rows = result.all()

    aggregates = {}
    for row in rows:
        event = {
            "source": row.source,
            "location": {"name": row.location_name} if row.location_name else {},
            "actors": row.actors or [],
            "extras": row.extras or {},
        }
        country = resolve_event_country(event)
        if not country:
            continue

        source = (row.source or "unknown").lower()
        score = float(row.sentiment_score or 0.5)
        is_negative = (row.sentiment_label or "").upper() == "NEGATIVE"
        is_social = source in social_sources

        agg = aggregates.setdefault(country, {
            "sum": 0.0,
            "count": 0,
            "negative": 0,
            "social": 0,
            "negative_social": 0,
            "sources": {},
        })
        agg["sum"] += score
        agg["count"] += 1
        agg["negative"] += int(is_negative)
        agg["social"] += int(is_social)
        agg["negative_social"] += int(is_social and is_negative)
        agg["sources"][source] = agg["sources"].get(source, 0) + 1

    payload = []
    max_count = max((agg["count"] for agg in aggregates.values()), default=1)
    for country, agg in aggregates.items():
        coords = geocode(country)
        if not coords:
            logger.debug("country heatmap skipped unmapped country: %s", country)
            continue

        lat, lon = coords
        avg_score = agg["sum"] / max(agg["count"], 1)
        negative_ratio = agg["negative"] / max(agg["count"], 1)
        social_ratio = agg["social"] / max(agg["count"], 1)
        negative_social_ratio = agg["negative_social"] / max(agg["social"], 1) if agg["social"] else 0.0
        weight = math.log(agg["count"] + 1) / math.log(max_count + 1)
        risk_signal = (
            (1.0 - avg_score) * 0.3 +
            negative_ratio * 0.45 +
            negative_social_ratio * 0.2 +
            social_ratio * 0.05
        )
        risk_score = round(min(1.0, max(0.0, risk_signal) * (0.45 + (weight * 0.55))), 4)
        payload.append({
            "country": country,
            "lat": round(lat, 4),
            "lon": round(lon, 4),
            "sentiment": round(avg_score, 4),
            "count": agg["count"],
            "avg_score": round(avg_score, 4),
            "event_count": agg["count"],
            "negative_count": agg["negative"],
            "negative_ratio": round(negative_ratio, 4),
            "social_count": agg["social"],
            "negative_social_count": agg["negative_social"],
            "social_ratio": round(social_ratio, 4),
            "negative_social_ratio": round(negative_social_ratio, 4),
            "source_count": len(agg["sources"]),
            "source_breakdown": dict(sorted(agg["sources"].items(), key=lambda item: item[1], reverse=True)),
            "risk_score": risk_score,
        })
    payload.sort(key=lambda item: item["risk_score"], reverse=True)
    return payload

@router.get("/timeline", summary="Sentiment scores aggregated over time", response_model=SentimentTimelineResponse)
async def sentiment_timeline(
    request:   Request,
    query:     Optional[str] = Query(None, description="Full-text filter on title/body"),
    source:    Optional[str] = Query(None, description="Filter by source name"),
    entity_id: Optional[str] = Query(None, description="Filter by actor entity ID"),
    entity_alias: Optional[str] = Query(None, alias="entity", description="Backward-compatible actor entity ID"),
    from_time: Optional[str] = Query(None, alias="from", description="ISO 8601 start"),
    to_time:   Optional[str] = Query(None, alias="to",   description="ISO 8601 end"),
    hours:     Optional[int] = Query(None, ge=1, le=24 * 365, description="Relative lookback window in hours"),
    bucket:    str            = Query("day", description="Time bucket: hour | day | week"),
):
    """
    Returns a time series of average sentiment scores, positive/neutral/negative
    counts, and total event counts per time bucket.

    Used by the frontend sentiment timeline chart (Chart.js).
    """
    _ALLOWED_BUCKETS = ("hour", "day", "week")
    if bucket not in _ALLOWED_BUCKETS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid bucket '{bucket}'. Allowed values: {', '.join(_ALLOWED_BUCKETS)}",
        )

    entity_id = entity_id or entity_alias
    if hours is not None:
        if from_time is None:
            from_time = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        if to_time is None:
            to_time = datetime.now(timezone.utc).isoformat()
    if request.app.state.db_available:
        try:
            from storage.database import get_session
            from storage.event_repo import EventRepository
            async with get_session() as session:
                timeline = await EventRepository(session).get_sentiment_timeline(
                    query=query, source=source,
                    from_time=from_time, to_time=to_time,
                    bucket=bucket,
                )
            return {
                "bucket_size": bucket,
                "query":       query,
                "source":      source,
                "entity_id":   entity_id,
                "data":        timeline,
                "count":       len(timeline),
            }
        except Exception as exc:
            logger.warning("DB sentiment_timeline fallback: %s", exc)
    timeline = _memory_timeline(query, source, entity_id)
    return {
        "bucket_size": "day",
        "query":       query,
        "source":      source,
        "entity_id":   entity_id,
        "data":        timeline,
        "count":       len(timeline),
        "note":        "memory-only mode â€” connect PostgreSQL for full history",
    }


@router.get("/country-heatmap", summary="Sentiment and risk per country", response_model=SentimentCountryHeatmapResponse)
async def sentiment_country_heatmap(
    request: Request,
    days_back: int = Query(7, ge=1, le=30, description="Lookback window in days"),
):
    """
    Returns per-country sentiment aggregates suitable for map heat layers.
    """
    if days_back == 7 and getattr(request.app.state, "event_bus", None):
        try:
            cached = await request.app.state.event_bus.cache_get("precomputed:country_sentiment")
            if isinstance(cached, dict):
                countries = cached.get("countries")
                if isinstance(countries, list):
                    from core.geo import geocode

                    normalized = []
                    for item in countries:
                        if not isinstance(item, dict):
                            continue

                        entry = dict(item)
                        if entry.get("lat") is None or entry.get("lon") is None:
                            coords = geocode(entry.get("country"))
                            if not coords:
                                continue
                            entry["lat"] = round(coords[0], 4)
                            entry["lon"] = round(coords[1], 4)

                        entry.setdefault("sentiment", entry.get("avg_score", 0.5))
                        entry.setdefault("count", entry.get("event_count", 1))
                        normalized.append(entry)

                    cached["countries"] = normalized
                    if not normalized or all("social_count" in entry and "negative_social_count" in entry for entry in normalized):
                        return cached
        except Exception:
            pass

    if request.app.state.db_available:
        data = await _country_sentiment_db(days_back=days_back)
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "countries": data,
        }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "countries": [],
        "note": "memory-only mode â€” connect PostgreSQL for full history",
    }

