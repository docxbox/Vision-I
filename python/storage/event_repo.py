"""
storage/event_repo.py
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
All PostgreSQL read/write operations for events.

The repository pattern keeps SQL out of routers and the NLP pipeline.
Every database interaction for events goes through this class.

Usage:
    repo   = EventRepository(session)
    events = await repo.list_events(source="usgs", limit=50)
    await  repo.upsert_many(vision_events)
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import String, and_, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.schema import VisionEvent
from core.geo import resolve_event_country
from core.entity_normalizer import normalize_actor_payloads, repair_text, sanitize_event_text
from storage.database import EventModel, IngestJobModel, TrackedQueryModel

logger = logging.getLogger("vision_i.storage.event_repo")


def _query_terms(query: Optional[str]) -> List[str]:
    """Split simple analyst query syntax into searchable terms.

    Workspace queries often arrive as "Iran OR Hormuz". The old implementation
    searched that literal phrase, which made social/workspace timelines look
    empty even when rows matched one of the individual terms.
    """
    if not query:
        return []
    raw_terms = re.split(r"\s+\bOR\b\s+|[;,|]", query, flags=re.IGNORECASE)
    terms = []
    seen = set()
    for term in raw_terms:
        clean = term.strip().strip("()[]{}\"'")
        clean = re.sub(r"\s+", " ", clean)
        key = clean.lower()
        if clean and key not in seen:
            terms.append(clean)
            seen.add(key)
    return terms


def _text_match_condition(query: Optional[str]):
    terms = _query_terms(query)
    if not terms:
        return None
    return or_(*[
        EventModel.title.ilike(f"%{term}%")
        | EventModel.body.ilike(f"%{term}%")
        | EventModel.description.ilike(f"%{term}%")
        | EventModel.source.ilike(f"%{term}%")
        | EventModel.event_type.ilike(f"%{term}%")
        | EventModel.author.ilike(f"%{term}%")
        | EventModel.location_name.ilike(f"%{term}%")
        | EventModel.actors.cast(String).ilike(f"%{term}%")
        | EventModel.tags.cast(String).ilike(f"%{term}%")
        | EventModel.extras.cast(String).ilike(f"%{term}%")
        for term in terms
    ])


def _parse_dt(iso_str: Optional[str]) -> Optional[datetime]:
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _event_to_row(e: VisionEvent) -> Dict[str, Any]:
    """Convert a VisionEvent dict to a flat dict matching EventModel columns."""
    e = sanitize_event_text(dict(e))
    sentiment = e.get("sentiment") or {}
    location  = e.get("location")  or {}
    country = resolve_event_country(e)
    extras = dict(e.get("extras") or {})
    if country and not extras.get("country"):
        extras["country"] = country
    return {
        "event_id":        e["event_id"],
        "source":          e["source"],
        "source_id":       e.get("source_id"),
        "event_type":      e.get("event_type", "news"),
        "title":           e.get("title", ""),
        "description":     e.get("description"),
        "body":            e.get("body"),
        "url":             e.get("url"),
        "language":        e.get("language", "en"),
        "author":          e.get("author"),
        "timestamp":       _parse_dt(e.get("timestamp")),
        "ingest_time":     _parse_dt(e.get("ingest_time")),
        "sentiment_label": sentiment.get("label"),
        "sentiment_score": sentiment.get("score"),
        "location_lat":    location.get("lat"),
        "location_lon":    location.get("lon"),
        "location_name":   location.get("name"),
        "actors":          normalize_actor_payloads(e.get("actors") or []),
        "tags":            e.get("tags")   or [],
        "extras":          {**extras, "provenance_id": e.get("provenance_id")},
    }


def _row_to_event(row: EventModel) -> Dict[str, Any]:
    """Convert an ORM row back to an API-ready dict."""
    extras = row.extras or {}
    country = extras.get("country")
    actors = normalize_actor_payloads(row.actors or [])
    payload = {
        "event_id":    row.event_id,
        "source":      row.source,
        "source_id":   row.source_id,
        "event_type":  row.event_type,
        "title":       repair_text(row.title),
        "description": repair_text(row.description),
        "body":        repair_text(row.body),
        "url":         row.url,
        "language":    row.language,
        "author":      repair_text(row.author),
        "timestamp":   row.timestamp.isoformat() + "Z" if row.timestamp else None,
        "ingest_time": row.ingest_time.isoformat() + "Z" if row.ingest_time else None,
        "actors":      actors,
        "location": {
            "lat":  row.location_lat,
            "lon":  row.location_lon,
            "name": repair_text(row.location_name),
            "country": repair_text(country),
        } if (row.location_lat or row.location_name) else None,
        "sentiment": {
            "label": row.sentiment_label,
            "score": row.sentiment_score,
        } if row.sentiment_label else None,
        "tags":   [repair_text(tag) if isinstance(tag, str) else tag for tag in (row.tags or [])],
        "extras": {k: (repair_text(v) if isinstance(v, str) else v) for k, v in extras.items()},
        "confidence_score": row.confidence_score,
        "influence_score": row.influence_score,
        "risk_score": row.risk_score,
        "supporting_signals": row.supporting_signals or [],
        "signal_count": row.signal_count or 0,
        "reasoning": row.reasoning,
        "provenance_id": (row.extras or {}).get("provenance_id"),
    }
    return sanitize_event_text(payload)


class EventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_many(self, events: List[VisionEvent]) -> int:
        """
        Insert-or-update events by event_id.
        On conflict: updates all fields except event_id and ingest_time.
        Returns number of rows affected.
        """
        if not events:
            return 0

        rows = [_event_to_row(e) for e in events]

        stmt = pg_insert(EventModel).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["event_id"],
            set_={
                col: stmt.excluded[col]
                for col in rows[0].keys()
                if col not in ("event_id", "ingest_time")
            },
        )

        result = await self._session.execute(stmt)
        logger.debug("Upserted %d events", result.rowcount)
        return result.rowcount

    async def upsert_one(self, event: VisionEvent) -> None:
        await self.upsert_many([event])

    async def list_events(
        self,
        source:      Optional[str] = None,
        event_type:  Optional[str] = None,
        query:       Optional[str] = None,
        sentiment:   Optional[str] = None,
        from_time:   Optional[str] = None,
        to_time:     Optional[str] = None,
        limit:       int = 50,
        offset:      int = 0,
        sort_by:     str = "latest",
        with_total:  bool = True,
    ) -> Tuple[int, List[Dict]]:
        """Returns (total_count, page_of_events). Pass with_total=False to skip the
        COUNT scan (a second full ILIKE pass) when the caller only needs the rows."""
        conditions = []
        if source:
            conditions.append(EventModel.source.ilike(f"{source}%"))
        if event_type:
            conditions.append(EventModel.event_type == event_type)
        if sentiment:
            conditions.append(func.upper(EventModel.sentiment_label) == sentiment.upper())
        if from_time:
            conditions.append(EventModel.timestamp >= _parse_dt(from_time))
        if to_time:
            conditions.append(EventModel.timestamp <= _parse_dt(to_time))
        if query:
            match = _text_match_condition(query)
            if match is not None:
                conditions.append(match)

        where = and_(*conditions) if conditions else True

        if sort_by in {"risk", "risk_score"}:
            ordering = [
                func.coalesce(EventModel.risk_score, 0).desc(),
                func.coalesce(EventModel.signal_count, 0).desc(),
                EventModel.timestamp.desc().nullslast(),
                EventModel.ingest_time.desc().nullslast(),
            ]
        elif sort_by in {"ingest", "ingest_time"}:
            ordering = [
                EventModel.ingest_time.desc().nullslast(),
                EventModel.timestamp.desc().nullslast(),
            ]
        else:
            ordering = [
                EventModel.timestamp.desc().nullslast(),
                EventModel.ingest_time.desc().nullslast(),
            ]

        rows_q   = (
            select(EventModel)
            .where(where)
            .order_by(*ordering)
            .limit(limit)
            .offset(offset)
        )
        rows     = (await self._session.execute(rows_q)).scalars().all()
        total = len(rows) if offset == 0 and len(rows) < limit else offset + len(rows)

        if not with_total:
            return total, [_row_to_event(r) for r in rows]

        try:
            if conditions:
                count_q = select(func.count()).select_from(EventModel).where(where)
                total = (await self._session.execute(count_q)).scalar_one()
            else:
                estimate_q = text("""
                    SELECT COALESCE(
                        (SELECT reltuples::bigint
                         FROM pg_class
                         WHERE oid = 'events'::regclass),
                        0
                    )
                """)
                estimated_total = (await self._session.execute(estimate_q)).scalar_one()
                total = max(int(estimated_total), total)
        except Exception as exc:
            logger.warning("Event count fallback in use: %s", exc)

        return total, [_row_to_event(r) for r in rows]

    async def list_source_facets(
        self,
        query: Optional[str] = None,
        event_type: Optional[str] = None,
        sentiment: Optional[str] = None,
        from_time: Optional[str] = None,
        to_time: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        conditions = []
        if event_type:
            conditions.append(EventModel.event_type == event_type)
        if sentiment:
            conditions.append(func.upper(EventModel.sentiment_label) == sentiment.upper())
        if from_time:
            conditions.append(EventModel.timestamp >= _parse_dt(from_time))
        if to_time:
            conditions.append(EventModel.timestamp <= _parse_dt(to_time))
        if query:
            match = _text_match_condition(query)
            if match is not None:
                conditions.append(match)

        where = and_(*conditions) if conditions else True
        rows = (
            await self._session.execute(
                select(
                    EventModel.source.label("source"),
                    func.count().label("count"),
                    func.max(EventModel.timestamp).label("latest"),
                )
                .where(where)
                .group_by(EventModel.source)
                .order_by(func.count().desc(), EventModel.source.asc())
                .limit(limit)
            )
        ).fetchall()
        return [
            {
                "source": row.source,
                "count": int(row.count or 0),
                "latest": row.latest.isoformat() + "Z" if row.latest else None,
            }
            for row in rows
        ]

    async def list_feed_events(
        self,
        source: Optional[str] = None,
        query: Optional[str] = None,
        sentiment: Optional[str] = None,
        from_time: Optional[str] = None,
        to_time: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        sort: str = "latest",
        include_tracking: bool = False,
    ) -> Tuple[int, List[Dict]]:
        """
        Returns feed-worthy intelligence items, excluding raw tracking churn.

        The shared `events` table currently mixes intelligence events with
        high-volume telemetry updates (AIS/OpenSky). This feed query keeps the
        canonical table but projects a more analyst-friendly slice by excluding
        raw tracking updates while preserving tracking anomalies.
        """
        conditions = []
        tracking_gate = or_(
            EventModel.event_type.ilike("%anomaly%"),
            EventModel.url.isnot(None),
            func.coalesce(EventModel.risk_score, 0) > 0,
            func.coalesce(EventModel.signal_count, 0) > 0,
        )
        source_lower = (source or "").lower()
        if source_lower in self._TRACKING_SOURCES:
            conditions.append(tracking_gate)
        elif not include_tracking:
            conditions.append(EventModel.source.not_in(list(self._TRACKING_SOURCES)))
        else:
            conditions.append(
                or_(
                    EventModel.source.not_in(list(self._TRACKING_SOURCES)),
                    tracking_gate,
                )
            )
        if source:
            conditions.append(EventModel.source.ilike(f"{source}%"))
        if sentiment:
            conditions.append(func.upper(EventModel.sentiment_label) == sentiment.upper())
        if from_time:
            conditions.append(EventModel.timestamp >= _parse_dt(from_time))
        if to_time:
            conditions.append(EventModel.timestamp <= _parse_dt(to_time))
        if query:
            match = _text_match_condition(query)
            if match is not None:
                conditions.append(match)

        where = and_(*conditions)
        if sort == "priority":
            ordering = [
                func.coalesce(EventModel.risk_score, 0).desc(),
                func.coalesce(EventModel.signal_count, 0).desc(),
                func.coalesce(EventModel.influence_score, 0).desc(),
                EventModel.ingest_time.desc().nullslast(),
                EventModel.timestamp.desc().nullslast(),
            ]
        else:
            ordering = [
                EventModel.ingest_time.desc().nullslast(),
                EventModel.timestamp.desc().nullslast(),
                func.coalesce(EventModel.risk_score, 0).desc(),
            ]

        rows_q = (
            select(EventModel)
            .where(where)
            .order_by(*ordering)
            .limit(limit)
            .offset(offset)
        )
        rows = (await self._session.execute(rows_q)).scalars().all()

        count_q = select(func.count()).select_from(EventModel).where(where)
        total = (await self._session.execute(count_q)).scalar_one()
        return total, [_row_to_event(r) for r in rows]

    async def get_event(self, event_id: str) -> Optional[Dict]:
        row = (
            await self._session.execute(
                select(EventModel).where(EventModel.event_id == event_id)
            )
        ).scalar_one_or_none()
        return _row_to_event(row) if row else None

    async def get_events_by_ids(self, event_ids: List[str]) -> List[Dict]:
        ids = [eid for eid in event_ids if eid]
        if not ids:
            return []
        rows = (
            await self._session.execute(
                select(EventModel).where(EventModel.event_id.in_(ids))
            )
        ).scalars().all()
        by_id = {row.event_id: _row_to_event(row) for row in rows}
        return [by_id[eid] for eid in ids if eid in by_id]

    # Live-tracking sources that can dominate the result set if uncapped
    _TRACKING_SOURCES = ("ais", "opensky")
    # Per-source cap for live tracking to leave room for intelligence events
    _TRACKING_CAP = 150
    # Cap for all non-tracking (intelligence) events
    _INTEL_CAP = 350

    async def get_map_events(
        self,
        source:     Optional[str] = None,
        event_type: Optional[str] = None,
        from_time:  Optional[str] = None,
        to_time:    Optional[str] = None,
        limit:      int = 500,
    ) -> List[Dict]:
        """
        Returns events for map display with balanced per-source representation.

        Live-tracking sources (AIS, OpenSky) are capped at _TRACKING_CAP each so
        they cannot crowd out intelligence events (GDELT, RSS, NewsAPI, etc.).
        Events without stored lat/lon get on-the-fly geocoding in _geojson().
        """
        from sqlalchemy import or_
        # Accept events with explicit coords OR any geocodable metadata
        geocodable = or_(
            EventModel.location_lat.isnot(None),
            EventModel.location_name.isnot(None),
            EventModel.actors.isnot(None),
            EventModel.extras["sourcecountry"].isnot(None),
            EventModel.extras["feed_region"].isnot(None),
        )

        # Build shared filter conditions (excluding the source filter which is
        # applied differently for tracking vs intel queries)
        shared: list = [geocodable]
        if event_type: shared.append(EventModel.event_type == event_type)
        if from_time:  shared.append(EventModel.timestamp >= _parse_dt(from_time))
        if to_time:    shared.append(EventModel.timestamp <= _parse_dt(to_time))

        all_rows: list = []

        if source:
            # Caller explicitly filtered by source â€” single query, honour limit
            rows = (
                await self._session.execute(
                    select(EventModel)
                    .where(and_(*shared, EventModel.source.ilike(f"{source}%")))
                    .order_by(EventModel.timestamp.desc().nullslast())
                    .limit(limit)
                )
            ).scalars().all()
            all_rows = list(rows)
        else:
            # Two-pronged fetch: tracking sources + everything else
            tracking_cap = min(self._TRACKING_CAP, limit // 2)
            intel_cap    = min(self._INTEL_CAP, limit)

            for src in self._TRACKING_SOURCES:
                rows = (
                    await self._session.execute(
                        select(EventModel)
                        .where(and_(*shared, EventModel.source == src))
                        .order_by(EventModel.timestamp.desc().nullslast())
                        .limit(tracking_cap)
                    )
                ).scalars().all()
                all_rows.extend(rows)

            intel_rows = (
                await self._session.execute(
                    select(EventModel)
                    .where(and_(*shared,
                                EventModel.source.not_in(list(self._TRACKING_SOURCES))))
                    .order_by(EventModel.timestamp.desc().nullslast())
                    .limit(intel_cap)
                )
            ).scalars().all()
            all_rows.extend(intel_rows)

        return [_row_to_event(r) for r in all_rows]

    async def get_sentiment_timeline(
        self,
        query:     Optional[str] = None,
        source:    Optional[str] = None,
        from_time: Optional[str] = None,
        to_time:   Optional[str] = None,
        bucket:    str = "day",   # "hour" | "day" | "week"
    ) -> List[Dict]:
        """
        Returns aggregated sentiment scores bucketed by time.
        Used by GET /sentiment/timeline.
        """
        trunc_map = {"hour": "hour", "day": "day", "week": "week"}
        trunc     = trunc_map.get(bucket, "day")

        # Build dynamic WHERE clause
        where_clauses = ["sentiment_score IS NOT NULL"]
        params = {"trunc": trunc}

        if source:
            where_clauses.append("source = :source")
            params["source"] = source
        if from_time:
            where_clauses.append("timestamp >= :from_time")
            params["from_time"] = _parse_dt(from_time)
        if to_time:
            where_clauses.append("timestamp <= :to_time")
            params["to_time"] = _parse_dt(to_time)
        if query:
            term_clauses = []
            for idx, term in enumerate(_query_terms(query)):
                key = f"query_{idx}"
                term_clauses.append(
                    f"(title ILIKE :{key} OR body ILIKE :{key} OR description ILIKE :{key})"
                )
                params[key] = f"%{term}%"
            if term_clauses:
                where_clauses.append("(" + " OR ".join(term_clauses) + ")")

        where_str = " AND ".join(where_clauses)

        sql = text(f"""
            SELECT
                date_trunc(:trunc, timestamp)         AS bucket,
                AVG(sentiment_score)                  AS avg_score,
                COUNT(*)                              AS event_count,
                SUM(CASE WHEN sentiment_label = 'POSITIVE' THEN 1 ELSE 0 END) AS positive,
                SUM(CASE WHEN sentiment_label = 'NEUTRAL'  THEN 1 ELSE 0 END) AS neutral,
                SUM(CASE WHEN sentiment_label = 'NEGATIVE' THEN 1 ELSE 0 END) AS negative
            FROM events
            WHERE {where_str}
            GROUP BY bucket
            ORDER BY bucket ASC
        """)

        rows = (await self._session.execute(sql, params)).fetchall()

        return [
            {
                "bucket":      row.bucket.isoformat() + "Z" if row.bucket else None,
                "avg_score":   round(float(row.avg_score), 4),
                "event_count": row.event_count,
                "positive":    row.positive,
                "neutral":     row.neutral,
                "negative":    row.negative,
            }
            for row in rows
        ]

    async def get_active_queries(self) -> List[str]:
        rows = (
            await self._session.execute(
                select(TrackedQueryModel.query)
                .where(TrackedQueryModel.is_active == True)  # noqa: E712
            )
        ).scalars().all()
        return list(rows)

    async def save_job(self, job: Dict) -> None:
        row = IngestJobModel(
            job_id        = job["job_id"],
            query         = job.get("query"),
            status        = job.get("status", "done"),
            started_at    = _parse_dt(job.get("started_at")),
            finished_at   = _parse_dt(job.get("finished_at")),
            total_events  = job.get("total_events"),
            source_counts = job.get("source_counts"),
            source_errors = job.get("source_errors"),
            error         = job.get("error"),
        )
        self._session.add(row)
        await self._session.commit()

