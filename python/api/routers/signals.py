"""
api/routers/signals.py
----------------------
Signal endpoints - list, detail, semantic search, clusters.

GET  /signals              - list signals (with filters)
GET  /signals/{signal_id}  - single signal detail
GET  /signals/search       - semantic similarity search
GET  /signals/clusters     - recent signal clusters
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

logger = logging.getLogger("vision_i.api.signals")
router = APIRouter(tags=["Signals"])


# ── Response schemas ─────────────────────────────────────────────────────────

class SignalSchema(BaseModel):
    signal_id:      Optional[str]   = None
    source_event_id: Optional[str]  = None
    source:         Optional[str]   = None
    signal_type:    Optional[str]   = None
    title:          Optional[str]   = None
    timestamp:      Optional[str]   = None
    actors:         Optional[Any]   = None
    location_name:  Optional[str]   = None
    location_lat:   Optional[float] = None
    location_lon:   Optional[float] = None
    sentiment_score: Optional[float] = None
    confidence:     Optional[float] = None
    cluster_id:     Optional[str]   = None
    confidence_bucket: Optional[str] = None

    class Config:
        extra = "allow"


class SignalListResponse(BaseModel):
    total:   int               = 0
    signals: List[SignalSchema] = Field(default_factory=list)


class SignalSearchResponse(BaseModel):
    query:     str
    threshold: float
    total:     int                = 0
    signals:   List[SignalSchema] = Field(default_factory=list)
    _mode:     Optional[str]      = None


class SignalClusterSchema(BaseModel):
    cluster_id:   Optional[str]  = None
    signal_count: int             = 0
    sources:      List[str]       = Field(default_factory=list)
    earliest:     Optional[str]   = None
    latest:       Optional[str]   = None
    span_hours:   Optional[float] = None

    class Config:
        extra = "allow"


class SignalClustersResponse(BaseModel):
    clusters:      List[SignalClusterSchema] = Field(default_factory=list)
    _served_from:  Optional[str]             = None


class CorrelationSummaryResponse(BaseModel):
    total_signals:    int            = 0
    clustered_signals: int           = 0
    cluster_count:    int            = 0
    unclustered:      int            = 0
    coverage_pct:     float          = 0.0
    by_source:        Dict[str, int] = Field(default_factory=dict)
    _served_from:     Optional[str]  = None


class ConfidenceDistributionResponse(BaseModel):
    high:     int = 0
    medium:   int = 0
    low:      int = 0
    unscored: int = 0


def _confidence_bucket(value: Optional[float]) -> str:
    if value is None:
        return "unscored"
    if value >= 0.75:
        return "high"
    if value >= 0.5:
        return "medium"
    return "low"


@router.get("", summary="List signals", response_model=SignalListResponse)
async def list_signals(
    request: Request,
    source: Optional[str] = Query(None),
    cluster_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    """List recent signals, optionally filtered by source or cluster."""
    from storage.database import get_session
    from sqlalchemy import text

    try:
        async with get_session() as session:
            where = ["1=1"]
            params = {"limit": limit}
            if source:
                where.append("source = :source")
                params["source"] = source
            if cluster_id:
                where.append("cluster_id = :cluster_id")
                params["cluster_id"] = cluster_id

            where_sql = " AND ".join(where)
            result = await session.execute(text(f"""
                SELECT signal_id, source_event_id, source, signal_type,
                       title, timestamp, actors, location_name,
                       location_lat, location_lon, sentiment_score,
                       confidence, cluster_id, meta
                FROM signals
                WHERE {where_sql}
                ORDER BY timestamp DESC
                LIMIT :limit
            """), params)
            rows = []
            for r in result.mappings().all():
                row = dict(r)
                if row.get("timestamp") and hasattr(row["timestamp"], "isoformat"):
                    row["timestamp"] = row["timestamp"].isoformat()
                rows.append(row)

        return {"total": len(rows), "signals": rows}
    except Exception as exc:
        logger.error("list_signals failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/search", summary="Semantic similarity search", response_model=SignalSearchResponse)
async def search_signals(
    request: Request,
    q: str = Query(..., min_length=2, max_length=500, description="Search text"),
    threshold: float = Query(0.5, ge=0.0, le=1.0),
    limit: int = Query(20, ge=1, le=100),
):
    """
    Semantic search: embed the query text and find similar signals via pgvector.
    Returns signals with cosine similarity >= threshold.
    """
    # In production we may run embeddings in a separate worker process.
    # Fallback to DB-native vector search when embedder isn't loaded in this API container.
    embedder = request.app.state.embedder
    if not embedder or not embedder.available:
        try:
            from storage.signal_repo import SignalRepository
            repo = SignalRepository()
            results = await repo.find_similar_text(
                query=q,
                threshold=threshold,
                limit=limit,
            )
            for r in results:
                if r.get("timestamp") and hasattr(r["timestamp"], "isoformat"):
                    r["timestamp"] = r["timestamp"].isoformat()
            return {"query": q, "threshold": threshold, "total": len(results), "signals": results, "_mode": "db_fallback"}
        except Exception:
            raise HTTPException(status_code=503, detail="Embedding service not available")

    try:
        import asyncio
        loop = asyncio.get_running_loop()
        embedding = await loop.run_in_executor(None, lambda: embedder.embed_single(q))

        from storage.signal_repo import SignalRepository
        repo = SignalRepository()
        results = await repo.find_similar(
            embedding=embedding,
            threshold=threshold,
            limit=limit,
        )

        # Convert datetime objects to strings for JSON serialization
        for r in results:
            if r.get("timestamp") and hasattr(r["timestamp"], "isoformat"):
                r["timestamp"] = r["timestamp"].isoformat()

        return {"query": q, "threshold": threshold, "total": len(results), "signals": results}
    except Exception as exc:
        logger.error("search_signals failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/clusters", summary="Recent signal clusters", response_model=SignalClustersResponse)
async def get_clusters(request: Request, limit: int = Query(20, ge=1, le=100)):
    """Read precomputed signal clusters from Redis."""
    event_bus = request.app.state.event_bus
    if event_bus:
        try:
            cached = await event_bus.cache_get("precomputed:signal_clusters")
            if cached:
                return {"clusters": cached[:limit], "_served_from": "precomputed"}
        except Exception:
            pass

    # Fallback: query DB
    from storage.database import get_session
    from sqlalchemy import text
    try:
        async with get_session() as session:
            result = await session.execute(text("""
                SELECT cluster_id, COUNT(*) AS cnt,
                       array_agg(DISTINCT source) AS sources,
                       MIN(timestamp) AS earliest, MAX(timestamp) AS latest
                FROM signals
                WHERE cluster_id IS NOT NULL
                  AND timestamp > NOW() - INTERVAL '24 hours'
                GROUP BY cluster_id
                ORDER BY cnt DESC
                LIMIT :limit
            """), {"limit": limit})
            clusters = []
            for row in result:
                signal_count = row[1]
                earliest = row[3]
                latest = row[4]
                if earliest and latest:
                    span_hours = round((latest - earliest).total_seconds() / 3600, 2)
                else:
                    span_hours = 0.0
                clusters.append({
                    "cluster_id": row[0],
                    "signal_count": signal_count,
                    "sources": row[2] if row[2] else [],
                    "shared_actors": [],
                    "composite_score": round(min(1.0, 0.35 + signal_count * 0.08), 4),
                    "representative_title": None,
                    "time_span_hours": span_hours,
                    "earliest": earliest.isoformat() if earliest else None,
                    "latest": latest.isoformat() if latest else None,
                })
        return {"clusters": clusters}
    except Exception as exc:
        logger.error("get_clusters failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/correlation-summary", summary="Signal correlation summary", response_model=CorrelationSummaryResponse)
async def get_correlation_summary(request: Request):
    """Read precomputed correlation summary from Redis, fallback to DB."""
    event_bus = request.app.state.event_bus
    if event_bus:
        try:
            cached = await event_bus.cache_get("precomputed:correlation_summary")
            if cached:
                return cached
        except Exception:
            pass

    from storage.database import get_session
    from sqlalchemy import text
    try:
        async with get_session() as session:
            result = await session.execute(text("""
                SELECT
                    COUNT(*) AS total_signals,
                    COUNT(*) FILTER (WHERE cluster_id IS NOT NULL) AS clustered_signals,
                    COUNT(DISTINCT cluster_id) FILTER (WHERE cluster_id IS NOT NULL) AS cluster_count
                FROM signals
                WHERE timestamp > NOW() - INTERVAL '24 hours'
            """))
            row = result.mappings().first()
            if row:
                total = int(row["total_signals"] or 0)
                clustered = int(row["clustered_signals"] or 0)
                cluster_count = int(row["cluster_count"] or 0)
                avg = round(clustered / cluster_count, 2) if cluster_count > 0 else 0.0
                return {
                    "total_signals": total,
                    "clustered_signals": clustered,
                    "cluster_count": cluster_count,
                    "avg_cluster_size": avg,
                    "top_clusters": [],
                }
    except Exception as exc:
        logger.error("get_correlation_summary fallback failed: %s", exc)

    return {"total_signals": 0, "clustered_signals": 0, "cluster_count": 0, "avg_cluster_size": 0.0, "top_clusters": []}


@router.get("/confidence-distribution", summary="Signal confidence breakdown", response_model=ConfidenceDistributionResponse)
async def get_confidence_distribution(request: Request):
    from storage.database import get_session
    from sqlalchemy import text

    try:
        async with get_session() as session:
            result = await session.execute(text("""
                SELECT
                    COUNT(*) FILTER (WHERE confidence >= 0.75) AS high,
                    COUNT(*) FILTER (WHERE confidence >= 0.50 AND confidence < 0.75) AS medium,
                    COUNT(*) FILTER (WHERE confidence < 0.50) AS low,
                    COUNT(*) FILTER (WHERE confidence IS NULL) AS unscored,
                    COUNT(*) AS total
                FROM signals
            """))
            row = result.mappings().first()
            if not row:
                return {"high": 0, "medium": 0, "low": 0, "unscored": 0, "total": 0}
            return {
                "high": int(row["high"] or 0),
                "medium": int(row["medium"] or 0),
                "low": int(row["low"] or 0),
                "unscored": int(row["unscored"] or 0),
                "total": int(row["total"] or 0),
            }
    except Exception as exc:
        logger.error("get_confidence_distribution failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{signal_id}", summary="Single signal detail")
async def get_signal(signal_id: str, request: Request):
    """Fetch a single signal by ID."""
    from storage.signal_repo import SignalRepository
    from storage.database import EventModel, get_session
    from sqlalchemy import select

    repo = SignalRepository()
    try:
        sig = await repo.get_signal(signal_id)
        if not sig:
            raise HTTPException(status_code=404, detail=f"Signal {signal_id} not found")

        linked_event = None
        cluster_peers = []
        async with get_session() as session:
            source_event_id = sig.get("source_event_id")
            if source_event_id:
                event_row = (
                    await session.execute(
                        select(EventModel).where(EventModel.event_id == source_event_id)
                    )
                ).scalar_one_or_none()
                if event_row:
                    linked_event = {
                        "event_id": event_row.event_id,
                        "title": event_row.title,
                        "source": event_row.source,
                        "event_type": event_row.event_type,
                        "timestamp": event_row.timestamp.isoformat() if event_row.timestamp else None,
                        "risk_score": event_row.risk_score,
                        "confidence_score": event_row.confidence_score,
                    }

            cluster_id = sig.get("cluster_id")
            if cluster_id:
                result = await session.execute(text("""
                    SELECT signal_id, source_event_id, source, signal_type, title, body,
                           timestamp, actors, location_name, location_lat, location_lon,
                           sentiment_score, confidence, cluster_id, meta
                    FROM signals
                    WHERE cluster_id = :cluster_id AND signal_id <> :signal_id
                    ORDER BY timestamp DESC NULLS LAST
                    LIMIT 5
                """), {"cluster_id": cluster_id, "signal_id": signal_id})
                cluster_peers = []
                for row in result.mappings().all():
                    peer = dict(row)
                    if peer.get("timestamp") and hasattr(peer["timestamp"], "isoformat"):
                        peer["timestamp"] = peer["timestamp"].isoformat()
                    cluster_peers.append(peer)

        meta = sig.get("meta") or {}
        evidence = {
            "actor_count": len(sig.get("actors") or []),
            "has_location": bool(sig.get("location_name") or sig.get("location_lat")),
            "cluster_peer_count": len(cluster_peers),
            "theme_count": len(meta.get("tags") or []),
        }

        return {
            **sig,
            "linked_event": linked_event,
            "cluster_peers": cluster_peers,
            "evidence": evidence,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("get_signal failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
