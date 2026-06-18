"""
intelligence/overview_builder.py
──────────────────────────────────
Builds a high-level system state snapshot from the database.

Used by GET /overview to populate the frontend dashboard.
All queries use raw SQL via session.execute(text(...)) for performance.
"""

import logging
from typing import Any, Dict, List

from sqlalchemy import text

logger = logging.getLogger("vision_i.intelligence.overview_builder")


class OverviewBuilder:
    """Aggregate system-state metrics into a single dict."""

    async def build(self, session, window_hours: int = 24) -> Dict[str, Any]:
        """
        Query the database and return the overview payload.

        Parameters
        ----------
        session      : SQLAlchemy async session (already open).
        window_hours : Look-back window for top_events / recent activity.

        Returns
        -------
        dict with keys:
            total_events, alert_count, narrative_count, asset_count,
            top_events, active_alerts, source_health,
            generated_at, window_hours
        """
        from core.utils import utcnow_iso

        total_events    = await self._count_events(session)
        alert_count     = await self._count_alerts(session)
        narrative_count = await self._count_narratives(session)
        asset_count     = await self._count_assets(session)
        top_events      = await self._top_events(session, window_hours)
        active_alerts   = await self._active_alerts(session)
        source_health   = await self._source_health(session)

        return {
            "total_events":    total_events,
            "alert_count":     alert_count,
            "narrative_count": narrative_count,
            "asset_count":     asset_count,
            "top_events":      top_events,
            "active_alerts":   active_alerts,
            "source_health":   source_health,
            "generated_at":    utcnow_iso(),
            "window_hours":    window_hours,
        }

    # ── private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _int_from_row(row, key: str) -> int:
        if not row:
            return 0
        try:
            return int(row[key] or 0)
        except Exception:
            try:
                return int(row._mapping[key] or 0)
            except Exception:
                return 0

    async def _count_events(self, session) -> int:
        try:
            return int((await session.execute(
                text("SELECT COUNT(*) FROM events")
            )).scalar() or 0)
        except Exception as exc:
            logger.debug("_count_events failed: %s", exc)
            return 0

    async def _count_alerts(self, session) -> int:
        """Count unacknowledged, non-dismissed alerts."""
        try:
            return int((await session.execute(
                text("""
                    SELECT COUNT(*)
                    FROM   alerts
                    WHERE  acknowledged = FALSE
                      AND  dismissed    = FALSE
                """)
            )).scalar() or 0)
        except Exception as exc:
            logger.debug("_count_alerts failed: %s", exc)
            return 0

    async def _count_narratives(self, session) -> int:
        try:
            return int((await session.execute(
                text("SELECT COUNT(*) FROM narratives WHERE status = 'active'")
            )).scalar() or 0)
        except Exception as exc:
            logger.debug("_count_narratives failed: %s", exc)
            return 0

    async def _count_assets(self, session) -> int:
        try:
            return int((await session.execute(
                text("SELECT COUNT(*) FROM assets")
            )).scalar() or 0)
        except Exception as exc:
            logger.debug("_count_assets failed: %s", exc)
            return 0

    async def _top_events(self, session, window_hours: int) -> List[Dict[str, Any]]:
        """Top 10 events by risk_score within the window, falling back to all-time."""
        try:
            result = await session.execute(
                text("""
                    SELECT
                        event_id,
                        title,
                        source,
                        event_type,
                        COALESCE(risk_score, 0)        AS risk_score,
                        timestamp,
                        location_lat                   AS lat,
                        location_lon                   AS lon,
                        sentiment_label
                    FROM events
                    WHERE timestamp >= NOW() - make_interval(hours => :window_hours)
                    ORDER BY risk_score DESC NULLS LAST
                    LIMIT 10
                """),
                {"window_hours": int(window_hours)}
            )
            rows = result.mappings().all()
            # Fall back to all-time top events if the window returned nothing
            if not rows:
                result = await session.execute(
                    text("""
                        SELECT
                            event_id,
                            title,
                            source,
                            event_type,
                            COALESCE(risk_score, 0) AS risk_score,
                            timestamp,
                            location_lat            AS lat,
                            location_lon            AS lon,
                            sentiment_label
                        FROM events
                        ORDER BY risk_score DESC NULLS LAST
                        LIMIT 10
                    """)
                )
                rows = result.mappings().all()
            return [_row_to_dict(r) for r in rows]
        except Exception as exc:
            logger.debug("_top_events failed: %s", exc)
            return []

    async def _active_alerts(self, session) -> List[Dict[str, Any]]:
        """Up to 10 most recent unacknowledged, non-dismissed alerts."""
        try:
            result = await session.execute(
                text("""
                    SELECT
                        alert_id,
                        title,
                        severity,
                        entity,
                        acknowledged,
                        escalated,
                        dismissed,
                        detected_at
                    FROM alerts
                    WHERE acknowledged = FALSE
                      AND dismissed    = FALSE
                    ORDER BY detected_at DESC
                    LIMIT 10
                """)
            )
            rows = result.mappings().all()
            return [_row_to_dict(r) for r in rows]
        except Exception as exc:
            logger.debug("_active_alerts failed: %s", exc)
            return []

    async def _source_health(self, session) -> List[Dict[str, Any]]:
        """
        Pull per-source health from source_checkpoints.

        We COALESCE away columns that may not exist in older schema
        by falling back to the information_schema approach.
        """
        try:
            # Try the full query first (works if error_count column exists)
            result = await session.execute(
                text("""
                    SELECT
                        source                          AS source_name,
                        last_run_at                     AS last_checked,
                        events_fetched                  AS record_count,
                        COALESCE(
                            (meta->>'error_count')::INT, 0
                        )                               AS error_count,
                        credibility_score
                    FROM source_checkpoints
                    ORDER BY last_run_at DESC NULLS LAST
                """)
            )
            rows = result.mappings().all()
            return [
                {
                    "source_name":      r["source_name"],
                    "last_checked":     r["last_checked"].isoformat() if r["last_checked"] else None,
                    "record_count":     r["record_count"] or 0,
                    "error_count":      r["error_count"] or 0,
                    "credibility_score": r["credibility_score"],
                    "status": (
                        "ok" if (r["error_count"] or 0) == 0 else
                        "degraded" if (r["error_count"] or 0) < 5 else
                        "error"
                    ),
                }
                for r in rows
            ]
        except Exception as exc:
            logger.debug("_source_health (full) failed: %s — trying minimal query", exc)
            try:
                result = await session.execute(
                    text("""
                        SELECT
                            source       AS source_name,
                            last_run_at  AS last_checked,
                            events_fetched AS record_count
                        FROM source_checkpoints
                        ORDER BY last_run_at DESC NULLS LAST
                    """)
                )
                rows = result.mappings().all()
                return [
                    {
                        "source_name":   r["source_name"],
                        "last_checked":  r["last_checked"].isoformat() if r["last_checked"] else None,
                        "record_count":  r["record_count"] or 0,
                        "error_count":   0,
                        "status":        "ok",
                        "credibility_score": None,
                    }
                    for r in rows
                ]
            except Exception as exc2:
                logger.debug("_source_health (minimal) also failed: %s", exc2)
                return []


def _row_to_dict(row) -> Dict[str, Any]:
    """Convert a SQLAlchemy RowMapping to a plain dict, serialising datetimes."""
    out = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out
