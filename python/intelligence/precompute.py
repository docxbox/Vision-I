"""
intelligence/precompute.py
--------------------------
Writes precomputed intelligence artifacts to Redis.

Called after each intelligence cycle (narrative detection, anomaly scan)
to populate Redis keys that the serving layer reads directly - ensuring
<200ms API latency for all intelligence endpoints.

Redis key convention:
  precomputed:dashboard_summary    - event/alert/narrative counts
  precomputed:threat_level         - overall threat assessment
  precomputed:live_streams         - latest live events (written by live_ingest_job)
  precomputed:influence_network    - actor graph nodes/edges
  precomputed:trend_lines          - per-source hourly event volumes
  precomputed:sentiment_summary    - avg sentiment per source
  precomputed:narratives_summary   - counts by type/severity
  precomputed:alerts_summary       - unacknowledged by severity
"""

import logging
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.event_bus import EventBus
    from storage.graph import GraphDB

logger = logging.getLogger("vision_i.intelligence.precompute")


class IntelligencePrecomputer:
    """Precomputes intelligence artifacts into Redis after each cycle."""

    def __init__(self, event_bus: "EventBus", graph: "GraphDB") -> None:
        self._bus = event_bus
        self._graph = graph

    async def precompute_all(self) -> dict:
        """Run all precomputation steps. Returns summary of what was written."""
        results = {}
        results["dashboard"] = await self._precompute_dashboard_summary()
        results["threat"] = await self._precompute_threat_level()
        results["trends"] = await self._precompute_trend_lines()
        results["sentiment"] = await self._precompute_sentiment_summary()
        results["country_sentiment"] = await self._precompute_country_sentiment()
        results["correlation"] = await self._precompute_correlation_summary()
        results["confidence"] = await self._precompute_confidence_distribution()
        results["escalation"] = await self._precompute_escalation_scores()
        results["communities"] = await self._precompute_community_graph()
        results["unrest_watch"] = await self._precompute_unrest_watch()
        return results

    async def _precompute_dashboard_summary(self) -> bool:
        """Aggregate event counts, alert counts, narrative counts."""
        try:
            from storage.database import get_session
            from sqlalchemy import text

            async with get_session() as session:
                # Event counts by source
                result = await session.execute(text(
                    "SELECT source, COUNT(*) as cnt FROM events "
                    "WHERE timestamp > NOW() - INTERVAL '24 hours' "
                    "GROUP BY source ORDER BY cnt DESC"
                ))
                events_by_source = {row[0]: row[1] for row in result}

                # Event counts by type
                result = await session.execute(text(
                    "SELECT event_type, COUNT(*) as cnt FROM events "
                    "GROUP BY event_type ORDER BY cnt DESC"
                ))
                events_by_type = {row[0]: row[1] for row in result}

                # Total events
                result = await session.execute(text("SELECT COUNT(*) FROM events"))
                total_events = result.scalar() or 0

                # Active narratives
                result = await session.execute(text(
                    "SELECT COUNT(*) FROM narratives WHERE status = 'active'"
                ))
                active_narratives = result.scalar() or 0

                # Unacknowledged alerts
                result = await session.execute(text(
                    "SELECT COUNT(*) FROM alerts WHERE acknowledged = false"
                ))
                unacked_alerts = result.scalar() or 0

            summary = {
                "total_events": total_events,
                "events_last_24h": sum(events_by_source.values()),
                "by_source": events_by_source,
                "by_type": events_by_type,
                "active_narratives": active_narratives,
                "unacknowledged_alerts": unacked_alerts,
                "generated_at": __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ).isoformat(),
            }
            await self._bus.cache_set("precomputed:dashboard_summary", summary)
            return True
        except Exception as exc:
            logger.warning("Dashboard precompute failed: %s", exc)
            return False

    async def _precompute_threat_level(self) -> bool:
        """Compute overall threat level from active alerts + narratives."""
        try:
            from storage.database import get_session
            from sqlalchemy import text

            async with get_session() as session:
                # Count critical/high alerts
                result = await session.execute(text(
                    "SELECT severity, COUNT(*) FROM alerts "
                    "WHERE acknowledged = false "
                    "AND detected_at > NOW() - INTERVAL '6 hours' "
                    "GROUP BY severity"
                ))
                alert_counts = {row[0]: row[1] for row in result}

                # Count high-strength narratives
                result = await session.execute(text(
                    "SELECT COUNT(*) FROM narratives "
                    "WHERE status = 'active' AND severity IN ('high', 'critical') "
                    "AND detected_at > NOW() - INTERVAL '6 hours'"
                ))
                critical_narratives = result.scalar() or 0

            critical = alert_counts.get("critical", 0)
            high = alert_counts.get("high", 0)

            if critical >= 2 or (critical >= 1 and critical_narratives >= 2):
                level, score = "CRITICAL", 0.9
            elif high >= 3 or critical >= 1:
                level, score = "HIGH", 0.7
            elif high >= 1 or critical_narratives >= 1:
                level, score = "ELEVATED", 0.5
            else:
                level, score = "LOW", 0.2

            threat = {
                "level": level,
                "score": score,
                "alert_counts": alert_counts,
                "critical_narratives": critical_narratives,
            }
            await self._bus.cache_set("precomputed:threat_level", threat)
            return True
        except Exception as exc:
            logger.warning("Threat level precompute failed: %s", exc)
            return False

    async def _precompute_trend_lines(self) -> bool:
        """Per-source hourly event volumes for the last 24h."""
        try:
            from storage.database import get_session
            from sqlalchemy import text

            async with get_session() as session:
                result = await session.execute(text(
                    "SELECT date_trunc('hour', timestamp) AS hour, source, "
                    "COUNT(*) AS cnt "
                    "FROM events "
                    "WHERE timestamp > NOW() - INTERVAL '24 hours' "
                    "GROUP BY hour, source "
                    "ORDER BY hour"
                ))
                trends = {}
                for row in result:
                    hour_str = row[0].isoformat() if row[0] else ""
                    source = row[1]
                    trends.setdefault(source, []).append({
                        "hour": hour_str,
                        "count": row[2],
                    })

            await self._bus.cache_set("precomputed:trend_lines", trends)
            return True
        except Exception as exc:
            logger.warning("Trend lines precompute failed: %s", exc)
            return False

    async def _precompute_sentiment_summary(self) -> bool:
        """Average sentiment per source for the last 24h."""
        try:
            from storage.database import get_session
            from sqlalchemy import text

            async with get_session() as session:
                result = await session.execute(text(
                    "SELECT source, AVG(sentiment_score) AS avg_score, "
                    "COUNT(*) AS cnt "
                    "FROM events "
                    "WHERE sentiment_score IS NOT NULL "
                    "AND timestamp > NOW() - INTERVAL '24 hours' "
                    "GROUP BY source"
                ))
                summary = {
                    row[0]: {"avg_score": round(float(row[1]), 3), "count": row[2]}
                    for row in result
                }

            await self._bus.cache_set("precomputed:sentiment_summary", summary)
            return True
        except Exception as exc:
            logger.warning("Sentiment precompute failed: %s", exc)
            return False

    async def _precompute_country_sentiment(self) -> bool:
        """Average sentiment per country for the last 7 days."""
        try:
            from storage.database import get_session
            from sqlalchemy import text

            async with get_session() as session:
                result = await session.execute(text(
                    "SELECT "
                    "COALESCE(extras->>'country', extras->>'sourcecountry', extras->>'origin_country') AS country, "
                    "AVG(sentiment_score) AS avg_score, "
                    "COUNT(*) AS cnt, "
                    "SUM(CASE WHEN sentiment_label = 'NEGATIVE' THEN 1 ELSE 0 END) AS negative "
                    "FROM events "
                    "WHERE sentiment_score IS NOT NULL "
                    "AND timestamp > NOW() - INTERVAL '7 days' "
                    "AND COALESCE(extras->>'country', extras->>'sourcecountry', extras->>'origin_country') IS NOT NULL "
                    "GROUP BY country"
                ))
                rows = result.fetchall()

            from core.geo import normalize_country

            aggregates = {}
            for row in rows:
                normalized = normalize_country(row.country, allow_fallback=False)
                if not normalized:
                    continue
                count = int(row.cnt)
                agg = aggregates.setdefault(normalized, {
                    "sum": 0.0,
                    "count": 0,
                    "negative": 0,
                })
                agg["sum"] += float(row.avg_score) * count
                agg["count"] += count
                agg["negative"] += int(row.negative or 0)

            payload = []
            max_count = max((agg["count"] for agg in aggregates.values()), default=1)
            for country, agg in aggregates.items():
                avg_score = agg["sum"] / max(agg["count"], 1)
                negative_ratio = agg["negative"] / max(agg["count"], 1)
                weight = math.log(agg["count"] + 1) / math.log(max_count + 1)
                risk_score = round(max(0.0, (1.0 - avg_score) * weight), 4)
                payload.append({
                    "country": country,
                    "avg_score": round(avg_score, 4),
                    "event_count": agg["count"],
                    "negative_ratio": round(negative_ratio, 4),
                    "risk_score": risk_score,
                })

            payload.sort(key=lambda item: item["risk_score"], reverse=True)
            await self._bus.cache_set("precomputed:country_sentiment", {
                "generated_at": __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ).isoformat(),
                "countries": payload,
            })
            return True
        except Exception as exc:
            logger.warning("Country sentiment precompute failed: %s", exc)
            return False

    async def _precompute_correlation_summary(self) -> bool:
        """Signal cluster counts and top clusters for the dashboard."""
        try:
            from storage.signal_repo import SignalRepository
            repo = SignalRepository()
            stats = await repo.count_signals(window_hours=24)

            from storage.database import get_session
            from sqlalchemy import text

            # Top 5 clusters by signal count
            top_clusters = []
            async with get_session() as session:
                result = await session.execute(text(
                    "SELECT cluster_id, COUNT(*) AS cnt, "
                    "array_agg(DISTINCT source) AS sources, "
                    "MIN(timestamp) AS earliest, MAX(timestamp) AS latest "
                    "FROM signals "
                    "WHERE cluster_id IS NOT NULL "
                    "AND timestamp > NOW() - INTERVAL '24 hours' "
                    "GROUP BY cluster_id "
                    "ORDER BY cnt DESC LIMIT 10"
                ))
                for row in result:
                    signal_count = row[1]
                    earliest = row[3]
                    latest = row[4]
                    if earliest and latest:
                        span_hours = round((latest - earliest).total_seconds() / 3600, 2)
                    else:
                        span_hours = 0.0
                    top_clusters.append({
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

            summary = {
                "total_signals": stats.get("total", 0),
                "clustered_signals": stats.get("clustered", 0),
                "cluster_count": stats.get("cluster_count", 0),
                "source_count": stats.get("source_count", 0),
                "top_clusters": top_clusters,
                "generated_at": __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ).isoformat(),
            }
            await self._bus.cache_set("precomputed:correlation_summary", summary)
            await self._bus.cache_set("precomputed:signal_clusters", top_clusters)
            return True
        except Exception as exc:
            logger.warning("Correlation precompute failed: %s", exc)
            return False

    async def _precompute_confidence_distribution(self) -> bool:
        """Distribution of events by confidence tier."""
        try:
            from storage.database import get_session
            from sqlalchemy import text

            async with get_session() as session:
                result = await session.execute(text(
                    "SELECT "
                    "  COUNT(CASE WHEN confidence_score >= 0.7 THEN 1 END) AS high, "
                    "  COUNT(CASE WHEN confidence_score >= 0.3 AND confidence_score < 0.7 THEN 1 END) AS medium, "
                    "  COUNT(CASE WHEN confidence_score < 0.3 THEN 1 END) AS low, "
                    "  COUNT(CASE WHEN confidence_score IS NULL THEN 1 END) AS unscored, "
                    "  COUNT(*) AS total "
                    "FROM events "
                    "WHERE timestamp > NOW() - INTERVAL '24 hours'"
                ))
                row = result.first()

            distribution = {
                "high": row[0] if row else 0,
                "medium": row[1] if row else 0,
                "low": row[2] if row else 0,
                "unscored": row[3] if row else 0,
                "total": row[4] if row else 0,
                "generated_at": __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ).isoformat(),
            }
            await self._bus.cache_set("precomputed:confidence_distribution", distribution)
            return True
        except Exception as exc:
            logger.warning("Confidence distribution precompute failed: %s", exc)
            return False

    async def _precompute_unrest_watch(self) -> bool:
        """Unified unrest watch used by narratives, alerts, and map surfaces."""
        try:
            from intelligence.unrest_engine import UnrestWatchEngine
            from storage.database import get_session

            async with get_session() as session:
                payload = await UnrestWatchEngine(session).build_watch(window_hours=72)

            await self._bus.cache_set("precomputed:unrest_watch", payload)
            return True
        except Exception as exc:
            logger.warning("Unrest watch precompute failed: %s", exc)
            return False

    async def _precompute_escalation_scores(self) -> bool:
        """Compute per-region escalation scores and cache to Redis."""
        try:
            from storage.database import get_session
            from intelligence.escalation_scorer import EscalationScorer
            async with get_session() as session:
                scorer = EscalationScorer(session)
                scores = await scorer.score_all_regions(window_hours=6)
            payload = {
                "scores": [
                    {
                        "region": s.region,
                        "score": s.score,
                        "risk_level": s.risk_level,
                        "drivers": s.drivers,
                        "confidence": s.confidence,
                        "event_count": s.event_count,
                        "computed_at": s.computed_at,
                    }
                    for s in scores
                ],
                "generated_at": __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ).isoformat(),
            }
            await self._bus.cache_set("precomputed:escalation_scores", payload, ttl_seconds=600)
            return True
        except Exception as exc:
            logger.warning("Escalation precompute failed: %s", exc)
            return False

    async def _precompute_community_graph(self) -> bool:
        """Detect actor communities and write back to graph + Redis."""
        try:
            import asyncio
            loop = asyncio.get_running_loop()
            communities = await loop.run_in_executor(None, self._graph.detect_communities)
            if communities:
                await loop.run_in_executor(None, self._graph.write_community_memberships, communities)
            temporal = await loop.run_in_executor(None, self._graph.get_temporal_graph, 48, 0)
            await self._bus.cache_set("precomputed:community_graph", {
                "communities": len(set(communities.values())) if communities else 0,
                "actor_count": len(communities),
                "temporal_graph": temporal,
                "generated_at": __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ).isoformat(),
            }, ttl_seconds=3600)
            return True
        except Exception as exc:
            logger.warning("Community precompute failed: %s", exc)
            return False
