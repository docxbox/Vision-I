"""
api/routers/intelligence.py
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Advanced intelligence endpoints:
  GET /intelligence/escalation      â€” per-region escalation probability scores
  GET /intelligence/bot-scores      â€” actor bot-score rankings
  GET /intelligence/credibility     â€” source credibility scores
  GET /intelligence/community-graph â€” temporal actor community graph
  GET /intelligence/causality       â€” Granger causality between two time series
"""

import logging
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from core.utils import utcnow_iso

logger = logging.getLogger("vision_i.api.intelligence")
router = APIRouter(tags=["Intelligence"])

# ── Response schemas ───────────────────────────────────────────────────────

class EscalationResponse(BaseModel):
    scores: List[Any] = Field(default_factory=list)
    generated_at: str = ""

class BotScoresResponse(BaseModel):
    total: int = 0
    actors: List[Any] = Field(default_factory=list)

class CredibilityResponse(BaseModel):
    sources: List[Any] = Field(default_factory=list)

class CommunityGraphResponse(BaseModel):
    communities: int = 0
    actor_count: int = 0
    temporal_graph: Dict[str, Any] = Field(default_factory=dict)

class CausalityResponse(BaseModel):
    series_a: str = ""
    series_b: str = ""
    lag_hours: int = 0
    granger_p_value: Optional[float] = None
    significant: bool = False
    interpretation: Optional[str] = None
    time_series_a: Optional[List[Dict[str, Any]]] = None
    time_series_b: Optional[List[Dict[str, Any]]] = None
    error: Optional[str] = None

class UnrestWatchResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

_VALID_SERIES = {
    "market_volatility",
    "conflict_events",
    "narrative_intensity",
    "aircraft_activity",
    "vessel_activity",
    "social_volume",
}

# SQL fragments for each named series (hourly counts over the window)
_SERIES_SQL = {
    "market_volatility": (
        "SELECT date_trunc('hour', timestamp) AS hour, COUNT(*) AS value "
        "FROM events "
        "WHERE timestamp > NOW() - INTERVAL ':window days' "
        "  AND event_type = 'market' "
        "  AND ABS(sentiment_score - 0.5) > 0.2 "
        "GROUP BY hour ORDER BY hour"
    ),
    "conflict_events": (
        "SELECT date_trunc('hour', timestamp) AS hour, COUNT(*) AS value "
        "FROM events "
        "WHERE timestamp > NOW() - INTERVAL ':window days' "
        "  AND tags @> ARRAY['conflict','military','attack','explosion'] "
        "GROUP BY hour ORDER BY hour"
    ),
    "narrative_intensity": (
        "SELECT date_trunc('hour', timestamp) AS hour, COUNT(*) AS value "
        "FROM events "
        "WHERE timestamp > NOW() - INTERVAL ':window days' "
        "  AND source IN ('gdelt_doc','newsapi','rss') "
        "GROUP BY hour ORDER BY hour"
    ),
    "aircraft_activity": (
        "SELECT date_trunc('hour', timestamp) AS hour, COUNT(*) AS value "
        "FROM events "
        "WHERE timestamp > NOW() - INTERVAL ':window days' "
        "  AND source ILIKE '%opensky%' "
        "GROUP BY hour ORDER BY hour"
    ),
    "vessel_activity": (
        "SELECT date_trunc('hour', timestamp) AS hour, COUNT(*) AS value "
        "FROM events "
        "WHERE timestamp > NOW() - INTERVAL ':window days' "
        "  AND source ILIKE '%ais%' "
        "GROUP BY hour ORDER BY hour"
    ),
    "social_volume": (
        "SELECT date_trunc('hour', timestamp) AS hour, COUNT(*) AS value "
        "FROM events "
        "WHERE timestamp > NOW() - INTERVAL ':window days' "
        "  AND source IN ('twitter','reddit','telegram') "
        "GROUP BY hour ORDER BY hour"
    ),
}

@router.get("/escalation", summary="Per-region escalation probability scores", response_model=EscalationResponse)
async def get_escalation_scores(request: Request):
    """
    Return escalation probability scores per region.
    Reads from Redis precomputed cache; falls back to live computation.
    """
    event_bus = request.app.state.event_bus
    if event_bus:
        try:
            cached = await event_bus.cache_get("precomputed:escalation_scores")
            if cached:
                return cached
        except Exception:
            pass

    # Cache miss â€” compute live
    try:
        import asyncio
        from storage.database import get_session
        from intelligence.escalation_scorer import EscalationScorer
        async with get_session() as session:
            scorer = EscalationScorer(session)
            scores = await asyncio.wait_for(scorer.score_all_regions(window_hours=6), timeout=15.0)
        return {"scores": scores, "generated_at": utcnow_iso()}
    except asyncio.TimeoutError:
        logger.warning("escalation scorer timed out")
        return {"scores": [], "generated_at": utcnow_iso()}
    except Exception as exc:
        logger.error("escalation scores failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

@router.get("/bot-scores", summary="Actor bot-score rankings", response_model=BotScoresResponse)
async def get_bot_scores(
    request: Request,
    window_hours: int = Query(24, ge=1, le=168),
    min_events: int = Query(3, ge=1),
    limit: int = Query(50, ge=1, le=200),
):
    """
    Return actors ranked by bot probability score.
    Caches to Redis for 5 min to prevent worker saturation on concurrent calls.
    """
    cache_key = f"cache:bot_scores:{window_hours}:{min_events}:{limit}"
    event_bus = request.app.state.event_bus
    if event_bus:
        try:
            cached = await event_bus.cache_get(cache_key)
            if cached:
                return cached
        except Exception:
            pass

    try:
        import asyncio
        from storage.database import get_session
        from intelligence.bot_score import BotScorer
        async with get_session() as session:
            scorer = BotScorer(session)
            results = await asyncio.wait_for(
                scorer.score_actors(window_hours=window_hours, min_events=min_events),
                timeout=25.0,
            )

        results.sort(key=lambda r: r.bot_score if hasattr(r, "bot_score") else r.get("bot_score", 0), reverse=True)
        actors = []
        for r in results[:limit]:
            actors.append(r.as_dict() if hasattr(r, "as_dict") else r)

        payload = {"total": len(results), "actors": actors}
        if event_bus:
            try:
                await event_bus.cache_set(cache_key, payload, ttl_seconds=300)
            except Exception:
                pass
        return payload
    except asyncio.TimeoutError:
        logger.warning("bot_scores timed out after 25 s")
        return {"total": 0, "actors": []}
    except Exception as exc:
        logger.error("bot_scores failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

@router.get("/credibility", summary="Source credibility scores", response_model=CredibilityResponse)
async def get_credibility_scores(request: Request):
    """Return credibility scores for all known sources, sorted descending.
    Caches to Redis for 10 min â€” O(nÂ²) computation is expensive."""
    event_bus = request.app.state.event_bus
    if event_bus:
        try:
            cached = await event_bus.cache_get("cache:credibility_scores")
            if cached:
                return cached
        except Exception:
            pass

    if not getattr(request.app.state, "db_available", False):
        return {"sources": []}

    try:
        import asyncio
        from storage.database import get_session
        from intelligence.credibility import CredibilityTracker

        async def _compute():
            async with get_session() as session:
                tracker = CredibilityTracker(session)
                return await tracker.compute_all()

        results = await asyncio.wait_for(_compute(), timeout=15.0)

        results.sort(
            key=lambda r: r.credibility_score if hasattr(r, "credibility_score") else r.get("credibility_score", 0),
            reverse=True,
        )
        sources = []
        for r in results:
            sources.append(r.as_dict() if hasattr(r, "as_dict") else r)

        payload = {"sources": sources}
        if event_bus:
            try:
                await event_bus.cache_set("cache:credibility_scores", payload, ttl_seconds=600)
            except Exception:
                pass
        return payload
    except asyncio.TimeoutError:
        logger.warning("credibility compute_all timed out")
        return {"sources": []}
    except Exception as exc:
        logger.error("credibility scores failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

@router.get("/community-graph", summary="Temporal actor community graph", response_model=CommunityGraphResponse)
async def get_community_graph(
    request: Request,
    since_hours: int = Query(48, ge=1, le=720),
):
    """
    Return actor community graph with temporal edges.
    Reads from Redis precomputed cache; falls back to live computation.
    """
    event_bus = request.app.state.event_bus
    if event_bus:
        try:
            cached = await event_bus.cache_get("precomputed:community_graph")
            if cached:
                return cached
        except Exception:
            pass

    # Cache miss â€” compute from Neo4j
    try:
        import asyncio
        graph = request.app.state.graph

        loop = asyncio.get_running_loop()
        community_map = await loop.run_in_executor(None, graph.detect_communities)
        temporal = await loop.run_in_executor(None, graph.get_temporal_graph, since_hours)

        num_communities = len(set(community_map.values())) if community_map else 0
        actor_count = len(temporal.get("nodes", []))

        return {
            "communities": num_communities,
            "actor_count": actor_count,
            "temporal_graph": temporal,
        }
    except Exception as exc:
        logger.error("community_graph failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

@router.get("/causality", summary="Granger causality between two time series", response_model=CausalityResponse)
async def get_causality(
    request: Request,
    series_a: str = Query(..., description="Source time series name"),
    series_b: str = Query(..., description="Target time series name"),
    lag_hours: int = Query(48, ge=6, le=336),
    window_days: int = Query(14, ge=1, le=90),
):
    """
    Test whether series_a Granger-causes series_b over the given window.

    Valid series names: market_volatility | conflict_events | narrative_intensity
                        | aircraft_activity | vessel_activity | social_volume
    """
    if series_a not in _VALID_SERIES:
        raise HTTPException(
            status_code=422,
            detail=f"series_a must be one of: {', '.join(sorted(_VALID_SERIES))}",
        )
    if series_b not in _VALID_SERIES:
        raise HTTPException(
            status_code=422,
            detail=f"series_b must be one of: {', '.join(sorted(_VALID_SERIES))}",
        )

    # statsmodels availability check
    try:
        from statsmodels.tsa.stattools import grangercausalitytests
        import numpy as np
    except ImportError:
        return {
            "series_a": series_a,
            "series_b": series_b,
            "lag_hours": lag_hours,
            "granger_p_value": None,
            "significant": False,
            "interpretation": None,
            "error": "statsmodels required",
        }

    try:
        from storage.database import get_session
        from sqlalchemy import text

        sql_a = _SERIES_SQL[series_a].replace(":window", str(window_days))
        sql_b = _SERIES_SQL[series_b].replace(":window", str(window_days))

        async with get_session() as session:
            res_a = await session.execute(text(sql_a))
            rows_a = [(row[0], int(row[1])) for row in res_a]

            res_b = await session.execute(text(sql_b))
            rows_b = [(row[0], int(row[1])) for row in res_b]

        # Build aligned hourly index
        hours_a = {r[0]: r[1] for r in rows_a}
        hours_b = {r[0]: r[1] for r in rows_b}
        all_hours = sorted(set(hours_a) | set(hours_b))

        ts_a = [{"hour": h.isoformat() if hasattr(h, "isoformat") else str(h), "value": hours_a.get(h, 0)} for h in all_hours]
        ts_b = [{"hour": h.isoformat() if hasattr(h, "isoformat") else str(h), "value": hours_b.get(h, 0)} for h in all_hours]

        if len(all_hours) < 20:
            return {
                "series_a": series_a,
                "series_b": series_b,
                "lag_hours": lag_hours,
                "granger_p_value": None,
                "significant": False,
                "interpretation": None,
                "error": "Insufficient data (fewer than 20 hourly observations)",
                "time_series_a": ts_a,
                "time_series_b": ts_b,
            }

        vec_a = np.array([hours_a.get(h, 0) for h in all_hours], dtype=float)
        vec_b = np.array([hours_b.get(h, 0) for h in all_hours], dtype=float)
        data_2d = np.column_stack([vec_b, vec_a])  # [target, cause] per statsmodels convention

        maxlag = max(1, lag_hours // 6)
        test_result = grangercausalitytests(data_2d, maxlag=maxlag, verbose=False)

        # Extract minimum p-value across all tested lags
        best_p = 1.0
        for lag_val, lag_data in test_result.items():
            ssr_ftest = lag_data[0].get("ssr_ftest")
            if ssr_ftest:
                p = ssr_ftest[1]  # p-value is index 1
                if p < best_p:
                    best_p = p

        significant = best_p < 0.05
        if significant:
            interpretation = (
                f"{series_a} Granger-causes {series_b} at 95% confidence"
            )
        else:
            interpretation = (
                f"No significant Granger causality detected from {series_a} to {series_b}"
            )

        return {
            "series_a": series_a,
            "series_b": series_b,
            "lag_hours": lag_hours,
            "granger_p_value": round(best_p, 6),
            "significant": significant,
            "interpretation": interpretation,
            "time_series_a": ts_a,
            "time_series_b": ts_b,
        }

    except Exception as exc:
        logger.error("causality test failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/unrest-watch", summary="Unified unrest watch across regions, narratives, actors, and alerts", response_model=UnrestWatchResponse)
async def get_unrest_watch(
    request: Request,
    window_hours: int = Query(72, ge=6, le=24 * 14),
):
    event_bus = request.app.state.event_bus
    cache_key = f"cache:intelligence:unrest-watch:{window_hours}"

    if window_hours == 72 and event_bus:
        try:
            cached = await event_bus.cache_get("precomputed:unrest_watch")
            if cached:
                cached["_served_from"] = "precomputed"
                return cached
        except Exception:
            pass

    if event_bus:
        try:
            cached = await event_bus.cache_get(cache_key)
            if cached:
                cached["_served_from"] = "cache"
                return cached
        except Exception:
            pass

    try:
        from intelligence.unrest_engine import UnrestWatchEngine
        from storage.database import get_session

        async with get_session() as session:
            payload = await UnrestWatchEngine(session).build_watch(window_hours=window_hours)

        if event_bus:
            try:
                await event_bus.cache_set(cache_key, payload, ttl_seconds=180)
            except Exception:
                pass
        return payload
    except Exception as exc:
        logger.error("unrest_watch failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
