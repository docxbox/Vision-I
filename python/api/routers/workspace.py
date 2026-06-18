"""
api/routers/workspace.py
─────────────────────────
Internal workspace resolver endpoints — called only by .NET API.

POST /workspace/resolve-events
POST /workspace/resolve-assets
POST /workspace/resolve-sentiment
POST /workspace/resolve-entities
POST /workspace/resolve-correlation
"""

import asyncio
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("vision_i.api.workspace")
router = APIRouter(tags=["Workspace"])

DEFAULT_DEVELOPMENT_SOURCES = [
    "gdelt",
    "rss",
    "newsapi",
    "reddit",
    "youtube",
    "usgs",
    "opensky",
    "ais",
    "yahoo_finance",
]

# ── Response schemas ───────────────────────────────────────────────────────

class WorkspaceEventsResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    events: List[Any] = Field(default_factory=list)
    total: int = 0

class WorkspaceAssetsResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    assets: List[Any] = Field(default_factory=list)
    total: int = 0

class WorkspaceSentimentResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    reddit: List[Any] = Field(default_factory=list)
    youtube: List[Any] = Field(default_factory=list)
    combined: List[Any] = Field(default_factory=list)
    reddit_items: List[Any] = Field(default_factory=list)
    youtube_items: List[Any] = Field(default_factory=list)
    social_items: List[Any] = Field(default_factory=list)

class WorkspaceEntitiesResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    entities: List[Any] = Field(default_factory=list)
    total: int = 0

class WorkspaceCorrelationResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    narratives: List[Any] = Field(default_factory=list)
    signal_clusters: List[Any] = Field(default_factory=list)
    events: List[Any] = Field(default_factory=list)


class GeoFilter(BaseModel):
    min_lat: Optional[float] = None
    max_lat: Optional[float] = None
    min_lon: Optional[float] = None
    max_lon: Optional[float] = None


class WorkspaceResolveRequest(BaseModel):
    queries: List[str] = []
    sources: List[str] = []
    window_hours: int = 24
    geo_filter: Optional[GeoFilter] = None
    entity_seeds: List[str] = []


def _in_bbox(lat: Optional[float], lon: Optional[float], geo: Optional[GeoFilter]) -> bool:
    if geo is None or lat is None or lon is None:
        return True
    if geo.min_lat is not None and lat < geo.min_lat:
        return False
    if geo.max_lat is not None and lat > geo.max_lat:
        return False
    if geo.min_lon is not None and lon < geo.min_lon:
        return False
    if geo.max_lon is not None and lon > geo.max_lon:
        return False
    return True


def _expanded_geo(geo: GeoFilter, degrees: float = 24.0) -> GeoFilter:
    return GeoFilter(
        min_lat=max(-85, geo.min_lat - degrees) if geo.min_lat is not None else None,
        max_lat=min(85, geo.max_lat + degrees) if geo.max_lat is not None else None,
        min_lon=max(-180, geo.min_lon - degrees) if geo.min_lon is not None else None,
        max_lon=min(180, geo.max_lon + degrees) if geo.max_lon is not None else None,
    )


def _workspace_query(req: WorkspaceResolveRequest) -> Optional[str]:
    parts = [q.strip() for q in req.queries[:5] if q and q.strip()]
    for seed in req.entity_seeds[:8]:
        seed = (seed or "").strip()
        if seed and seed.lower() not in {p.lower() for p in parts}:
            parts.append(seed)
    return " OR ".join(parts) if parts else None


def _extractor_query(req: WorkspaceResolveRequest) -> str:
    """Build a natural search phrase for upstream social providers."""
    parts = []
    for text in [*req.queries[:3], *req.entity_seeds[:6]]:
        clean = (text or "").replace(" OR ", " ").replace("|", " ").strip()
        if clean and clean.lower() not in {p.lower() for p in parts}:
            parts.append(clean)
    return " ".join(parts)[:240] or "global security"


def _workspace_terms(query: Optional[str]) -> List[str]:
    if not query:
        return []
    terms: List[str] = []
    for part in re.split(r"\s+\bOR\b\s+|[;,|]", query, flags=re.IGNORECASE):
        part = re.sub(r"\s+", " ", part.strip().strip("\"'()[]{}"))
        if part:
            terms.append(part.lower())
        for word in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", part):
            terms.append(word.lower())
    deduped: List[str] = []
    seen = set()
    for term in terms:
        if term not in seen:
            deduped.append(term)
            seen.add(term)
    return deduped


def _source_family(source: Optional[str]) -> str:
    s = (source or "").lower()
    if s.startswith("gdelt"):
        return "gdelt"
    if s.startswith("rss"):
        return "rss"
    if s == "newsapi" or s == "news":
        return "news"
    if s in {"reddit", "youtube", "telegram", "twitter", "bluesky"}:
        return "social"
    if s in {"ais", "opensky"}:
        return "transport"
    if s in {"usgs", "firms", "weather"}:
        return "geospatial"
    if s in {"yahoo_finance", "stocks", "treasury"}:
        return "market"
    return s or "unknown"


def _source_prefixes(profile: str) -> List[str]:
    p = (profile or "").lower()
    if p == "gdelt":
        return ["gdelt"]
    if p == "rss":
        return ["rss"]
    if p == "news":
        return ["newsapi"]
    if p == "socials":
        return ["reddit", "youtube", "telegram", "twitter", "bluesky"]
    if p == "stocks":
        return ["yahoo_finance"]
    if p in {"reddit", "youtube", "telegram", "twitter", "bluesky", "ais", "opensky", "usgs", "weather", "firms"}:
        return [p]
    return [p] if p else []


def _development_source_prefixes(req: WorkspaceResolveRequest) -> List[str]:
    """Return a broad source list for workspace developments.

    Workspace source profiles are useful as analyst hints, but the developments
    tab should be a cross-source evidence stream. If a workspace was created
    with only a social profile (for example YouTube), merge in the standard
    intelligence providers so GDELT/RSS/NewsAPI/geo/transport evidence still
    appears when present.
    """
    prefixes: List[str] = []
    for source in req.sources:
        prefixes.extend(_source_prefixes(source))

    families = {_source_family(prefix) for prefix in prefixes}
    if not prefixes or families.issubset({"social"}):
        prefixes.extend(DEFAULT_DEVELOPMENT_SOURCES)
    else:
        # Always include canonical text/news providers for developments; users
        # can still filter the returned evidence mix in the UI.
        prefixes.extend(["gdelt", "rss", "newsapi"])

    out: List[str] = []
    seen: set[str] = set()
    for prefix in prefixes:
        key = (prefix or "").lower()
        if key and key not in seen:
            out.append(key)
            seen.add(key)
    return out


def _allowed_families(req: WorkspaceResolveRequest) -> set[str]:
    families: set[str] = set()
    for src in req.sources:
        for prefix in _source_prefixes(src):
            families.add(_source_family(prefix))
    return families


def _event_allowed_by_sources(event: Dict[str, Any], allowed: set[str]) -> bool:
    return not allowed or _source_family(event.get("source")) in allowed


def _balanced_events(events: List[Dict[str, Any]], limit: int = 100) -> List[Dict[str, Any]]:
    """Round-robin source families so social/telemetry cannot bury news evidence."""
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for event in events:
        buckets.setdefault(_source_family(event.get("source")), []).append(event)

    for bucket in buckets.values():
        bucket.sort(key=lambda e: (e.get("risk_score") or 0, e.get("timestamp") or ""), reverse=True)

    family_order = ["gdelt", "rss", "news", "social", "transport", "geospatial", "market"]
    family_order.extend(sorted(f for f in buckets if f not in family_order))

    selected: List[Dict[str, Any]] = []
    seen: set[str] = set()
    while len(selected) < limit:
        added = False
        for family in family_order:
            bucket = buckets.get(family) or []
            while bucket:
                event = bucket.pop(0)
                eid = event.get("event_id")
                if not eid or eid in seen:
                    continue
                seen.add(eid)
                selected.append(event)
                added = True
                break
            if len(selected) >= limit:
                break
        if not added:
            break
    return selected


def _social_item(event: Dict[str, Any]) -> Dict[str, Any]:
    sentiment = event.get("sentiment") or {}
    extras = event.get("extras") or {}
    desc = event.get("description") or event.get("body") or ""
    return {
        "event_id": event.get("event_id") or "",
        "source": event.get("source") or "",
        "title": event.get("title") or "Untitled social item",
        "url": event.get("url"),
        "author": event.get("author") or extras.get("channel") or extras.get("subreddit"),
        "timestamp": event.get("timestamp") or event.get("ingest_time"),
        "sentiment_score": sentiment.get("score"),
        "sentiment_label": sentiment.get("label"),
        "description": desc[:420] if isinstance(desc, str) else "",
        "meta": {
            "subreddit": extras.get("subreddit"),
            "score": extras.get("score"),
            "num_comments": extras.get("num_comments"),
            "view_count": extras.get("view_count"),
            "duration": extras.get("duration"),
            "thumbnail": extras.get("thumbnail"),
        },
    }


async def _fetch_social_items(repo: Any, source: str, query: Optional[str], hours: int, limit: int = 24) -> List[Dict[str, Any]]:
    start = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    try:
        _total, events = await repo.list_events(
            source=source,
            query=query,
            from_time=start,
            limit=limit,
        )
        if not events and query:
            # Some workspace queries combine phrases and entity seeds. If the SQL
            # phrase match is too strict, load recent provider rows and apply a
            # softer analyst-term match so the UI can still show readable proof.
            _total, candidates = await repo.list_events(
                source=source,
                from_time=start,
                limit=max(limit * 3, 60),
            )
            terms = _workspace_terms(query)
            events = [
                e for e in candidates
                if any(t in f"{e.get('title') or ''} {e.get('description') or ''} {e.get('body') or ''}".lower() for t in terms)
            ][:limit]
        return [_social_item(e) for e in events]
    except Exception as exc:
        logger.warning("resolve-sentiment social item load failed for %s: %s", source, exc)
        return []


async def _warm_youtube_for_workspace(req: WorkspaceResolveRequest, request: Request) -> List[Dict[str, Any]]:
    """Run a bounded YouTube search so workspace social is readable immediately."""
    try:
        from extractors.socials import YouTubeExtractor
        from storage.database import get_session
        from storage.event_repo import EventRepository

        query = _extractor_query(req)
        loop = asyncio.get_running_loop()

        def _work():
            events = YouTubeExtractor().run(query=query, limit=8)
            for event in events:
                event["sentiment"] = event.get("sentiment") or {
                    "label": "NEUTRAL",
                    "score": 0.5,
                    "confidence": 0.35,
                    "provisional": True,
                }
            return events

        events = await asyncio.wait_for(loop.run_in_executor(None, _work), timeout=6.0)
        if not events:
            return []

        async with get_session() as session:
            repo = EventRepository(session)
            await repo.upsert_many(events)

        return [_social_item({k: v for k, v in dict(e).items() if k != "raw"}) for e in events]
    except asyncio.TimeoutError:
        logger.warning("Workspace YouTube warmup timed out")
    except Exception as exc:
        logger.warning("Workspace YouTube warmup failed: %s", exc)
    return []


async def _warm_reddit_for_workspace(req: WorkspaceResolveRequest, request: Request) -> List[Dict[str, Any]]:
    """Surface Reddit in workspace social. Reddit's public search.json hard-403s from
    datacenter IPs (no OAuth path), so a live request from the container always fails and
    just burns the resolver budget. Instead serve the Reddit rows the worker already
    ingested (the worker runs from a residential-style egress), topically matched to the
    workspace, falling back to most-recent so the column is never empty."""
    try:
        from storage.database import get_session
        from storage.event_repo import EventRepository

        terms = _workspace_terms(_extractor_query(req))
        async with get_session() as session:
            repo = EventRepository(session)
            _total, candidates = await repo.list_events(source="reddit", limit=160)
        matched = [
            e for e in candidates
            if any(t in f"{e.get('title') or ''} {e.get('description') or ''} {e.get('body') or ''}".lower() for t in terms)
        ] if terms else []
        # Prefer topical matches; if none, show most-recent reddit so the column isn't empty.
        chosen = (matched or candidates)[:16]
        return [_social_item({k: v for k, v in dict(e).items() if k != "raw"}) for e in chosen]
    except Exception as exc:
        logger.warning("Workspace Reddit warmup failed: %s", exc)
    return []


@router.post("/resolve-events", response_model=WorkspaceEventsResponse)
async def resolve_events(req: WorkspaceResolveRequest, request: Request):
    """Fetch and rank events for a workspace scope."""
    if not getattr(request.app.state, "db_available", False):
        return {"events": [], "total": 0, "note": "db unavailable"}

    try:
        from storage.database import get_session
        from storage.event_repo import EventRepository

        requested_hours = max(1, req.window_hours)
        window_start = (datetime.now(timezone.utc) - timedelta(hours=requested_hours)).isoformat()
        all_events: List[Dict[str, Any]] = []
        seen_ids: set = set()
        combined_query = _workspace_query(req)
        source_prefixes = _development_source_prefixes(req)
        # The development feed intentionally searches across source families.
        # Exact source filtering belongs to the UI and source-specific endpoints.
        allowed_families: set[str] = set()
        terms = _workspace_terms(combined_query)

        def add_events(page: List[Dict[str, Any]]) -> None:
            for e in page:
                eid = e.get("event_id")
                if not eid or eid in seen_ids:
                    continue
                if not _event_allowed_by_sources(e, allowed_families):
                    continue
                lat = (e.get("location") or {}).get("lat")
                lon = (e.get("location") or {}).get("lon")
                if lat is not None and lon is not None and req.geo_filter:
                    if not _in_bbox(lat, lon, req.geo_filter):
                        continue
                seen_ids.add(eid)
                all_events.append(e)

        async with get_session() as session:
            repo = EventRepository(session)
            # ONE combined query across all sources. The OR query already matches any
            # workspace term in any column, so the old per-source loop (16 sources ×
            # list_feed_events + per-query loops + fallbacks = 40+ scans over 37k rows,
            # which blew past the .NET resolver budget and returned empty) is replaced by
            # a single list_events call. Source-family filtering is applied in Python below.
            try:
                _total, page = await repo.list_events(
                    query=combined_query,
                    from_time=window_start,
                    limit=300,
                    sort_by="risk_score",
                    with_total=False,
                )
                add_events(page)
            except Exception as exc:
                logger.warning("resolve-events combined query failed: %s", exc)

            # If the requested window is genuinely thin, widen to 7 days once (still one query).
            if len(all_events) < 8 and requested_hours < 168:
                fallback_start = (datetime.now(timezone.utc) - timedelta(hours=168)).isoformat()
                try:
                    _total, page = await repo.list_events(
                        query=combined_query,
                        from_time=fallback_start,
                        limit=300,
                        sort_by="risk_score",
                        with_total=False,
                    )
                    add_events(page)
                except Exception as exc:
                    logger.warning("resolve-events 7d fallback failed: %s", exc)

        all_events = _balanced_events(all_events, 100)
        source_counts: Dict[str, int] = {}
        family_counts: Dict[str, int] = {}
        for event in all_events:
            src = event.get("source") or "unknown"
            fam = _source_family(src)
            source_counts[src] = source_counts.get(src, 0) + 1
            family_counts[fam] = family_counts.get(fam, 0) + 1

        return {
            "events": all_events,
            "total": len(all_events),
            "source_counts": source_counts,
            "family_counts": family_counts,
            "window_hours": requested_hours,
            "source_prefixes": source_prefixes,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        logger.error("resolve-events failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/resolve-assets", response_model=WorkspaceAssetsResponse)
async def resolve_assets(req: WorkspaceResolveRequest, request: Request):
    """Return assets filtered by bbox with anomaly highlights."""
    if not getattr(request.app.state, "db_available", False):
        return {"assets": [], "counts": {}, "total": 0, "note": "db unavailable"}

    try:
        from storage.asset_repo import AssetRepository
        repo = AssetRepository()

        expanded_scope = False
        if req.geo_filter:
            assets = await repo.get_assets_in_bbox(
                min_lat=req.geo_filter.min_lat,
                max_lat=req.geo_filter.max_lat,
                min_lon=req.geo_filter.min_lon,
                max_lon=req.geo_filter.max_lon,
                limit=500,
            )
            if len(assets) < 25:
                expanded = _expanded_geo(req.geo_filter)
                regional_assets = await repo.get_assets_in_bbox(
                    min_lat=expanded.min_lat,
                    max_lat=expanded.max_lat,
                    min_lon=expanded.min_lon,
                    max_lon=expanded.max_lon,
                    limit=500,
                )
                seen = {a.get("asset_id") for a in assets}
                assets.extend([a for a in regional_assets if a.get("asset_id") not in seen])
                expanded_scope = len(assets) > len(seen)
        else:
            assets = await repo.get_assets(limit=200)

        for a in assets:
            lat = a.get("last_lat")
            lon = a.get("last_lon")
            a["within_aoi"] = _in_bbox(lat, lon, req.geo_filter)

        counts: Dict[str, int] = {}
        for a in assets:
            t = a.get("asset_type", "unknown")
            counts[t] = counts.get(t, 0) + 1

        # Anomalies: high speed or recent activity
        anomalies = [
            a for a in assets
            if (a.get("last_speed") or 0) > 25 or a.get("on_ground") is False
        ][:10]
        anomaly_ids = {a.get("asset_id") for a in anomalies}
        for a in assets:
            a["is_anomaly"] = a.get("asset_id") in anomaly_ids

        return {
            "assets": assets,
            "counts": counts,
            "total": len(assets),
            "anomalies": anomalies,
            "scope": "regional_context" if expanded_scope else "aoi",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        logger.error("resolve-assets failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/resolve-sentiment", response_model=WorkspaceSentimentResponse)
async def resolve_sentiment(req: WorkspaceResolveRequest, request: Request):
    """Compute sentiment timeline for workspace scope."""
    if not getattr(request.app.state, "db_available", False):
        return {"reddit": [], "youtube": [], "combined": [], "note": "db unavailable"}

    try:
        from storage.database import get_session
        from storage.event_repo import EventRepository

        requested_hours = max(1, req.window_hours)
        window_start = (datetime.now(timezone.utc) - timedelta(hours=requested_hours)).isoformat()
        combined_query = _workspace_query(req)

        results: Dict[str, Any] = {
            "reddit": [],
            "youtube": [],
            "combined": [],
            "reddit_items": [],
            "youtube_items": [],
            "social_items": [],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        _social_sources = ["reddit", "youtube", "telegram", "twitter"]
        enabled_social = [s for s in _social_sources if not req.sources or s in req.sources]

        async with get_session() as session:
            repo = EventRepository(session)
            async def load_window(hours: int) -> Dict[str, Any]:
                start = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
                out: Dict[str, Any] = {}
                for source_name in enabled_social:
                    try:
                        out[source_name] = await repo.get_sentiment_timeline(
                            query=combined_query,
                            source=source_name,
                            from_time=start,
                            bucket="day",
                        )
                    except Exception as exc:
                        logger.warning("resolve-sentiment %s failed: %s", source_name, exc)
                        out[source_name] = []
                return out

            effective_hours = requested_hours
            source_hours: Dict[str, int] = {src: requested_hours for src in enabled_social}
            loaded = await load_window(requested_hours)
            if sum(len(loaded.get(src, [])) for src in enabled_social) == 0 and requested_hours < 168:
                loaded = await load_window(168)
                if sum(len(loaded.get(src, [])) for src in enabled_social) > 0:
                    effective_hours = 168
                    source_hours = {src: 168 for src in enabled_social}
                    results["fallback_window_hours"] = 168
                    results["note"] = "no social matches in requested window; returned recent 7-day workspace social context"
            elif requested_hours < 168:
                fallback_loaded: Optional[Dict[str, Any]] = None
                for src in enabled_social:
                    if len(loaded.get(src, [])) == 0:
                        if fallback_loaded is None:
                            fallback_loaded = await load_window(168)
                        if len(fallback_loaded.get(src, [])) > 0:
                            loaded[src] = fallback_loaded.get(src, [])
                            source_hours[src] = 168
                            results["partial_fallback_window_hours"] = 168

            for source_name in enabled_social:
                results[source_name] = loaded.get(source_name, [])

            # Pull whatever already matches in the DB (fast), then warm the empty providers.
            reddit_items = (
                await _fetch_social_items(repo, "reddit", combined_query, source_hours.get("reddit", effective_hours))
                if "reddit" in enabled_social else []
            )
            youtube_items = (
                await _fetch_social_items(repo, "youtube", combined_query, source_hours.get("youtube", effective_hours))
                if "youtube" in enabled_social else []
            )

            # Warm empty providers CONCURRENTLY so a slow YouTube live search can't starve
            # the resolver budget and lose Reddit (or vice-versa). Reddit warmup is DB-only
            # and instant; YouTube does a bounded live search.
            warm_tasks: Dict[str, Any] = {}
            if "reddit" in enabled_social and not reddit_items:
                warm_tasks["reddit"] = _warm_reddit_for_workspace(req, request)
            if "youtube" in enabled_social and not youtube_items:
                warm_tasks["youtube"] = _warm_youtube_for_workspace(req, request)
            if warm_tasks:
                warmed = await asyncio.gather(*warm_tasks.values(), return_exceptions=True)
                timeline_from = (datetime.now(timezone.utc) - timedelta(hours=max(effective_hours, 168))).isoformat()
                for key, res in zip(warm_tasks.keys(), warmed):
                    if isinstance(res, BaseException) or not res:
                        continue
                    if key == "reddit":
                        reddit_items = res
                    else:
                        youtube_items = res
                    results[key] = await repo.get_sentiment_timeline(
                        query=combined_query, source=key, from_time=timeline_from, bucket="day",
                    )
            if "reddit" in enabled_social:
                results["reddit_items"] = reddit_items
            if "youtube" in enabled_social:
                results["youtube_items"] = youtube_items
            results["social_items"] = sorted(
                [*results.get("reddit_items", []), *results.get("youtube_items", [])],
                key=lambda item: item.get("timestamp") or "",
                reverse=True,
            )[:40]

            # Combined social = merge only social-source timelines
            combined_buckets: Dict[str, Dict] = {}
            for src in enabled_social:
                for point in results.get(src, []):
                    bucket = point.get("bucket", "")
                    if not bucket:
                        continue
                    b = combined_buckets.setdefault(bucket, {
                        "bucket": bucket, "event_count": 0, "positive": 0,
                        "neutral": 0, "negative": 0, "_sum": 0.0,
                    })
                    cnt = point.get("event_count", 0)
                    b["event_count"] += cnt
                    b["positive"] += point.get("positive", 0)
                    b["neutral"] += point.get("neutral", 0)
                    b["negative"] += point.get("negative", 0)
                    b["_sum"] += (point.get("avg_score") or 0.5) * cnt

            combined_list = []
            for b in sorted(combined_buckets.values(), key=lambda x: x["bucket"]):
                cnt = max(b["event_count"], 1)
                combined_list.append({
                    "bucket": b["bucket"],
                    "avg_score": round(b["_sum"] / cnt, 4),
                    "event_count": b["event_count"],
                    "positive": b["positive"],
                    "neutral": b["neutral"],
                    "negative": b["negative"],
                })
            results["combined"] = combined_list

        return results
    except Exception as exc:
        logger.error("resolve-sentiment failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/resolve-entities", response_model=WorkspaceEntitiesResponse)
async def resolve_entities(req: WorkspaceResolveRequest, request: Request):
    """Return top actors for workspace query scope."""
    if not getattr(request.app.state, "db_available", False):
        return {"entities": [], "total": 0, "note": "db unavailable"}

    try:
        from storage.database import get_session
        from storage.event_repo import EventRepository

        requested_hours = max(1, req.window_hours)
        window_start = (datetime.now(timezone.utc) - timedelta(hours=requested_hours)).isoformat()
        actor_counts: Dict[str, Dict[str, Any]] = {}
        combined_query = _workspace_query(req)
        source_prefixes = _development_source_prefixes(req)
        terms = _workspace_terms(combined_query)

        def add_actor(name: str, entity_type: Optional[str], event_id: Optional[str]) -> None:
            clean = (name or "").strip()
            if not clean or len(clean) < 2:
                return
            key = clean.lower()
            if key not in actor_counts:
                actor_counts[key] = {
                    "name": clean,
                    "type": entity_type,
                    "count": 0,
                    "events": [],
                }
            actor_counts[key]["count"] += 1
            if event_id and event_id not in actor_counts[key]["events"]:
                actor_counts[key]["events"].append(event_id)

        def add_event_entities(event: Dict[str, Any]) -> None:
            eid = event.get("event_id")
            for actor in event.get("actors") or []:
                if not isinstance(actor, dict):
                    continue
                add_actor(actor.get("name") or actor.get("display_name") or "", actor.get("type"), eid)

            haystack = f"{event.get('title') or ''} {event.get('description') or ''} {event.get('body') or ''}".lower()
            for seed in req.entity_seeds:
                if seed and seed.lower() in haystack:
                    add_actor(seed, "seed", eid)
            for tag in event.get("tags") or []:
                if isinstance(tag, str) and tag.startswith("#"):
                    add_actor(tag.lstrip("#"), "tag", eid)

        async with get_session() as session:
            repo = EventRepository(session)
            # ONE combined query (same speedup as resolve-events) then extract actors.
            try:
                _total, events = await repo.list_events(
                    query=combined_query,
                    from_time=window_start,
                    limit=300,
                    sort_by="risk_score",
                    with_total=False,
                )
                for event in events:
                    add_event_entities(event)
            except Exception as exc:
                logger.warning("resolve-entities combined query failed: %s", exc)

            if len(actor_counts) < 5 and requested_hours < 168:
                fallback_start = (datetime.now(timezone.utc) - timedelta(hours=168)).isoformat()
                try:
                    _total, events = await repo.list_events(
                        query=combined_query,
                        from_time=fallback_start,
                        limit=300,
                        sort_by="risk_score",
                        with_total=False,
                    )
                    for event in events:
                        add_event_entities(event)
                except Exception as exc:
                    logger.warning("resolve-entities 7d fallback failed: %s", exc)

        # Merge seed entities
        for seed in req.entity_seeds:
            key = seed.lower()
            if key not in actor_counts:
                actor_counts[key] = {"name": seed, "type": None, "count": 0, "events": []}

        entities = sorted(actor_counts.values(), key=lambda e: e["count"], reverse=True)[:50]
        for e in entities:
            e["event_count"] = len(e.pop("events", []))

        return {
            "entities": entities,
            "total": len(entities),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        logger.error("resolve-entities failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/resolve-correlation", response_model=WorkspaceCorrelationResponse)
async def resolve_correlation(req: WorkspaceResolveRequest, request: Request):
    """Return signal clusters and narratives for workspace scope."""
    try:
        graph = getattr(request.app.state, "graph", None)
        narratives: List[Dict] = []
        clusters: List[Dict] = []
        graph_events: List[Dict] = []

        if graph and getattr(graph, "available", False):
            try:
                # Primary: precomputed actor co-occurrence (CO_MENTIONED_WITH) computed over
                # ALL events, not the workspace-filtered subset — true cross-corpus correlation.
                seed_ids = [f"actor:{s.lower().replace(' ', '_')}" for s in req.entity_seeds[:8] if s]
                for c in graph.correlated_actors(seed_ids, limit=40):
                    narratives.append({"source": c["source"], "rel": c["rel"], "target": c["target"]})

                # Connected events: every event the seed actors are MENTIONS-linked to in the
                # graph (cross-corpus, all sources) — the "events from the graph" for correlation.
                graph_events = graph.actor_events(seed_ids, limit=60)

                # Dedup narratives (correlated_actors already cross-corpus; DB co_counts add more
                # below). Skipped the per-seed ego_graph supplement — 3 extra Neo4j round-trips
                # that pushed this resolver past the .NET 8s budget for marginal new links.
                seen = set()
                narratives = [
                    n for n in narratives
                    if (k := f"{n['source']}|{n['target']}") not in seen and not seen.add(k)
                ][:30]
            except Exception as exc:
                logger.warning("resolve-correlation graph query failed: %s", exc)

        if getattr(request.app.state, "db_available", False):
            try:
                from storage.database import get_session
                from storage.event_repo import EventRepository
                window_start = (datetime.now(timezone.utc) - timedelta(hours=req.window_hours)).isoformat()
                combined_query = " OR ".join(req.queries[:5]) if req.queries else None
                async with get_session() as session:
                    repo = EventRepository(session)
                    _total, events = await repo.list_events(query=combined_query, from_time=window_start, limit=200)
                tag_counts: Dict[str, int] = {}
                co_counts: Dict[tuple, int] = {}
                seed_keys = {s.lower() for s in req.entity_seeds if s}
                for ev in events:
                    for tag in ev.get("tags") or []:
                        tag_counts[tag] = tag_counts.get(tag, 0) + 1
                    actors = [
                        (a.get("name") or a.get("display_name") or "").strip()
                        for a in (ev.get("actors") or [])
                        if isinstance(a, dict)
                    ]
                    actors.extend([
                        seed for seed in req.entity_seeds
                        if seed and seed.lower() in f"{ev.get('title') or ''} {ev.get('description') or ''} {ev.get('body') or ''}".lower()
                    ])
                    clean_actors = []
                    seen_actor = set()
                    for actor in actors:
                        key = actor.lower()
                        if actor and key not in seen_actor:
                            clean_actors.append(actor)
                            seen_actor.add(key)
                    for i, src in enumerate(clean_actors[:8]):
                        for tgt in clean_actors[i + 1:8]:
                            if src.lower() == tgt.lower():
                                continue
                            key = tuple(sorted((src, tgt), key=str.lower))
                            co_counts[key] = co_counts.get(key, 0) + 1
                clusters = [
                    {"tag": tag, "count": count}
                    for tag, count in sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:20]
                ]
                existing_links = {f"{n.get('source','').lower()}|{n.get('target','').lower()}" for n in narratives}
                for (src, tgt), count in sorted(co_counts.items(), key=lambda x: x[1], reverse=True):
                    if count < 2 and src.lower() not in seed_keys and tgt.lower() not in seed_keys:
                        continue
                    key = f"{src.lower()}|{tgt.lower()}"
                    rev = f"{tgt.lower()}|{src.lower()}"
                    if key in existing_links or rev in existing_links:
                        continue
                    narratives.append({
                        "source": src,
                        "rel": f"CO_OCCURS_{count}X",
                        "target": tgt,
                    })
                    existing_links.add(key)
                    if len(narratives) >= 30:
                        break
            except Exception as exc:
                logger.warning("resolve-correlation tag clusters failed: %s", exc)

        return {
            "narratives": narratives,
            "signal_clusters": clusters,
            "events": graph_events,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        logger.error("resolve-correlation failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
