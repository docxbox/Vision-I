"""
storage/signal_repo.py
───────────────────────
Signal persistence and pgvector semantic search.

Provides CRUD operations for the signals table plus vector similarity
queries used by the correlation engine and semantic search API.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.utils import to_iso
from storage.database import SignalModel, get_session

logger = logging.getLogger("vision_i.storage.signal_repo")


def _parse_dt(value: Any) -> Optional[datetime]:
    """Coerce timestamp inputs into database-friendly datetimes."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(to_iso(value).replace("Z", "+00:00"))
    except Exception:
        return None


class SignalRepository:
    """Signal CRUD + pgvector similarity search."""

    async def upsert_signals(self, signals: List[Dict[str, Any]]) -> int:
        """
        Bulk upsert signals.  Skips duplicates by signal_id.
        Returns the number of signals inserted.
        """
        if not signals:
            return 0

        inserted = 0
        async with get_session() as session:
            for sig in signals:
                stmt = pg_insert(SignalModel).values(
                    signal_id       = sig["signal_id"],
                    source_event_id = sig["source_event_id"],
                    source          = sig["source"],
                    signal_type     = sig.get("signal_type", "raw"),
                    title           = sig["title"],
                    body            = sig.get("body"),
                    content_hash    = sig.get("content_hash"),
                    embedding       = sig.get("embedding"),
                    timestamp       = _parse_dt(sig.get("timestamp")),
                    actors          = sig.get("actors", []),
                    location_name   = sig.get("location_name"),
                    location_lat    = sig.get("location_lat"),
                    location_lon    = sig.get("location_lon"),
                    sentiment_score = sig.get("sentiment_score"),
                    confidence      = sig.get("confidence", 0.5),
                    cluster_id      = sig.get("cluster_id"),
                    meta            = sig.get("meta", {}),
                ).on_conflict_do_nothing(index_elements=["signal_id"])
                result = await session.execute(stmt)
                if result.rowcount:
                    inserted += result.rowcount

        logger.info("Upserted signals: %d inserted (of %d)", inserted, len(signals))
        return inserted

    async def find_similar(
        self,
        embedding: List[float],
        threshold: float = 0.75,
        time_start: Optional[datetime] = None,
        time_end: Optional[datetime] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        Semantic similarity search using pgvector cosine distance.

        Returns signals with cosine similarity >= threshold, ordered by similarity desc.
        """
        async with get_session() as session:
            # pgvector <=> is cosine distance (0 = identical, 2 = opposite)
            # similarity = 1 - distance
            vec_str = f"[{','.join(str(v) for v in embedding)}]"

            where_clauses = [f"1 - (embedding <=> '{vec_str}'::vector) >= {threshold}"]
            if time_start:
                where_clauses.append(f"timestamp >= '{time_start.isoformat()}'")
            if time_end:
                where_clauses.append(f"timestamp <= '{time_end.isoformat()}'")

            where_sql = " AND ".join(where_clauses)
            query = text(f"""
                SELECT signal_id, source_event_id, source, signal_type,
                       title, body, timestamp, actors, location_name,
                       location_lat, location_lon, sentiment_score,
                       confidence, cluster_id, meta,
                       1 - (embedding <=> '{vec_str}'::vector) AS similarity
                FROM signals
                WHERE embedding IS NOT NULL AND {where_sql}
                ORDER BY embedding <=> '{vec_str}'::vector
                LIMIT :limit
            """)
            result = await session.execute(query, {"limit": limit})
            rows = result.mappings().all()
            return [dict(r) for r in rows]

    async def find_similar_text(
        self,
        query: str,
        threshold: float = 0.0,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        Fallback search when embeddings are unavailable.

        This is NOT semantic similarity. It performs a simple ILIKE match on title/body
        and returns recent rows. The `threshold` parameter is accepted for API parity
        but is not used in this mode.
        """
        q = (query or "").strip()
        if not q:
            return []

        async with get_session() as session:
            result = await session.execute(text("""
                SELECT signal_id, source_event_id, source, signal_type,
                       title, body, timestamp, actors, location_name,
                       location_lat, location_lon, sentiment_score,
                       confidence, cluster_id, meta
                FROM signals
                WHERE (title ILIKE :q OR body ILIKE :q)
                ORDER BY timestamp DESC NULLS LAST
                LIMIT :limit
            """), {"q": f"%{q}%", "limit": limit})
            rows = result.mappings().all()
            return [dict(r) for r in rows]

    async def find_by_actors(
        self,
        actor_names: List[str],
        window_hours: int = 6,
    ) -> List[Dict[str, Any]]:
        """Find signals mentioning any of the given actors within the time window."""
        if not actor_names:
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        async with get_session() as session:
            # JSONB array overlap: actors ?| array[...]
            names_sql = ",".join(f"'{n}'" for n in actor_names)
            query = text(f"""
                SELECT signal_id, source_event_id, source, title, timestamp,
                       actors, confidence, cluster_id, sentiment_score,
                       location_name, location_lat, location_lon, meta
                FROM signals
                WHERE timestamp >= :cutoff
                  AND actors ?| array[{names_sql}]
                ORDER BY timestamp DESC
                LIMIT 200
            """)
            result = await session.execute(query, {"cutoff": cutoff})
            return [dict(r) for r in result.mappings().all()]

    async def find_unclustered(self, window_hours: int = 6) -> List[Dict[str, Any]]:
        """Fetch signals with no cluster assignment in the time window."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        async with get_session() as session:
            query = text("""
                SELECT signal_id, source_event_id, source, title, body,
                       timestamp, actors, confidence, sentiment_score,
                       location_name, location_lat, location_lon,
                       embedding, meta
                FROM signals
                WHERE cluster_id IS NULL
                  AND embedding IS NOT NULL
                  AND timestamp >= :cutoff
                ORDER BY timestamp DESC
                LIMIT 500
            """)
            result = await session.execute(query, {"cutoff": cutoff})
            return [dict(r) for r in result.mappings().all()]

    async def update_cluster(
        self, signal_ids: List[str], cluster_id: str
    ) -> int:
        """Assign a cluster_id to a batch of signals."""
        if not signal_ids:
            return 0
        async with get_session() as session:
            stmt = (
                update(SignalModel)
                .where(SignalModel.signal_id.in_(signal_ids))
                .values(cluster_id=cluster_id, signal_type="correlated")
            )
            result = await session.execute(stmt)
            return result.rowcount

    async def get_signal(self, signal_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single signal by ID."""
        async with get_session() as session:
            stmt = select(SignalModel).where(SignalModel.signal_id == signal_id)
            row = (await session.execute(stmt)).scalar_one_or_none()
            if not row:
                return None
            return {
                "signal_id":       row.signal_id,
                "source_event_id": row.source_event_id,
                "source":          row.source,
                "signal_type":     row.signal_type,
                "title":           row.title,
                "body":            row.body,
                "content_hash":    row.content_hash,
                "timestamp":       row.timestamp.isoformat() if row.timestamp else None,
                "actors":          row.actors,
                "location_name":   row.location_name,
                "location_lat":    row.location_lat,
                "location_lon":    row.location_lon,
                "sentiment_score": row.sentiment_score,
                "confidence":      row.confidence,
                "cluster_id":      row.cluster_id,
                "meta":            row.meta,
            }

    async def count_signals(self, window_hours: int = 24) -> Dict[str, Any]:
        """Quick aggregate counts for the admin/stats endpoint."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        async with get_session() as session:
            result = await session.execute(text("""
                SELECT COUNT(*) AS total,
                       COUNT(CASE WHEN cluster_id IS NOT NULL THEN 1 END) AS clustered,
                       COUNT(DISTINCT cluster_id) FILTER (WHERE cluster_id IS NOT NULL) AS cluster_count,
                       COUNT(DISTINCT source) AS source_count
                FROM signals
                WHERE timestamp >= :cutoff
            """), {"cutoff": cutoff})
            row = result.mappings().first()
            return dict(row) if row else {"total": 0, "clustered": 0, "cluster_count": 0, "source_count": 0}
