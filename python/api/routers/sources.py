"""
api/routers/sources.py
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Per-extractor custom search endpoints.

Every data source is individually addressable so users (and the .NET API)
can run targeted queries against a single source with full parameter control,
instead of always going through the full orchestration pipeline.

Each endpoint:
  - Accepts the source's native fetch parameters as query params
  - Runs NLP on the results
  - Returns the same VisionEvent schema as /events
  - Is individually documented in Swagger at /docs

Routes:
  GET /sources/news          â€” NewsAPI keyword search
  GET /sources/reddit        â€” Reddit search
  GET /sources/youtube       â€” YouTube search
  GET /sources/usgs          â€” USGS earthquake feed
  GET /sources/stocks        â€” Yahoo Finance tickers
  GET /sources/opensky       â€” OpenSky live flight positions
"""

import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from core.source_registry import source_registry

logger = logging.getLogger("vision_i.api.sources")
router = APIRouter(tags=["Sources"])
_CATALOG_CACHE_FAST = {"ts": 0.0, "data": None}

# ── Response schemas ───────────────────────────────────────────────────────

class SourceEventsResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    total: int = 0
    source: str = ""
    events: List[Any] = Field(default_factory=list)

class SourceCatalogResponse(BaseModel):
    model_config = ConfigDict(extra="allow")


def _normalize_probe_status(probe: Optional[Dict[str, Any]], requires_credentials: bool = False) -> str:
    if not probe:
        return "unknown"
    raw = str(probe.get("status") or "unknown").strip().lower()
    detail = str(probe.get("detail") or "")
    detail_l = detail.lower()
    if raw in {"ok", "healthy"}:
        return "healthy"
    if raw in {"degraded", "stale"}:
        return "degraded"
    if raw in {"timeout", "circuit_open"}:
        return "down"
    if raw in {"error", "failed", "down"}:
        if requires_credentials and any(token in detail_l for token in ("credential", "api key", "token", "secret", "auth")):
            return "not_configured"
        return "down"
    return "unknown"


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _derive_checkpoint_status(
    *,
    last_run_at: Optional[datetime],
    events_fetched: int,
    error_count: int,
    requires_credentials: bool,
    last_error: str = "",
) -> str:
    if last_run_at is None:
        if requires_credentials and last_error:
            return "not_configured" if any(t in last_error.lower() for t in ("credential", "api key", "token", "secret", "auth")) else "unknown"
        return "unknown"

    age = datetime.now(timezone.utc) - last_run_at
    if requires_credentials and last_error and any(t in last_error.lower() for t in ("credential", "api key", "token", "secret", "auth")):
        return "not_configured"
    if error_count >= 5:
        return "down"
    if error_count > 0 or age > timedelta(hours=24):
        return "degraded"
    if events_fetched > 0 or age <= timedelta(hours=24):
        return "healthy"
    return "unknown"


async def _checkpoint_health_map(request: Request) -> Dict[str, Dict[str, Any]]:
    if not getattr(request.app.state, "db_available", False):
        return {}

    try:
        from sqlalchemy import select
        from storage.database import SourceCheckpointModel, get_session

        checkpoint_map: Dict[str, Dict[str, Any]] = {}
        async with get_session() as session:
            rows = (await session.execute(select(SourceCheckpointModel))).scalars().all()

        for row in rows:
            canonical = source_registry.canonicalize(row.source) or (row.source or "").strip().lower()
            if not canonical:
                continue

            meta = row.meta or {}
            error_count = _coerce_int(meta.get("error_count"), 0) if isinstance(meta, dict) else 0
            last_error = ""
            if isinstance(meta, dict):
                last_error = str(meta.get("last_error") or meta.get("error_summary") or "")

            existing = checkpoint_map.get(canonical)
            if existing and existing.get("last_run_at") and row.last_run_at and existing["last_run_at"] >= row.last_run_at:
                continue

            checkpoint_map[canonical] = {
                "source_name": row.source,
                "last_run_at": row.last_run_at,
                "events_fetched": row.events_fetched or 0,
                "error_count": error_count,
                "credibility_score": row.credibility_score,
                "last_error": last_error,
            }

        return checkpoint_map
    except Exception as exc:
        logger.warning("Failed to load source checkpoints for catalog: %s", exc)
        return {}


def _merge_catalog_health(
    item: Dict[str, Any],
    probe_health: Optional[Dict[str, Any]],
    checkpoint_health: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    requires_credentials = bool(item.get("requires_credentials"))
    probe_status = _normalize_probe_status(probe_health, requires_credentials=requires_credentials)

    checkpoint_status = None
    if checkpoint_health:
        checkpoint_status = _derive_checkpoint_status(
            last_run_at=checkpoint_health.get("last_run_at"),
            events_fetched=_coerce_int(checkpoint_health.get("events_fetched"), 0),
            error_count=_coerce_int(checkpoint_health.get("error_count"), 0),
            requires_credentials=requires_credentials,
            last_error=str(checkpoint_health.get("last_error") or ""),
        )

    if checkpoint_status in {"healthy", "degraded"}:
        merged_status = "degraded" if probe_status == "down" else checkpoint_status
    elif checkpoint_status in {"down", "not_configured"}:
        merged_status = checkpoint_status
    elif probe_status != "unknown":
        merged_status = probe_status
    else:
        merged_status = "not_configured" if requires_credentials else "unknown"

    result = {
        "status": merged_status,
        "probe_status": probe_health.get("status") if probe_health else None,
    }
    if checkpoint_health:
        result.update({
            "last_checked": checkpoint_health.get("last_run_at").isoformat() if checkpoint_health.get("last_run_at") else None,
            "record_count": _coerce_int(checkpoint_health.get("events_fetched"), 0),
            "error_count": _coerce_int(checkpoint_health.get("error_count"), 0),
            "credibility_score": checkpoint_health.get("credibility_score"),
        })
        if checkpoint_health.get("last_error"):
            result["detail"] = checkpoint_health.get("last_error")
    elif probe_health and probe_health.get("detail"):
        result["detail"] = probe_health.get("detail")

    return result

def _cache_key(source: str, fetch_kwargs: dict, run_nlp: bool) -> str:
    payload = {
        "source": source,
        "run_nlp": run_nlp,
        "kwargs": fetch_kwargs,
    }
    raw = json.dumps(payload, sort_keys=True, default=str)
    return "sources:cache:" + hashlib.md5(raw.encode("utf-8")).hexdigest()


def _cache_ttl_from_request(request: Request, default_ttl: int = 30) -> int:
    raw = request.query_params.get("cache_ttl")
    if raw is None:
        return default_ttl
    try:
        ttl = int(raw)
    except ValueError:
        return 0
    return max(0, min(ttl, 600))


async def _run_and_enrich(
    request: Request,
    extractor_cls,
    fetch_kwargs: dict,
    run_nlp: bool = True,
    cache_only: bool = False,
) -> dict:
    """
    Instantiate an extractor, fetch, normalize, run NLP, return events dict.
    Runs in a thread pool so the async loop is never blocked.
    """
    loop = asyncio.get_running_loop()
    cache_ttl = _cache_ttl_from_request(request)
    event_bus = getattr(request.app.state, "event_bus", None)
    cache_key = _cache_key(extractor_cls.source_name, fetch_kwargs, run_nlp)

    if cache_ttl > 0 and event_bus:
        cached = await event_bus.cache_get(cache_key)
        if cached:
            cached["cached"] = True
            return cached
        if cache_only:
            try:
                # Ask the worker to warm this cache key.
                await event_bus.publish("sources_warm", {
                    "source": extractor_cls.source_name,
                    "run_nlp": bool(run_nlp),
                    "kwargs": fetch_kwargs,
                    "cache_ttl": cache_ttl,
                })
            except Exception:
                pass
            return JSONResponse(
                status_code=202,
                content={
                    "status": "warming",
                    "source": extractor_cls.source_name,
                    "total": 0,
                    "events": [],
                    "cache_key": cache_key,
                    "note": "cache miss; retry shortly or call with fast=false",
                },
            )

    def _work():
        ext    = extractor_cls()
        events = ext.run(**fetch_kwargs)
        if run_nlp and events:
            request.app.state.nlp.process(events)
        return events

    events = await loop.run_in_executor(None, _work)
    payload = {
        "total":  len(events),
        "source": extractor_cls.source_name,
        "events": [{k: v for k, v in e.items() if k != "raw"} for e in events],
    }

    if cache_ttl > 0 and event_bus:
        await event_bus.cache_set(cache_key, payload, ttl_seconds=cache_ttl)

    return payload


@router.get("/catalog", summary="List source capabilities, health, and parameters", response_model=SourceCatalogResponse)
async def source_catalog(
    request: Request,
    fast: bool = Query(True, description="Use cached/timeout-limited health probes"),
):
    # Small in-process cache keeps repeated UI refreshes sub-10ms.
    if fast:
        age = time.time() - float(_CATALOG_CACHE_FAST["ts"])
        if _CATALOG_CACHE_FAST["data"] is not None and age < 30:
            return _CATALOG_CACHE_FAST["data"]

    orchestrator = request.app.state.orchestrator
    loop = asyncio.get_running_loop()
    try:
        # orchestrator.health() is synchronous and may do network I/O; never block the event loop.
        if fast:
            health = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: orchestrator.health(timeout_s=1.0, use_cache=True, cache_ttl_s=60),
                ),
                timeout=2.5,
            )
        else:
            health = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: orchestrator.health(timeout_s=4.0, use_cache=False, cache_ttl_s=0),
                ),
                timeout=6.0,
            )
    except asyncio.TimeoutError:
        logger.warning("Source catalog health probe timed out (fast=%s)", fast)
        health = {}
    except Exception as exc:
        logger.warning("Failed to compute source health for catalog: %s", exc)
        health = {}
    checkpoint_health = await _checkpoint_health_map(request)
    payload = source_registry.catalog(health)
    for item in payload.get("sources", []):
        key = item.get("key", "")
        merged = _merge_catalog_health(
            item,
            health.get(key),
            checkpoint_health.get(key),
        )
        item["health"] = merged
        if merged.get("credibility_score") is not None:
            item["credibility_score"] = merged.get("credibility_score")

    if fast:
        _CATALOG_CACHE_FAST["ts"] = time.time()
        _CATALOG_CACHE_FAST["data"] = payload
    return payload

@router.get("/news", summary="Search NewsAPI for a keyword query", response_model=SourceEventsResponse)
async def search_news(
    request:   Request,
    query:     str           = Query(...,  description="Search keywords"),
    limit:     int           = Query(10,   ge=1,  le=100),
    days_back: int           = Query(1,    ge=1,  le=30,
                                     description="How many days of history to fetch"),
    language:  str           = Query("en", description="ISO 639-1 language code"),
    sort_by:   str           = Query("publishedAt",
                                     description="publishedAt | relevancy | popularity"),
    fast:      bool          = Query(True, description="Cache-first: return cached/202 instead of live fetch"),
):
    """
    Direct access to the NewsAPI extractor.
    Requires NEWSAPI_KEY to be set in .env.
    """
    from extractors.news import NewsExtractor
    return await _run_and_enrich(
        request, NewsExtractor,
        dict(query=query, limit=limit, days_back=days_back,
             language=language, sort_by=sort_by),
        cache_only=fast,
    )

@router.get("/reddit", summary="Search Reddit posts", response_model=SourceEventsResponse)
async def search_reddit(
    request:   Request,
    query:     str           = Query(...,  description="Search keywords"),
    limit:     int           = Query(25,   ge=1, le=100),
    sort:      str           = Query("new",
                                     description="new | hot | relevance | top"),
    subreddit: Optional[str] = Query(None, description="Restrict to one subreddit (no r/ prefix)"),
    fast:      bool          = Query(True, description="Cache-first: return cached/202 instead of live fetch"),
):
    """
    Direct access to the Reddit extractor.
    Uses Reddit's public JSON API â€” no credentials required.
    """
    from extractors.socials import RedditExtractor
    return await _run_and_enrich(
        request, RedditExtractor,
        dict(query=query, limit=limit, sort=sort, subreddit=subreddit),
        cache_only=fast,
    )

@router.get("/youtube", summary="Search YouTube videos via yt-dlp", response_model=SourceEventsResponse)
async def search_youtube(
    request: Request,
    query:   str = Query(..., description="Search keywords"),
    limit:   int = Query(10,  ge=1, le=50),
    fast:    bool = Query(True, description="Cache-first: return cached/202 instead of live fetch"),
):
    """
    Direct access to the YouTube extractor.
    Uses yt-dlp â€” no API key required.
    """
    from extractors.socials import YouTubeExtractor
    return await _run_and_enrich(
        request, YouTubeExtractor,
        dict(query=query, limit=limit),
        cache_only=fast,
    )

# Ã¢â€â‚¬Ã¢â€â‚¬ /sources/rss Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬

@router.get("/rss", summary="Search curated RSS/Atom feeds", response_model=SourceEventsResponse)
async def search_rss(
    request: Request,
    query:   str = Query(..., description="Search keywords"),
    limit:   int = Query(20,  ge=1, le=50),
    timeout: float = Query(10.0, ge=1.0, le=30.0, description="Upstream timeout (seconds)"),
    fast:    bool = Query(True, description="Cache-first: return cached/202 instead of live fetch"),
):
    """
    Direct access to the RSS extractor across curated global + finance feeds.
    """
    from extractors.rss import RSSExtractor
    try:
        return await asyncio.wait_for(
            _run_and_enrich(
                request, RSSExtractor,
                dict(query=query, limit=limit),
                cache_only=fast,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return {
            "total": 0,
            "source": RSSExtractor.source_name,
            "events": [],
            "warning": f"rss timeout after {timeout:.1f}s",
        }


# Ã¢â€â‚¬Ã¢â€â‚¬ /sources/hackernews Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬

@router.get("/hackernews", summary="Search Hacker News stories", response_model=SourceEventsResponse)
async def search_hackernews(
    request: Request,
    query:   str = Query(..., description="Search keywords"),
    limit:   int = Query(20,  ge=1, le=50),
    timeout: float = Query(10.0, ge=1.0, le=30.0, description="Upstream timeout (seconds)"),
    fast:    bool = Query(True, description="Cache-first: return cached/202 instead of live fetch"),
):
    from extractors.hackernews import HackerNewsExtractor
    try:
        return await asyncio.wait_for(
            _run_and_enrich(
                request, HackerNewsExtractor,
                dict(query=query, limit=limit),
                cache_only=fast,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return {
            "total": 0,
            "source": HackerNewsExtractor.source_name,
            "events": [],
            "warning": f"hackernews timeout after {timeout:.1f}s",
        }


# Ã¢â€â‚¬Ã¢â€â‚¬ /sources/telegram Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬

@router.get("/telegram", summary="Search Telegram channels", response_model=SourceEventsResponse)
async def search_telegram(
    request: Request,
    query:   str = Query(..., description="Search keywords"),
    limit:   int = Query(20,  ge=1, le=50),
    timeout: float = Query(10.0, ge=1.0, le=30.0, description="Upstream timeout (seconds)"),
    fast:    bool = Query(True, description="Cache-first: return cached/202 instead of live fetch"),
):
    from extractors.telegram_monitor import TelegramExtractor
    try:
        return await asyncio.wait_for(
            _run_and_enrich(
                request, TelegramExtractor,
                dict(query=query, limit=limit),
                cache_only=fast,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return {
            "total": 0,
            "source": TelegramExtractor.source_name,
            "events": [],
            "warning": f"telegram timeout after {timeout:.1f}s",
        }


# Ã¢â€â‚¬Ã¢â€â‚¬ /sources/twitter Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬

@router.get("/twitter", summary="Search X/Twitter recent posts", response_model=SourceEventsResponse)
async def search_twitter(
    request: Request,
    query:   str = Query(..., description="Twitter v2 recent search query"),
    limit:   int = Query(25, ge=10, le=100),
    lang:    str = Query("en", description="ISO language code"),
    timeout: float = Query(10.0, ge=1.0, le=30.0, description="Upstream timeout (seconds)"),
    fast:    bool = Query(True, description="Cache-first: return cached/202 instead of live fetch"),
):
    from extractors.twitter import TwitterExtractor
    try:
        return await asyncio.wait_for(
            _run_and_enrich(
                request, TwitterExtractor,
                dict(query=query, limit=limit, lang=lang),
                cache_only=fast,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return {
            "total": 0,
            "source": TwitterExtractor.source_name,
            "events": [],
            "warning": f"twitter timeout after {timeout:.1f}s",
        }


# Ã¢â€â‚¬Ã¢â€â‚¬ /sources/gdelt Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬

@router.get("/gdelt", summary="Search GDELT (doc + geo by default)", response_model=SourceEventsResponse)
async def search_gdelt(
    request: Request,
    query:   str = Query(..., description="GDELT query string"),
    limit:   int = Query(25, ge=1, le=100),
    apis:    Optional[str] = Query("doc,geo", description="Comma-separated APIs: doc, geo, context, tv"),
    delay:   float = Query(0.5, ge=0.0, le=10.0),
    timeout: float = Query(12.0, ge=1.0, le=60.0, description="Upstream timeout (seconds)"),
    fast:    bool = Query(True, description="Cache-first: return cached/202 instead of live fetch"),
):
    from extractors.gdelt import GDELTExtractor
    apis_list = [a.strip() for a in apis.split(",") if a.strip()] if apis else ["doc"]
    try:
        return await asyncio.wait_for(
            _run_and_enrich(
                request, GDELTExtractor,
                dict(query=query, limit=limit, apis=apis_list, delay=delay),
                cache_only=fast,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return {
            "total": 0,
            "source": GDELTExtractor.source_name,
            "events": [],
            "warning": f"gdelt timeout after {timeout:.1f}s",
        }


@router.get("/usgs", summary="USGS earthquake feed with custom parameters", response_model=SourceEventsResponse)
async def search_usgs(
    request:    Request,
    limit:      int            = Query(10,  ge=1,   le=100),
    min_mag:    float          = Query(4.0, ge=0.0, le=10.0,
                                       description="Minimum magnitude"),
    hours_back: int            = Query(24,  ge=1,   le=168,
                                       description="Hours of history to fetch"),
    lat_min:    Optional[float] = Query(None, description="Bounding box: min latitude"),
    lon_min:    Optional[float] = Query(None, description="Bounding box: min longitude"),
    lat_max:    Optional[float] = Query(None, description="Bounding box: max latitude"),
    lon_max:    Optional[float] = Query(None, description="Bounding box: max longitude"),
    fast:       bool            = Query(True, description="Cache-first: return cached/202 instead of live fetch"),
):
    """
    Direct access to the USGS extractor.
    Supports bounding box filtering for regional earthquake monitoring.
    """
    from extractors.usgs import USGSExtractor
    bbox = (lat_min, lon_min, lat_max, lon_max) \
        if all(v is not None for v in [lat_min, lon_min, lat_max, lon_max]) \
        else None
    return await _run_and_enrich(
        request, USGSExtractor,
        dict(limit=limit, min_mag=min_mag, hours_back=hours_back, bbox=bbox),
        run_nlp=False,   # USGS events are structured â€” no NER needed
        cache_only=fast,
    )

@router.get("/stocks", summary="Yahoo Finance — fetch specific tickers", response_model=SourceEventsResponse)
async def search_stocks(
    request: Request,
    tickers: Optional[str] = Query(
        None,
        description="Comma-separated ticker symbols, e.g. AAPL,TSLA,NVDA. "
                    "Defaults to the configured watchlist."
    ),
    limit:   int           = Query(20, ge=1, le=50),
    fast:    bool          = Query(True, description="Cache-first: return cached/202 instead of live fetch"),
):
    """
    Direct access to the stock extractor.
    Pass custom tickers to track any publicly listed company.
    """
    from extractors.stocks import StockExtractor, DEFAULT_TICKERS

    ticker_dict = DEFAULT_TICKERS
    if tickers:
        # Build a dict from the comma-separated list; use symbol as display name
        symbols     = [t.strip().upper() for t in tickers.split(",") if t.strip()]
        ticker_dict = {sym: sym for sym in symbols}

    return await _run_and_enrich(
        request, StockExtractor,
        dict(tickers=ticker_dict, limit=limit),
        run_nlp=False,   # Stock events are structured â€” NER would be noisy
        cache_only=fast,
    )

@router.get("/opensky", summary="OpenSky live aircraft positions", response_model=SourceEventsResponse)
async def search_opensky(
    request:        Request,
    limit:          int            = Query(50,   ge=1,  le=200),
    callsign:       Optional[str]  = Query(None, description="Filter by callsign (partial match)"),
    icao24:         Optional[str]  = Query(None, description="Filter by ICAO 24-bit hex address"),
    lat_min:        Optional[float] = Query(None, description="Bounding box: min latitude"),
    lon_min:        Optional[float] = Query(None, description="Bounding box: min longitude"),
    lat_max:        Optional[float] = Query(None, description="Bounding box: max latitude"),
    lon_max:        Optional[float] = Query(None, description="Bounding box: max longitude"),
    airborne_only:  bool           = Query(False, description="Exclude aircraft on the ground"),
    on_ground_only: bool           = Query(False, description="Only aircraft on the ground"),
    fast:           bool           = Query(True, description="Cache-first: return cached/202 instead of live fetch"),
):
    """
    Direct access to the OpenSky extractor.
    Filter by callsign, ICAO code, geographic bounding box, or ground status.
    No credentials required (anonymous rate limit applies).
    """
    from extractors.opensky import OpenSkyExtractor
    bbox = (lat_min, lon_min, lat_max, lon_max) \
        if all(v is not None for v in [lat_min, lon_min, lat_max, lon_max]) \
        else None
    return await _run_and_enrich(
        request, OpenSkyExtractor,
        dict(limit=limit, callsign=callsign, icao24=icao24,
             bbox=bbox, airborne_only=airborne_only,
             on_ground_only=on_ground_only),
        run_nlp=False,   # Flight telemetry - NER not useful
        cache_only=fast,
    )


# Ã¢â€â‚¬Ã¢â€â‚¬ /sources/ais Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬

@router.get("/ais", summary="AIS vessel tracking", response_model=SourceEventsResponse)
async def search_ais(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    fast:  bool = Query(True, description="Cache-first: return cached/202 instead of live fetch"),
):
    """
    Direct access to AIS vessel telemetry (aisstream.io or legacy AIS endpoint).
    """
    from extractors.ais import AISExtractor
    return await _run_and_enrich(
        request, AISExtractor,
        dict(limit=limit),
        run_nlp=False,
        cache_only=fast,
    )


# ── /sources/bluesky ──────────────────────────────────────────────────────────

@router.get("/bluesky", summary="Search Bluesky posts (public, no auth)", response_model=SourceEventsResponse)
async def search_bluesky(
    request: Request,
    query:   str = Query("", description="Search keywords (empty = trending)"),
    limit:   int = Query(25, ge=1, le=100),
    timeout: float = Query(15.0, ge=1.0, le=30.0, description="Upstream timeout (seconds)"),
):
    """
    Direct access to the Bluesky extractor.
    Uses the public AT Protocol XRPC endpoint — no credentials required.
    """
    try:
        from extractors.bluesky import fetch as _fetch
        result = await asyncio.wait_for(_fetch(query=query, limit=limit), timeout=timeout)
        return result
    except asyncio.TimeoutError:
        return {"total": 0, "source": "bluesky", "events": [], "warning": f"bluesky timeout after {timeout:.1f}s"}
    except Exception as exc:
        logger.error("search_bluesky failed: %s", exc)
        return {"total": 0, "source": "bluesky", "events": [], "error": str(exc)}


# ── /sources/cisa_kev ─────────────────────────────────────────────────────────

@router.get("/cisa_kev", summary="CISA Known Exploited Vulnerabilities (no auth)", response_model=SourceEventsResponse)
async def search_cisa_kev(
    request: Request,
    limit:   int   = Query(50, ge=1, le=500),
    timeout: float = Query(20.0, ge=1.0, le=60.0, description="Upstream timeout (seconds)"),
):
    """
    Direct access to the CISA KEV feed.
    No credentials required. Returns structured vulnerability events.
    """
    try:
        from extractors.cisa_kev import fetch as _fetch
        result = await asyncio.wait_for(_fetch(limit=limit), timeout=timeout)
        return result
    except asyncio.TimeoutError:
        return {"total": 0, "source": "cisa_kev", "events": [], "warning": f"cisa_kev timeout after {timeout:.1f}s"}
    except Exception as exc:
        logger.error("search_cisa_kev failed: %s", exc)
        return {"total": 0, "source": "cisa_kev", "events": [], "error": str(exc)}


# ── /sources/treasury ─────────────────────────────────────────────────────────

@router.get("/treasury", summary="US Treasury Fiscal Data (no auth)", response_model=SourceEventsResponse)
async def search_treasury(
    request:  Request,
    endpoint: str  = Query("v1/debt/mspd/mspd_table_1", description="Fiscal Data API endpoint path"),
    limit:    int  = Query(10, ge=1, le=100),
    timeout:  float = Query(20.0, ge=1.0, le=60.0, description="Upstream timeout (seconds)"),
):
    """
    Direct access to the US Treasury Fiscal Data API.
    No credentials required. Default endpoint is the Monthly Statement of the Public Debt.
    """
    try:
        from extractors.treasury import fetch as _fetch
        result = await asyncio.wait_for(_fetch(endpoint=endpoint, limit=limit), timeout=timeout)
        return result
    except asyncio.TimeoutError:
        return {"total": 0, "source": "treasury", "events": [], "warning": f"treasury timeout after {timeout:.1f}s"}
    except Exception as exc:
        logger.error("search_treasury failed: %s", exc)
        return {"total": 0, "source": "treasury", "events": [], "error": str(exc)}


# ── Source credibility ────────────────────────────────────────────────────────

from pydantic import BaseModel as _CredModel
from typing import Optional as _Opt
from fastapi import Body as _Body, HTTPException as _HTTPEx


class CredibilityUpdate(_CredModel):
    credibility_score: _Opt[float] = None
    credibility_note:  _Opt[str]   = None


@router.patch("/{source_name}/credibility", summary="Set source credibility score")
async def set_source_credibility(source_name: str, request: Request, body: CredibilityUpdate = _Body(...)):
    """Analyst-assigned credibility score (0.0–1.0) for a named source."""
    if not request.app.state.db_available:
        raise _HTTPEx(status_code=503, detail="Database unavailable")
    try:
        from sqlalchemy import update as _upd
        from storage.database import SourceCheckpointModel, get_session
        async with get_session() as session:
            res = await session.execute(
                _upd(SourceCheckpointModel)
                .where(SourceCheckpointModel.source == source_name)
                .values(credibility_score=body.credibility_score, credibility_note=body.credibility_note)
            )
            if res.rowcount == 0:
                raise _HTTPEx(status_code=404, detail=f"Source '{source_name}' not found")
        return {"source": source_name, "credibility_score": body.credibility_score, "credibility_note": body.credibility_note}
    except _HTTPEx:
        raise
    except Exception as exc:
        logger.error("credibility update failed: %s", exc)
        raise _HTTPEx(status_code=500, detail=str(exc))


@router.get("/{source_name}/credibility", summary="Get source credibility score")
async def get_source_credibility(source_name: str, request: Request):
    if not request.app.state.db_available:
        return {"source": source_name, "credibility_score": None, "credibility_note": None}
    try:
        from sqlalchemy import select as _sel
        from storage.database import SourceCheckpointModel, get_session
        async with get_session() as session:
            row = (await session.execute(
                _sel(SourceCheckpointModel).where(SourceCheckpointModel.source == source_name)
            )).scalar_one_or_none()
        if row is None:
            return {"source": source_name, "credibility_score": None, "credibility_note": None}
        return {"source": source_name, "credibility_score": row.credibility_score, "credibility_note": row.credibility_note}
    except Exception as exc:
        logger.error("get credibility failed: %s", exc)
        return {"source": source_name, "credibility_score": None, "credibility_note": None}
