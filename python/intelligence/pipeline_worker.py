"""
intelligence/pipeline_worker.py
────────────────────────────────
Event-driven intelligence pipeline worker.

Subscribes to Redis Pub/Sub events from the data layer and chains
intelligence stages in sequence:

  pipeline:ingest_complete
    → narrative detection
    → anomaly scan
    → precompute artifacts to Redis
    → publish pipeline:intelligence_complete

This is the primary trigger for intelligence. APScheduler jobs serve as
a safety net (run on intervals regardless) but this worker reacts to
data arrival for faster updates.
"""

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Optional

from config.settings import settings

if TYPE_CHECKING:
    from core.event_bus import EventBus
    from storage.graph import GraphDB

logger = logging.getLogger("vision_i.intelligence.pipeline_worker")


async def start_pipeline_worker(
    event_bus: "EventBus",
    graph: "GraphDB",
) -> asyncio.Task:
    """
    Start the intelligence pipeline worker as a background task.
    Returns the Task handle so it can be cancelled on shutdown.
    """
    task = asyncio.create_task(_pipeline_loop(event_bus, graph))
    logger.info("Intelligence pipeline worker started")
    return task


async def _pipeline_loop(event_bus: "EventBus", graph: "GraphDB") -> None:
    """Main loop: subscribe to ingest events and run intelligence."""
    try:
        pubsub = await event_bus.subscribe("ingest_complete")

        async for message in pubsub.listen():
            if message["type"] != "message":
                continue

            try:
                payload = json.loads(message["data"])
                batch_id = payload.get("batch_id", "unknown")
                event_count = payload.get("event_count", 0)
                job_type = payload.get("job_type", "unknown")

                logger.info(
                    "Pipeline worker: received ingest_complete batch=%s events=%d type=%s",
                    batch_id, event_count, job_type,
                )

                # Skip intelligence for very small batches (live telemetry)
                if event_count < 3 and job_type == "live":
                    logger.info("Pipeline worker: skipping intelligence for small live batch")
                    # Still run precompute for dashboard
                    await _run_precompute(event_bus, graph)
                    continue

                # Run intelligence chain
                n_count  = await _run_narratives(graph)
                clusters = await _run_correlation(event_bus, graph)
                c_count  = await _run_composite_detection(clusters, event_bus)
                a_count  = await _run_anomalies()
                s_count  = await _run_social_enrichment(event_bus)
                r_count  = await _run_risk_scoring(event_bus)
                sit_count = await _run_situation_detection(graph, event_bus)
                await _run_precompute(event_bus, graph)

                # Publish intelligence complete
                await event_bus.publish("intelligence_complete", {
                    "batch_id":        batch_id,
                    "narratives_count": n_count,
                    "clusters_count":  len(clusters) if clusters else 0,
                    "composite_events": c_count,
                    "alerts_count":    a_count,
                    "social_enriched": s_count,
                    "risk_scored":     r_count,
                    "situations":      sit_count,
                    "trigger":         "ingest_complete",
                })

                logger.info(
                    "Pipeline worker: intelligence complete batch=%s narratives=%d alerts=%d",
                    batch_id, n_count, a_count,
                )

            except Exception as exc:
                logger.error("Pipeline worker: error processing message: %s", exc)

    except asyncio.CancelledError:
        logger.info("Pipeline worker: cancelled")
    except Exception as exc:
        logger.error("Pipeline worker: fatal error: %s", exc)


async def _run_narratives(graph: "GraphDB") -> int:
    """Run narrative detection. Returns signal count."""
    try:
        from intelligence.narrative_detector import NarrativeDetector
        from storage.intelligence_repo import NarrativeRepository
        from storage.database import get_session

        async with get_session() as session:
            detector = NarrativeDetector(session=session, graph=graph)
            signals = await detector.detect(window_hours=6, baseline_days=7)

            if signals:
                repo = NarrativeRepository(session)
                saved = await repo.upsert_signals(signals)

                if graph and graph.available:
                    import asyncio
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(
                        None,
                        lambda: graph.write_narrative_nodes([s.to_dict() for s in signals])
                    )
                return saved
        return 0
    except Exception as exc:
        logger.error("Pipeline worker: narrative detection failed: %s", exc)
        return 0


async def _run_anomalies() -> int:
    """Run anomaly scanning. Returns alert count."""
    try:
        from intelligence.anomaly_detector import AnomalyDetector
        from storage.intelligence_repo import AlertRepository
        from storage.database import get_session

        async with get_session() as session:
            detector = AnomalyDetector(session=session)
            alerts = await detector.scan(window_hours=1, baseline_days=7)

            if alerts:
                repo = AlertRepository(session)
                saved = await repo.upsert_alerts(alerts)
                return saved
        return 0
    except Exception as exc:
        logger.error("Pipeline worker: anomaly scan failed: %s", exc)
        return 0


async def _run_correlation(event_bus: "EventBus", graph: "GraphDB") -> list:
    """Run signal correlation. Returns list of SignalCluster objects."""
    try:
        from intelligence.correlation_engine import CorrelationEngine

        engine = CorrelationEngine()
        clusters = await engine.correlate()

        if clusters and graph and graph.available:
            import asyncio
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, graph.write_signal_clusters, clusters)

        if clusters and event_bus:
            await event_bus.publish("correlation_complete", {
                "cluster_count": len(clusters),
                "total_signals": sum(len(c.signal_ids) for c in clusters),
            })

        return clusters or []
    except Exception as exc:
        logger.error("Pipeline worker: correlation failed: %s", exc)
        return []


async def _run_composite_detection(clusters: list, event_bus: "EventBus") -> int:
    """Run composite event detection on signal clusters. Returns event count."""
    if not clusters:
        return 0
    try:
        from intelligence.composite_detector import CompositeEventDetector

        detector = CompositeEventDetector()
        count = await detector.detect(clusters)

        if count > 0 and event_bus:
            await event_bus.publish("composite_events", {
                "event_count": count,
                "cluster_count": len(clusters),
            })

        return count
    except Exception as exc:
        logger.error("Pipeline worker: composite detection failed: %s", exc)
        return 0


async def _run_precompute(event_bus: "EventBus", graph: "GraphDB") -> None:
    """Run all precomputation and write to Redis."""
    try:
        from intelligence.precompute import IntelligencePrecomputer
        precomputer = IntelligencePrecomputer(event_bus=event_bus, graph=graph)
        results = await precomputer.precompute_all()
        logger.info("Pipeline worker: precompute results: %s", results)
    except Exception as exc:
        logger.error("Pipeline worker: precompute failed: %s", exc)


async def _run_risk_scoring(event_bus: Optional["EventBus"]) -> int:
    """Score recent events with the unified risk engine. Returns count of events scored."""
    try:
        from datetime import datetime, timezone, timedelta
        from sqlalchemy import select, desc, update
        from storage.database import get_session, EventModel
        from intelligence.risk_engine import compute_risk_score, severity_from_score

        cutoff = datetime.now(timezone.utc) - timedelta(hours=6)

        async with get_session() as session:
            rows = (await session.execute(
                select(EventModel)
                .where(EventModel.ingest_time >= cutoff)
                .where(EventModel.risk_score.is_(None))
                .order_by(desc(EventModel.ingest_time))
                .limit(200)
            )).scalars().all()

            if not rows:
                return 0

            scored = 0
            for row in rows:
                ev = {
                    "event_id":    row.event_id,
                    "title":       row.title or "",
                    "description": row.description,
                    "body":        row.body,
                    "actors":      row.actors or [],
                    "tags":        row.tags or [],
                    "sentiment":   {"score": row.sentiment_score} if row.sentiment_score else None,
                    "influence_score": row.influence_score,
                    "signal_count": row.signal_count,
                    "supporting_signals": row.supporting_signals or [],
                    "extras":      row.extras or {},
                    "reasoning":   row.reasoning,
                    "event_type":  row.event_type,
                }
                score = compute_risk_score(ev)
                row.risk_score = score
                scored += 1

        if scored > 0 and event_bus:
            await event_bus.publish("risk_score_updated", {"events_scored": scored})

        logger.info("Pipeline worker: risk scoring complete — %d events scored", scored)
        return scored

    except Exception as exc:
        logger.error("Pipeline worker: risk scoring failed: %s", exc)
        return 0


async def _run_situation_detection(
    graph: "GraphDB",
    event_bus: Optional["EventBus"],
) -> int:
    """Detect situations from recent events. Returns count of situations upserted."""
    try:
        from datetime import datetime, timezone, timedelta
        from sqlalchemy import select, desc as sa_desc
        from storage.database import get_session, EventModel
        from storage.situation_repo import upsert_situation
        from intelligence.situation_detector import detect_situations

        cutoff = datetime.now(timezone.utc) - timedelta(hours=6)

        async with get_session() as session:
            rows = (await session.execute(
                select(EventModel)
                .where(EventModel.ingest_time >= cutoff)
                .order_by(sa_desc(EventModel.ingest_time))
                .limit(300)
            )).scalars().all()

        if not rows:
            return 0

        events = [
            {
                "event_id":    r.event_id,
                "title":       r.title or "",
                "description": r.description,
                "body":        r.body,
                "source":      r.source,
                "event_type":  r.event_type,
                "timestamp":   r.timestamp.isoformat() if r.timestamp else None,
                "actors":      r.actors or [],
                "tags":        r.tags or [],
                "location": {
                    "lat":  r.location_lat,
                    "lon":  r.location_lon,
                    "name": r.location_name,
                } if (r.location_name or r.location_lat) else {},
                "sentiment":   {"score": r.sentiment_score} if r.sentiment_score else None,
                "risk_score":  r.risk_score,
                "influence_score": r.influence_score,
                "signal_count": r.signal_count,
                "extras":      r.extras or {},
            }
            for r in rows
        ]

        situations = detect_situations(events)
        if not situations:
            return 0

        async with get_session() as session:
            for sit in situations:
                await upsert_situation(session, sit)

        if graph and graph.available:
            import asyncio
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, graph.write_situation_nodes, situations)

        if event_bus:
            await event_bus.publish("situation_updated", {
                "situation_count": len(situations),
                "trigger": "pipeline",
            })

        logger.info("Pipeline worker: %d situations detected/updated", len(situations))
        return len(situations)

    except Exception as exc:
        logger.error("Pipeline worker: situation detection failed: %s", exc)
        return 0


async def _run_social_enrichment(event_bus: Optional["EventBus"]) -> int:
    """Trigger social search enrichment for significant recent events."""
    try:
        from intelligence.social_enricher import SocialEnricher
        from nlp.pipeline import NLPPipeline
        from storage.database import get_session

        async with get_session() as session:
            enricher = SocialEnricher(
                window_hours=settings.social_enrich_window_hours,
                max_events=settings.social_enrich_max_events,
                limit_per_event=settings.social_enrich_limit_per_event,
                min_score=settings.social_enrich_min_score,
                cooldown_minutes=settings.social_enrich_cooldown_minutes,
            )
            nlp = NLPPipeline()
            return await enricher.enrich_recent_events(session, nlp, event_bus)
    except Exception as exc:
        logger.warning("Pipeline worker: social enrichment failed: %s", exc)
        return 0
