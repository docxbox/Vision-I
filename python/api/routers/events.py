"""
api/routers/events.py
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
GET /events          â€” paginated, filtered event list
GET /events/map      â€” GeoJSON FeatureCollection for Leaflet
GET /events/{id}     â€” single event detail

DB-backed when PostgreSQL is available; falls back to in-memory job store.
Response shape is identical in both modes.
"""

import logging
import base64
import html
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from api.routers.ingest import _jobs
from core.utils import utcnow_iso

logger = logging.getLogger("vision_i.api.events")
router = APIRouter(tags=["Events"])
_TRACKING_SOURCES = {"ais", "opensky"}

class EventListResponse(BaseModel):
    total: int = 0
    limit: int = 50
    offset: int = 0
    events: List[Any] = Field(default_factory=list)
    sources: List[Any] = Field(default_factory=list)

class EventItemSchema(BaseModel):
    model_config = ConfigDict(extra="allow")
    event_id: Optional[str] = None
    title: Optional[str] = None
    source: Optional[str] = None
    event_type: Optional[str] = None
    timestamp: Optional[str] = None

class GeoJSONGeometry(BaseModel):
    type: str = "Point"
    coordinates: List[float] = Field(default_factory=list)

class GeoJSONFeature(BaseModel):
    type: str = "Feature"
    geometry: GeoJSONGeometry
    properties: Dict[str, Any] = Field(default_factory=dict)

class GeoJSONResponse(BaseModel):
    type: str = "FeatureCollection"
    features: List[GeoJSONFeature] = Field(default_factory=list)
    generated_at: str = ""

class FeedGroupSchema(BaseModel):
    model_config = ConfigDict(extra="allow")
    group_key: Optional[str] = None
    group_type: Optional[str] = None
    title: Optional[str] = None
    severity: Optional[str] = None
    source_count: int = 0
    corroboration_score: float = 0.0
    event_count: int = 0
    events: List[Any] = Field(default_factory=list)

class FeedResponse(BaseModel):
    total: int = 0
    limit: int = 50
    offset: int = 0
    sort: str = "latest"
    group_by: str = "none"
    mode: str = "intelligence_feed"
    events: List[Any] = Field(default_factory=list)
    groups: List[Any] = Field(default_factory=list)

class EventWindowResponse(BaseModel):
    window: Dict[str, Any] = Field(default_factory=dict)
    offset: int = 0
    limit: int = 100
    has_more: bool = False
    next_cursor: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)

class EventSnapshotResponse(BaseModel):
    snapshot_at: str = ""
    lookback_hours: int = 24
    data: Dict[str, Any] = Field(default_factory=dict)

class SocialNarrativeSignal(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: Optional[str] = None
    label: Optional[str] = None
    confidence: float = 0.0
    detail: Optional[str] = None

class SocialNarrativeSchema(BaseModel):
    label: str = "organic"
    confidence: float = 0.0
    signals: List[Any] = Field(default_factory=list)

class EventSocialResponse(BaseModel):
    event_id: str = ""
    query: str = ""
    posts: List[Any] = Field(default_factory=list)
    narrative: SocialNarrativeSchema = Field(default_factory=SocialNarrativeSchema)
    narrative_db: List[Any] = Field(default_factory=list)
    generated_at: str = ""

class EventEnrichResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    status: str = ""
    event_id: Optional[str] = None
    signal_id: Optional[str] = None
    message: Optional[str] = None

class EnrichPayload(BaseModel):
    intel: str

def _memory_events(request: Request) -> List[Dict]:
    out: List[Dict] = []
    for job in sorted(_jobs.values(), key=lambda j: j.get("started_at", ""), reverse=True):
        out.extend(job.get("events") or [])
    return out


def _strip(e: Dict) -> Dict:
    return {k: v for k, v in e.items() if k != "raw"}


def _source_family(source: Optional[str]) -> str:
    raw = (source or "").lower()
    if raw.startswith("rss_"):
        return "rss"
    if raw.startswith("gdelt_"):
        return "gdelt"
    if raw.startswith("newsapi"):
        return "newsapi"
    if raw.startswith("yahoo_finance"):
        return "market"
    return raw or "unknown"


def _strip_html(text: Optional[str]) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"<[^>]+>", " ", text)
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _build_snippet(event: Dict, max_len: int = 220) -> str:
    parts = [
        _strip_html(event.get("description")),
        _strip_html(event.get("body")),
        _strip_html((event.get("extras") or {}).get("summary")),
    ]
    for part in parts:
        if part:
            return part[: max_len - 1] + "..." if len(part) > max_len else part
    return ""


def _engagement_from_extras(extras: Dict) -> Dict:
    keys = (
        "score",
        "upvote_ratio",
        "comment_count",
        "comments",
        "like_count",
        "retweet_count",
        "reply_count",
        "quote_count",
        "view_count",
        "watchers",
    )
    return {k: extras.get(k) for k in keys if extras.get(k) is not None}


def _feed_kind(event: Dict) -> str:
    event_type = (event.get("event_type") or "").lower()
    source_family = _source_family(event.get("source"))
    if "anomaly" in event_type:
        return "anomaly"
    if event_type in {"social", "video"} or source_family in {"reddit", "twitter", "youtube", "telegram"}:
        return "social"
    if event_type in {"market"} or source_family == "market":
        return "market"
    if event_type in {"disaster", "weather", "health"} or source_family in {"usgs", "who"}:
        return "alert"
    return "news"


def _is_feedworthy_event(event: Dict, include_tracking: bool = False) -> bool:
    source = (event.get("source") or "").lower()
    event_type = (event.get("event_type") or "").lower()
    if source in _TRACKING_SOURCES and not include_tracking:
        return False
    if source in _TRACKING_SOURCES and "anomaly" not in event_type:
        has_url = bool(event.get("url"))
        has_score = any(
            event.get(k) is not None and event.get(k) not in (0, 0.0)
            for k in ("risk_score", "influence_score")
        ) or (event.get("signal_count") or 0) > 0
        return has_url or has_score
    return True


def _project_feed_item(event: Dict, linked_situation: Optional[Dict]) -> Dict:
    extras = event.get("extras") or {}
    actors = event.get("actors") or []
    tags = event.get("tags") or []
    location = event.get("location") or {}
    source_family = _source_family(event.get("source"))
    feed_kind = _feed_kind(event)
    snippet = _build_snippet(event)
    return {
        **event,
        "snippet": snippet,
        "source_family": source_family,
        "feed_kind": feed_kind,
        "has_external_link": bool(event.get("url")),
        "actor_count": len(actors),
        "tag_count": len(tags),
        "engagement": _engagement_from_extras(extras),
        "location_summary": location.get("name") or location.get("country"),
        "linked_situation": linked_situation,
        "feed_summary": snippet or event.get("title"),
    }


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        cleaned = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned)
    except Exception:
        return None


def _event_age_hours(event: Dict) -> float:
    dt = _parse_iso(event.get("ingest_time")) or _parse_iso(event.get("timestamp"))
    if dt is None:
        return 24.0
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = now - dt.astimezone(timezone.utc)
    return max(delta.total_seconds() / 3600.0, 0.0)


def _compute_corroboration_score(event: Dict, linked_situation: Optional[Dict]) -> float:
    signal_count = float(event.get("signal_count") or 0)
    supporting = float(len(event.get("supporting_signals") or []))
    actor_count = float(len(event.get("actors") or []))
    linked_sources = float((linked_situation or {}).get("source_count") or 1)
    situation_hint = float((linked_situation or {}).get("corroboration_hint") or 0.0)
    source_url = 1.0 if event.get("url") else 0.0
    value = (
        min(signal_count, 6.0) / 6.0 * 0.35 +
        min(supporting, 6.0) / 6.0 * 0.15 +
        min(actor_count, 6.0) / 6.0 * 0.10 +
        min(linked_sources, 5.0) / 5.0 * 0.25 +
        source_url * 0.10 +
        min(situation_hint, 1.0) * 0.05
    )
    return round(min(value, 1.0), 3)


def _compute_feed_score(event: Dict, linked_situation: Optional[Dict], corroboration: float) -> float:
    risk = float(event.get("risk_score") or 0.0)
    influence = float(event.get("influence_score") or 0.0)
    signal_count = float(event.get("signal_count") or 0.0)
    actor_count = float(len(event.get("actors") or []))
    situation_risk = float((linked_situation or {}).get("risk_score") or 0.0)
    anomaly_bonus = 0.12 if "anomaly" in (event.get("event_type") or "").lower() else 0.0
    importance = (
        risk * 0.32 +
        situation_risk * 0.18 +
        min(influence, 1.0) * 0.08 +
        min(signal_count, 6.0) / 6.0 * 0.12 +
        min(actor_count, 6.0) / 6.0 * 0.08 +
        corroboration * 0.22 +
        anomaly_bonus
    )
    recency = 1.0 / (1.0 + (_event_age_hours(event) / 6.0))

    event_type = (event.get("event_type") or "").lower()
    source = (event.get("source") or "").lower()
    content_dt = _parse_iso(event.get("timestamp"))
    content_age_days = 0.0
    if content_dt is not None:
        if content_dt.tzinfo is None:
            content_dt = content_dt.replace(tzinfo=timezone.utc)
        content_age_days = max((datetime.now(timezone.utc) - content_dt.astimezone(timezone.utc)).total_seconds() / 86400.0, 0.0)

    stale_penalty = 1.0
    if source in {"reddit", "twitter", "youtube", "telegram"} or event_type in {"social", "video"}:
        if content_age_days > 180:
            stale_penalty = 0.10
        elif content_age_days > 90:
            stale_penalty = 0.18
        elif content_age_days > 45:
            stale_penalty = 0.35
    elif event_type == "news" and content_age_days > 30:
        stale_penalty = 0.45

    if (event.get("extras") or {}).get("trigger_type") == "auto_social":
        stale_penalty *= 0.8

    score = (importance * 0.7 + recency * 0.3) * stale_penalty
    return round(min(score, 1.0), 3)


def _content_age_days(event: Dict) -> float:
    content_dt = _parse_iso(event.get("timestamp"))
    if content_dt is None:
        return 0.0
    if content_dt.tzinfo is None:
        content_dt = content_dt.replace(tzinfo=timezone.utc)
    return max(
        (datetime.now(timezone.utc) - content_dt.astimezone(timezone.utc)).total_seconds() / 86400.0,
        0.0,
    )


def _is_priority_eligible(event: Dict, linked_situation: Optional[Dict]) -> bool:
    """
    Keep the priority feed focused on fresh or corroborated intelligence.

    The live table contains a long tail of social rows generated as automatic
    echoes off tracking anomalies. Those are useful as supporting evidence, but
    they should not outrank current reporting in the primary operator lane.
    """
    extras = event.get("extras") or {}
    source = (event.get("source") or "").lower()
    event_type = (event.get("event_type") or "").lower()
    age_days = _content_age_days(event)
    risk = float(event.get("risk_score") or 0.0)
    signal_count = int(event.get("signal_count") or 0)
    has_case = bool((linked_situation or {}).get("situation_id"))
    trigger_type = (extras.get("trigger_type") or "").lower()
    trigger_event_id = str(extras.get("trigger_event_id") or "").lower()
    social_like = source in {"reddit", "twitter", "youtube", "telegram"} or event_type in {"social", "video"}
    market_like = source == "yahoo_finance" or event_type == "market"

    if market_like and not has_case and risk < 0.55 and signal_count == 0:
        return False

    if social_like and trigger_type == "auto_social":
        if trigger_event_id.startswith("ais:") or trigger_event_id.startswith("opensky:"):
            return False

    if social_like:
        if age_days > 30 and not has_case:
            return False
        if age_days > 14 and risk < 0.55 and signal_count == 0 and not has_case:
            return False

    if event_type == "news" and age_days > 21 and risk < 0.25 and signal_count == 0 and not has_case:
        return False

    return True


def _apply_feed_scores(event: Dict, linked_situation: Optional[Dict]) -> Dict:
    linked_sources = int((linked_situation or {}).get("source_count") or 1)
    corroboration = _compute_corroboration_score(event, linked_situation)
    score = _compute_feed_score(event, linked_situation, corroboration)
    event["linked_source_count"] = linked_sources
    event["corroboration_score"] = corroboration
    event["feed_score"] = score
    return event


def _build_feed_groups(events: List[Dict]) -> List[Dict]:
    buckets: Dict[str, Dict] = {}
    for event in events:
        sit = event.get("linked_situation")
        if sit and sit.get("situation_id"):
            key = str(sit.get("situation_id"))
            bucket = buckets.setdefault(key, {
                "group_key": key,
                "group_type": "case",
                "title": sit.get("title") or f"Case {key}",
                "severity": sit.get("severity") or "medium",
                "region": sit.get("region"),
                "source_count": 0,
                "corroboration_score": 0.0,
                "event_count": 0,
                "events": [],
            })
        else:
            key = "unlinked"
            bucket = buckets.setdefault(key, {
                "group_key": key,
                "group_type": "unlinked",
                "title": "Unlinked Signals",
                "severity": "low",
                "region": None,
                "source_count": 0,
                "corroboration_score": 0.0,
                "event_count": 0,
                "events": [],
            })

        bucket["events"].append(event)

    for bucket in buckets.values():
        seen_sources = {
            (evt.get("source_family") or evt.get("source") or "").lower()
            for evt in bucket["events"]
            if evt.get("source_family") or evt.get("source")
        }
        bucket["source_count"] = len(seen_sources)
        bucket["event_count"] = len(bucket["events"])
        bucket["corroboration_score"] = round(
            sum(float(evt.get("corroboration_score") or 0.0) for evt in bucket["events"]) / max(len(bucket["events"]), 1),
            3,
        )
        bucket["events"].sort(
            key=lambda evt: (
                float(evt.get("feed_score") or 0.0),
                evt.get("ingest_time") or "",
                evt.get("timestamp") or "",
            ),
            reverse=True,
        )
    groups = list(buckets.values())
    groups.sort(
        key=lambda grp: (
            float(grp.get("corroboration_score") or 0.0),
            int(grp.get("source_count") or 0),
            int(grp.get("event_count") or 0),
        ),
        reverse=True,
    )
    return groups


def _build_situation_group(situation: Dict, events: List[Dict]) -> Optional[Dict]:
    if not events:
        return None

    linked_case = {
        "situation_id": situation.get("situation_id"),
        "title": situation.get("title"),
        "severity": situation.get("severity"),
        "risk_score": situation.get("risk_score"),
        "status": situation.get("status"),
        "region": situation.get("region"),
        "event_count": situation.get("event_count"),
        "source_count": (situation.get("meta") or {}).get("source_count"),
        "corroboration_hint": (situation.get("meta") or {}).get("intel_score"),
        "subcase_id": (situation.get("meta") or {}).get("subcase_id"),
        "parent_situation_id": (situation.get("meta") or {}).get("parent_situation_id"),
    }
    enriched_events = [
        {**evt, "linked_situation": linked_case}
        for evt in events
    ]
    ranked_events = sorted(
        enriched_events,
        key=lambda evt: (
            float(evt.get("feed_score") or 0.0),
            evt.get("ingest_time") or "",
            evt.get("timestamp") or "",
        ),
        reverse=True,
    )
    seen_sources = {
        (evt.get("source_family") or evt.get("source") or "").lower()
        for evt in ranked_events
        if evt.get("source_family") or evt.get("source")
    }
    meta = situation.get("meta") or {}
    effective_source_count = max(
        len({src for src in seen_sources if src}),
        int(meta.get("source_count") or 0),
    )
    corroboration = round(
        sum(float(evt.get("corroboration_score") or 0.0) for evt in ranked_events) / max(len(ranked_events), 1),
        3,
    )
    return {
        "group_key": situation.get("situation_id"),
        "group_type": "case",
        "title": situation.get("title") or "Case",
        "severity": situation.get("severity") or "medium",
        "region": situation.get("region"),
        "source_count": effective_source_count,
        "corroboration_score": corroboration,
        "event_count": len(ranked_events),
        "events": ranked_events,
    }


def _filter(events, source, event_type, query, sentiment, from_time, to_time):
    if source:      events = [e for e in events if e.get("source") == source]
    if event_type:  events = [e for e in events if e.get("event_type") == event_type]
    if sentiment:
        wanted = sentiment.upper()
        events = [
            e for e in events
            if ((e.get("sentiment") or {}).get("label") or "").upper() == wanted
        ]
    if from_time:   events = [e for e in events if (e.get("timestamp") or "") >= from_time]
    if to_time:     events = [e for e in events if (e.get("timestamp") or "") <= to_time]
    if query:
        q = query.lower()
        def matches(e: Dict[str, Any]) -> bool:
            loc = e.get("location") or {}
            searchable = " ".join([
                str(e.get("title") or ""),
                str(e.get("body") or ""),
                str(e.get("description") or ""),
                str(e.get("source") or ""),
                str(e.get("event_type") or ""),
                str(e.get("author") or ""),
                str(loc.get("name") or ""),
                str(loc.get("country") or ""),
                str(e.get("actors") or ""),
                str(e.get("tags") or ""),
                str(e.get("extras") or ""),
            ]).lower()
            return q in searchable

        events = [e for e in events if matches(e)]
    return events


def _geojson(events: List[Dict]) -> Dict:
    from core.geo import resolve_event_coords
    features = []
    for e in events:
        loc = e.get("location") or {}
        lat, lon = loc.get("lat"), loc.get("lon")
        inferred = False

        # On-the-fly geocoding for events stored before the geocoder existed
        if lat is None or lon is None:
            coords = resolve_event_coords(e)
            if coords:
                lat, lon = coords
                inferred = True

        if lat is None or lon is None:
            continue

        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]},
            "properties": {
                "event_id":     e.get("event_id"),
                "title":        e.get("title"),
                "source":       e.get("source"),
                "event_type":   e.get("event_type"),
                "timestamp":    e.get("timestamp"),
                "sentiment":    (e.get("sentiment") or {}).get("label"),
                "url":          e.get("url"),
                "name":         loc.get("name") or (e.get("location") or {}).get("country"),
                "inferred_loc": inferred,
            },
        })
    return {"type": "FeatureCollection", "features": features,
            "generated_at": utcnow_iso()}


def _enrich_locations(events: List[Dict]) -> List[Dict]:
    """Apply on-the-fly geocoding to events missing lat/lon."""
    from core.geo import apply_geocoding
    for e in events:
        loc = e.get("location") or {}
        if loc.get("lat") is None:
            apply_geocoding(e)
    return events

# In-process cache: (entity_set, expires_at_unix)
_ANOMALY_CACHE: Dict[str, object] = {"entities": set(), "expires": 0.0}
_ANOMALY_TTL_SEC = 60


async def _hot_anomaly_entities() -> set:
    """
    Returns lowercased entity names that triggered high-severity entity_spike
    alerts in the last hour. Cached for 60s to keep the events list cheap.
    """
    import time
    now = time.time()
    if _ANOMALY_CACHE["expires"] > now:
        return _ANOMALY_CACHE["entities"]   # type: ignore[return-value]

    entities: set = set()
    try:
        from datetime import datetime, timedelta, timezone
        from sqlalchemy import and_, select
        from storage.database import AlertModel, get_session

        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        async with get_session() as session:
            rows = (
                await session.execute(
                    select(AlertModel.entity)
                    .where(and_(
                        AlertModel.alert_type == "entity_spike",
                        AlertModel.severity.in_(("high", "critical")),
                        AlertModel.detected_at >= cutoff,
                        AlertModel.entity.isnot(None),
                    ))
                )
            ).fetchall()
            entities = {(r.entity or "").strip().lower() for r in rows if r.entity}
    except Exception as exc:
        logger.debug("Anomaly cache refresh failed: %s", exc)

    _ANOMALY_CACHE["entities"] = entities
    _ANOMALY_CACHE["expires"]  = now + _ANOMALY_TTL_SEC
    return entities


async def _apply_anomaly_flags(events: List[Dict]) -> List[Dict]:
    hot = await _hot_anomaly_entities()
    if not hot:
        for e in events:
            e["is_anomaly"] = False
        return events
    for e in events:
        names = {(a.get("name") or "").strip().lower() for a in (e.get("actors") or [])}
        e["is_anomaly"] = bool(names & hot)
    return events


def _encode_cursor(offset: int) -> str:
    return base64.b64encode(str(max(0, int(offset))).encode("utf-8")).decode("utf-8")


def _decode_cursor(cursor: Optional[str]) -> int:
    if not cursor:
        return 0
    try:
        raw = base64.b64decode(cursor.encode("utf-8")).decode("utf-8")
        value = int(raw)
        return value if value >= 0 else 0
    except Exception:
        return 0

@router.get("", summary="Paginated event list", response_model=EventListResponse)
async def list_events(
    request:    Request,
    source:     Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    query:      Optional[str] = Query(None),
    sentiment:  Optional[str] = Query(None),
    from_time:  Optional[str] = Query(None, alias="from"),
    to_time:    Optional[str] = Query(None, alias="to"),
    limit:      int           = Query(50, ge=1, le=1000),
    offset:     int           = Query(0,  ge=0),
    job_id:     Optional[str] = Query(None),
    sort_by:    str           = Query("latest", pattern="^(latest|timestamp|ingest|ingest_time|risk|risk_score)$"),
):
    if request.app.state.db_available and not job_id:
        try:
            from storage.database import get_session
            from storage.event_repo import EventRepository
            async with get_session() as session:
                repo = EventRepository(session)
                total, page = await repo.list_events(
                    source=source, event_type=event_type, query=query, sentiment=sentiment,
                    from_time=from_time, to_time=to_time,
                    limit=limit, offset=offset, sort_by=sort_by,
                )
                source_facets = await repo.list_source_facets(
                    query=query,
                    event_type=event_type,
                    sentiment=sentiment,
                    from_time=from_time,
                    to_time=to_time,
                )
            page = _enrich_locations(page)
            page = await _apply_anomaly_flags(page)
            return {"total": total, "limit": limit, "offset": offset, "events": page, "sources": source_facets}
        except Exception as exc:
            logger.warning("DB fallback: %s", exc)

    if job_id:
        job = _jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
        events = job.get("events") or []
    else:
        events = _memory_events(request)

    events = _filter(events, source, event_type, query, sentiment, from_time, to_time)
    events = _enrich_locations(events[offset: offset + limit])
    stripped = [_strip(e) for e in events]
    stripped = await _apply_anomaly_flags(stripped)
    source_facets = [
        {"source": src, "count": count}
        for src, count in sorted(
            {
                src: sum(1 for e in events if e.get("source") == src)
                for src in {e.get("source") for e in events if e.get("source")}
            }.items(),
            key=lambda item: item[1],
            reverse=True,
        )
    ]
    return {"total": len(events) + offset, "limit": limit, "offset": offset,
            "events": stripped, "sources": source_facets}


@router.get("/delta", summary="Events ingested since a cursor (incremental)")
async def events_delta(
    request: Request,
    since: Optional[str] = Query(None, description="ISO ingest_time cursor; returns only events ingested after it"),
    limit: int = Query(200, ge=1, le=1000),
):
    """
    Incremental delta: events ingested after `since` (by ingest_time), newest first.
    Drives the frontend's "patch the store" path instead of refetching the whole window.
    Response carries `cursor` = newest ingest_time returned; pass it back as `since` next time.
    """
    if not getattr(request.app.state, "db_available", False):
        return {"total": 0, "limit": limit, "offset": 0, "events": [], "sources": [], "cursor": since}

    from sqlalchemy import select
    from storage.database import get_session, EventModel
    from storage.event_repo import _row_to_event

    cursor_dt = None
    if since:
        try:
            cursor_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except Exception:
            cursor_dt = None

    async with get_session() as session:
        # NULLS LAST so the newest real ingest_time is rows[0] (Postgres DESC = NULLs first).
        stmt = (
            select(EventModel)
            .order_by(EventModel.ingest_time.desc().nulls_last())
            .limit(limit)
        )
        if cursor_dt is not None:
            stmt = stmt.where(EventModel.ingest_time > cursor_dt)
        rows = (await session.execute(stmt)).scalars().all()
        events = [_row_to_event(r) for r in rows]
        # Cursor from the row (rows are ordered by ingest_time desc); _row_to_event drops it.
        next_cursor = (
            rows[0].ingest_time.isoformat()
            if rows and getattr(rows[0], "ingest_time", None) else since
        )

    events = _enrich_locations(events)
    events = await _apply_anomaly_flags(events)
    return {"total": len(events), "limit": limit, "offset": 0,
            "events": events, "sources": [], "cursor": next_cursor}


@router.get("/feed", summary="Analyst intelligence feed", response_model=FeedResponse)
async def list_feed_events(
    request: Request,
    source: Optional[str] = Query(None),
    query: Optional[str] = Query(None),
    sentiment: Optional[str] = Query(None),
    from_time: Optional[str] = Query(None, alias="from"),
    to_time: Optional[str] = Query(None, alias="to"),
    sort: str = Query("latest", pattern="^(latest|priority)$"),
    group_by: str = Query("none", pattern="^(none|case)$"),
    include_tracking: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """
    Return an analyst-friendly intelligence feed.

    This deliberately excludes raw AIS/OpenSky churn so the feed can be rendered
    like an operator stream of articles, posts, anomalies, and contextualized
    alerts instead of a mixed telemetry firehose.
    """
    events: List[Dict]
    total: int
    situation_map: Dict[str, Dict] = {}

    if request.app.state.db_available:
        try:
            from storage.database import get_session
            from storage.event_repo import EventRepository
            from storage.situation_repo import (
                get_situation_membership_map,
                get_situations_by_ids,
                list_feed_situations,
            )

            async with get_session() as session:
                candidate_limit = limit
                candidate_offset = offset
                if sort == "priority" or group_by == "case":
                    candidate_limit = max(limit * 8, 200)
                    candidate_offset = 0
                total, events = await EventRepository(session).list_feed_events(
                    source=source,
                    query=query,
                    sentiment=sentiment,
                    from_time=from_time,
                    to_time=to_time,
                    sort=sort,
                    include_tracking=include_tracking,
                    limit=candidate_limit,
                    offset=candidate_offset,
                )
                situation_map = await get_situation_membership_map(
                    session,
                    [e.get("event_id") for e in events if e.get("event_id")],
                )
                situation_ids = [s.get("situation_id") for s in situation_map.values() if s.get("situation_id")]
                if situation_ids:
                    full_situations = await get_situations_by_ids(session, list(dict.fromkeys(situation_ids)))
                    by_sid = {s.get("situation_id"): s for s in full_situations if s.get("situation_id")}
                    member_ids = list({
                        eid
                        for sit in full_situations
                        for eid in (sit.get("event_ids") or [])
                        if eid
                    })
                    member_events = await EventRepository(session).get_events_by_ids(member_ids)
                    by_event_id = {e.get("event_id"): e for e in member_events if e.get("event_id")}
                    for event_id, membership in situation_map.items():
                        sid = membership.get("situation_id")
                        sit = by_sid.get(sid)
                        if not sit:
                            continue
                        related_sources = {
                            _source_family(by_event_id[eid].get("source"))
                            for eid in (sit.get("event_ids") or [])
                            if eid in by_event_id
                        }
                        membership["source_count"] = len({src for src in related_sources if src})
                        membership["corroboration_hint"] = round(
                            min(
                                (
                                    min(len(related_sources), 6) / 6.0 * 0.5
                                    + min(float(sit.get("event_count") or 0), 8.0) / 8.0 * 0.5
                                ),
                                    1.0,
                                ),
                                3,
                            )

                case_groups: List[Dict] = []
                if group_by == "case":
                    candidate_situations = await list_feed_situations(session, limit=max(12, min(limit, 24)))
                    candidate_situations = [
                        sit
                        for sit in candidate_situations
                        if int((sit.get("meta") or {}).get("source_count") or 0) >= 2
                        and int(sit.get("event_count") or 0) >= 2
                        and float(sit.get("risk_score") or 0.0) >= 0.05
                    ]
                    if candidate_situations:
                        case_event_ids = list(
                            dict.fromkeys(
                                eid
                                for sit in candidate_situations
                                for eid in (sit.get("event_ids") or [])
                                if eid
                            )
                        )
                        if case_event_ids:
                            case_member_events = await EventRepository(session).get_events_by_ids(case_event_ids)
                            case_member_events = _enrich_locations(case_member_events)
                            case_member_events = await _apply_anomaly_flags(case_member_events)
                            by_case_event_id = {}
                            for evt in case_member_events:
                                linked = situation_map.get(evt.get("event_id"))
                                if not _is_feedworthy_event(evt, include_tracking=include_tracking):
                                    continue
                                projected_evt = _apply_feed_scores(_project_feed_item(evt, linked), linked)
                                if not _is_priority_eligible(projected_evt, linked):
                                    continue
                                by_case_event_id[evt.get("event_id")] = projected_evt

                            for sit in candidate_situations:
                                sit_events = [
                                    {
                                        **by_case_event_id[eid],
                                        "linked_situation": by_case_event_id[eid].get("linked_situation") or {
                                            "situation_id": sit.get("situation_id"),
                                            "title": sit.get("title"),
                                            "severity": sit.get("severity"),
                                            "risk_score": sit.get("risk_score"),
                                            "status": sit.get("status"),
                                            "region": sit.get("region"),
                                            "event_count": sit.get("event_count"),
                                            "source_count": int((sit.get("meta") or {}).get("source_count") or 0),
                                            "corroboration_hint": round(
                                                min(
                                                    (
                                                        min(int((sit.get("meta") or {}).get("source_count") or 0), 6) / 6.0 * 0.5
                                                        + min(float(sit.get("event_count") or 0), 8.0) / 8.0 * 0.5
                                                    ),
                                                    1.0,
                                                ),
                                                3,
                                            ),
                                        },
                                    }
                                    for eid in (sit.get("event_ids") or [])
                                    if eid in by_case_event_id
                                ]
                                group_payload = _build_situation_group(sit, sit_events)
                                if group_payload and int(group_payload.get("source_count") or 0) >= 2:
                                    case_groups.append(group_payload)
                            case_groups.sort(
                                key=lambda grp: (
                                    float(grp.get("corroboration_score") or 0.0),
                                    int(grp.get("source_count") or 0),
                                    int(grp.get("event_count") or 0),
                                ),
                                reverse=True,
                            )
            events = _enrich_locations(events)
            events = await _apply_anomaly_flags(events)
            projected = [
                _apply_feed_scores(
                    _project_feed_item(e, situation_map.get(e.get("event_id"))),
                    situation_map.get(e.get("event_id")),
                )
                for e in events
                if _is_feedworthy_event(e, include_tracking=include_tracking)
            ]
            projected = [
                e for e in projected
                if sort != "priority" or _is_priority_eligible(e, e.get("linked_situation"))
            ]
            if sort == "priority":
                projected.sort(key=lambda e: float(e.get("feed_score") or 0.0), reverse=True)
                projected = projected[offset: offset + limit]
            elif offset or len(projected) > limit:
                projected = projected[offset: offset + limit]

            groups = case_groups if group_by == "case" and case_groups else (_build_feed_groups(projected) if group_by == "case" else [])
            return {
                "total": total,
                "limit": limit,
                "offset": offset,
                "sort": sort,
                "group_by": group_by,
                "mode": "intelligence_feed",
                "events": projected,
                "groups": groups,
            }
        except Exception as exc:
            logger.warning("DB feed fallback: %s", exc)

    events = _memory_events(request)
    events = _filter(events, source, None, query, sentiment, from_time, to_time)
    events = [_strip(e) for e in events if _is_feedworthy_event(e, include_tracking=include_tracking)]
    events = _enrich_locations(events)
    events = await _apply_anomaly_flags(events)
    if sort == "priority":
        events.sort(
            key=lambda e: (
                e.get("risk_score") or 0,
                e.get("signal_count") or 0,
                e.get("influence_score") or 0,
                e.get("timestamp") or "",
            ),
            reverse=True,
        )
    else:
        events.sort(key=lambda e: (e.get("ingest_time") or "", e.get("timestamp") or ""), reverse=True)

    page = events[offset: offset + limit]
    projected = [_apply_feed_scores(_project_feed_item(e, None), None) for e in page]
    if sort == "priority":
        projected = [e for e in projected if _is_priority_eligible(e, None)]
        projected.sort(key=lambda e: float(e.get("feed_score") or 0.0), reverse=True)
    groups = _build_feed_groups(projected) if group_by == "case" else []
    return {
        "total": len(events),
        "limit": limit,
        "offset": offset,
        "sort": sort,
        "group_by": group_by,
        "mode": "intelligence_feed",
        "events": projected,
        "groups": groups,
    }


@router.get("/map", summary="GeoJSON for Leaflet map", response_model=GeoJSONResponse)
async def events_map(
    request:    Request,
    source:     Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    from_time:  Optional[str] = Query(None, alias="from"),
    to_time:    Optional[str] = Query(None, alias="to"),
    limit:      int           = Query(500, ge=1, le=2000),
):
    if request.app.state.db_available:
        try:
            from storage.database import get_session
            from storage.event_repo import EventRepository
            async with get_session() as session:
                rows = await EventRepository(session).get_map_events(
                    source=source, event_type=event_type,
                    from_time=from_time, to_time=to_time, limit=limit,
                )
            return _geojson(rows)
        except Exception as exc:
            logger.warning("DB fallback: %s", exc)

    events = _filter(_memory_events(request), source, event_type, None, from_time, to_time)
    return _geojson(events[:limit])


@router.get("/window", summary="Playback window with cursor pagination", response_model=EventWindowResponse)
async def events_window(
    request:    Request,
    source:     Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    query:      Optional[str] = Query(None),
    from_time:  Optional[str] = Query(None, alias="from"),
    to_time:    Optional[str] = Query(None, alias="to"),
    limit:      int           = Query(100, ge=1, le=500),
    cursor:     Optional[str] = Query(None),
):
    offset = _decode_cursor(cursor)
    payload = await list_events(
        request=request,
        source=source,
        event_type=event_type,
        query=query,
        sentiment=None,
        from_time=from_time,
        to_time=to_time,
        limit=limit,
        offset=offset,
        job_id=None,
    )
    total = int(payload.get("total") or 0)
    returned = len(payload.get("events") or [])
    next_offset = offset + returned
    has_more = next_offset < total
    return {
        "window": {"from": from_time, "to": to_time},
        "offset": offset,
        "limit": limit,
        "has_more": has_more,
        "next_cursor": _encode_cursor(next_offset) if has_more else None,
        "data": payload,
    }


@router.get("/snapshot", summary="Event snapshot at time", response_model=EventSnapshotResponse)
async def events_snapshot(
    request: Request,
    at: Optional[str] = Query(None, description="ISO timestamp; default now"),
    lookback_hours: int = Query(24, ge=1, le=168),
    limit: int = Query(200, ge=1, le=500),
):
    # Snapshot is modeled as a window ending at `at`.
    from datetime import datetime, timezone, timedelta
    dt = datetime.now(timezone.utc)
    if at:
        try:
            dt = datetime.fromisoformat(at.replace("Z", "+00:00"))
        except Exception:
            pass
    from_time = (dt - timedelta(hours=lookback_hours)).isoformat().replace("+00:00", "Z")
    to_time = dt.isoformat().replace("+00:00", "Z")
    payload = await list_events(
        request=request,
        source=None,
        event_type=None,
        query=None,
        sentiment=None,
        from_time=from_time,
        to_time=to_time,
        limit=limit,
        offset=0,
        job_id=None,
    )
    return {
        "snapshot_at": to_time,
        "lookback_hours": lookback_hours,
        "data": payload,
    }


@router.get("/{event_id}", summary="Single event detail", response_model=EventItemSchema)
async def get_event(event_id: str, request: Request):
    from core.geo import apply_geocoding
    if request.app.state.db_available:
        try:
            from storage.database import get_session
            from storage.event_repo import EventRepository
            async with get_session() as session:
                event = await EventRepository(session).get_event(event_id)
            if event:
                apply_geocoding(event)
                return event
        except Exception as exc:
            logger.warning("DB fallback: %s", exc)

    for e in _memory_events(request):
        if e.get("event_id") == event_id:
            e = _strip(e)
            apply_geocoding(e)
            return e

    raise HTTPException(status_code=404, detail=f"Event '{event_id}' not found")

@router.get("/{event_id}/social", summary="Social media correlation for event", response_model=EventSocialResponse)
async def get_event_social(
    event_id: str,
    request: Request,
    limit: int = Query(20, ge=1, le=50),
):
    """
    Queries social platforms in real-time for posts correlated to this event.
    Returns ranked posts + propaganda / herd-mentality / influence classification.
    """
    event = None
    if request.app.state.db_available:
        try:
            import asyncio
            from storage.database import get_session
            from storage.event_repo import EventRepository

            async def _get_event():
                async with get_session() as session:
                    return await EventRepository(session).get_event(event_id)

            event = await asyncio.wait_for(_get_event(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("DB event lookup timed out for social: %s", event_id)
        except Exception as exc:
            logger.warning("DB event lookup failed: %s", exc)

    if not event:
        for e in _memory_events(request):
            if e.get("event_id") == event_id:
                event = _strip(e)
                break

    if not event:
        raise HTTPException(status_code=404, detail=f"Event '{event_id}' not found")
    title = event.get("title") or ""
    actors = [
        a.get("name", "") for a in (event.get("actors") or [])
        if isinstance(a, dict) and a.get("name")
    ]
    tags = [t for t in (event.get("tags") or []) if isinstance(t, str)]
    query = title[:80] if title else " ".join((actors + tags)[:3])

    if not query.strip():
        return {
            "event_id": event_id, "query": "", "posts": [],
            "narrative": {"label": "insufficient_data", "confidence": 0.0, "signals": []},
            "narrative_db": [], "generated_at": utcnow_iso(),
        }
    posts: list = []
    try:
        from extractors.socials import SocialExtractor, RedditExtractor, YouTubeExtractor
        import asyncio
        # On-demand correlation: use Reddit (site-wide, not OSINT-only) + YouTube
        # so we get the broadest social reaction to a specific news event.
        extractor = SocialExtractor(
            sources=[RedditExtractor(), YouTubeExtractor()],
            osint_only=False,
        )
        loop = asyncio.get_running_loop()
        raw = await asyncio.wait_for(
            loop.run_in_executor(
                None, lambda: extractor.collect(query=query, limit=limit + 10)
            ),
            timeout=12.0,
        )
        posts = [_strip(p) for p in raw]
    except asyncio.TimeoutError:
        logger.warning("Social extraction timed out after 12 s")
    except Exception as exc:
        logger.warning("Social extraction failed: %s", exc)
    keywords = _social_keywords(title, actors, tags)
    scored: list = []
    for post in posts:
        text = ((post.get("title") or "") + " " + (post.get("body") or "")).lower()
        hits = sum(1 for kw in keywords if kw in text)
        rel = round(hits / max(len(keywords), 1), 3)
        post["_relevance"] = rel
        scored.append((post, rel))

    scored.sort(key=lambda x: x[1], reverse=True)
    ranked = [p for p, _ in scored][:limit]
    narrative = _classify_social_narrative(ranked)
    db_narratives: list = []
    if request.app.state.db_available:
        try:
            from storage.database import get_session
            from storage.intelligence_repo import NarrativeRepository
            async with get_session() as session:
                repo = NarrativeRepository(session)
                all_narr = await repo.list_narratives(limit=30)
                kw_set = {kw.lower() for kw in keywords}
                for n in all_narr:
                    n_d = n if isinstance(n, dict) else vars(n)
                    topic = (n_d.get("topic") or "").lower()
                    n_actors = [str(a).lower() for a in (n_d.get("actors") or [])]
                    if any(kw in topic for kw in kw_set) or any(
                        any(kw in a for kw in kw_set) for a in n_actors
                    ):
                        db_narratives.append(n_d)
                    if len(db_narratives) >= 5:
                        break
        except Exception as exc:
            logger.warning("Narrative DB lookup failed: %s", exc)

    return {
        "event_id": event_id,
        "query": query,
        "posts": ranked,
        "narrative": narrative,
        "narrative_db": db_narratives,
        "generated_at": utcnow_iso(),
    }


def _social_keywords(title: str, actors: list, tags: list) -> list:
    stops = {
        "about", "after", "again", "their", "there", "where", "which",
        "while", "would", "could", "should", "other", "being", "having",
        "these", "those", "with", "from", "that", "this", "were", "have",
    }
    kws: list = []
    if title:
        kws.extend(
            w.lower() for w in title.split()
            if len(w) >= 4 and w.lower() not in stops
        )
    kws.extend(a.lower() for a in actors if len(a) >= 3)
    kws.extend(t.lower() for t in tags if len(t) >= 3)
    # deduplicate preserving order
    seen: set = set()
    return [k for k in kws if not (k in seen or seen.add(k))][:12]  # type: ignore[func-returns-value]


def _classify_social_narrative(posts: list) -> dict:
    """
    Detect propaganda, herd-mentality, and influence-operation patterns.
    Returns a classification dict with a primary label + per-signal breakdown.
    """
    if not posts:
        return {"label": "insufficient_data", "confidence": 0.0, "signals": []}

    signals: list = []
    title_sets = [
        set((p.get("title") or "").lower().split())
        for p in posts if (p.get("title") or "").strip()
    ]
    if len(title_sets) >= 3:
        similar = total = 0
        for i in range(len(title_sets)):
            for j in range(i + 1, len(title_sets)):
                a, b = title_sets[i], title_sets[j]
                if a and b:
                    jacc = len(a & b) / len(a | b)
                    if jacc > 0.45:
                        similar += 1
                    total += 1
        if total > 0 and similar / total > 0.25:
            signals.append({
                "type": "propaganda",
                "label": "COORDINATED MESSAGING",
                "confidence": round(min(similar / total * 2.5, 1.0), 3),
                "detail": f"{similar} near-duplicate post pairs out of {total} pairs",
            })
    sent_scores = [
        float(p["sentiment"]["score"])
        for p in posts
        if isinstance(p.get("sentiment"), dict) and "score" in p["sentiment"]
    ]
    if len(sent_scores) >= 4:
        import statistics
        try:
            variance = statistics.variance(sent_scores)
            mean_s = statistics.mean(sent_scores)
            if variance < 0.06 and len(posts) >= 5:
                pol = "NEGATIVE" if mean_s < 0.4 else "POSITIVE" if mean_s > 0.6 else "NEUTRAL"
                signals.append({
                    "type": "herd_mentality",
                    "label": f"HERD MENTALITY â€” {pol}",
                    "confidence": round(max(0.0, 1.0 - variance * 12), 3),
                    "detail": f"Sentiment variance={variance:.4f}, mean={mean_s:.2f}, n={len(posts)}",
                })
        except Exception:
            pass
    high_eng = [
        p for p in posts
        if isinstance(p.get("extras"), dict)
        and (p["extras"].get("score", 0) > 500 or p["extras"].get("upvote_ratio", 0) > 0.92)
    ]
    if high_eng:
        signals.append({
            "type": "influence",
            "label": "INFLUENCE OPERATION",
            "confidence": round(min(len(high_eng) / max(len(posts), 1) * 3, 1.0), 3),
            "detail": f"{len(high_eng)} high-engagement posts (score>500 or upvote_ratio>0.92)",
        })

    if not signals:
        return {
            "label": "organic",
            "confidence": 0.75,
            "signals": [{"type": "organic", "label": "ORGANIC DISCOURSE", "confidence": 0.75,
                         "detail": "No anomalous coordination patterns detected"}],
        }

    top = max(signals, key=lambda s: s["confidence"])
    return {"label": top["type"], "confidence": top["confidence"], "signals": signals}



@router.post("/{event_id}/enrich", summary="Upload Analyst Field Intelligence", response_model=EventEnrichResponse)
async def enrich_event(event_id: str, payload: EnrichPayload, request: Request):
    """
    Manually inject Analyst Intelligence (Lockheed Martin Protocol).
    Converts raw field intel into pgvector space and structurally binds to the event.
    """
    if not request.app.state.db_available:
        return {"status": "error", "message": "Vector DB offline"}
    
    try:
        from storage.database import get_session, SignalModel
        import hashlib
        import json

        payload_hash = hashlib.sha256(payload.intel.encode()).hexdigest()

        async with get_session() as session:
            sig = SignalModel(
                signal_id=f"intel_{payload_hash[:12]}",
                source_event_id=event_id,
                source="field_analyst",
                signal_type="human_intelligence",
                title="Field Analyst Context",
                body=payload.intel,
                confidence=1.0,
                meta={"uploaded_by": "analyst"}
            )
            session.add(sig)
            await session.commit()

        return {"status": "success", "event_id": event_id, "signal_id": sig.signal_id}

    except Exception as exc:
        logger.error("Event enrich failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)
