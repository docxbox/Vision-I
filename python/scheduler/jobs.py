"""Background jobs for ingestion and downstream processing."""

import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config.settings import settings
from core.utils import utcnow_iso
from ontology.views import refresh_precomputed_views

if TYPE_CHECKING:
    from core.orchestrator import Orchestrator
    from core.enricher import Enricher
    from core.event_bus import EventBus
    from intelligence.embedder import EmbeddingService
    from nlp.pipeline import NLPPipeline
    from storage.event_repo import EventRepository
    from storage.graph import GraphDB

logger = logging.getLogger("vision_i.scheduler")

async def _load_checkpoints() -> Dict[str, datetime]:
    """Load source checkpoints from the database."""
    from storage.database import get_session, SourceCheckpointModel
    from sqlalchemy import select

    checkpoints: Dict[str, datetime] = {}
    try:
        async with get_session() as session:
            result = await session.execute(select(SourceCheckpointModel))
            for row in result.scalars().all():
                if row.last_event_ts:
                    checkpoints[row.source] = row.last_event_ts
    except Exception as exc:
        logger.warning("Failed to load checkpoints: %s", exc)
    return checkpoints


def _resolve_text_queries(queries: List[str]) -> List[str]:
    return [query.strip() for query in queries if query and query.strip()]


async def _update_checkpoints(
    events: list,
    source_counts: Dict[str, int],
) -> None:
    """Update source checkpoints based on the newest events per source."""
    from storage.database import get_session, SourceCheckpointModel
    from sqlalchemy import select
    from core.source_registry import source_registry

    if not events:
        return

    def _parse_event_ts(value: Any) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except Exception:
            return None

    # Keep the newest event per concrete source and derive per-source counts
    # from the events themselves. Scheduler task buckets like "gdelt" or
    # "socials" are not precise enough for checkpoint high-water marks.
    newest: Dict[str, dict] = {}
    event_counts: Dict[str, int] = {}
    for e in events:
        src = e.get("source", "")
        if not src:
            continue
        event_counts[src] = event_counts.get(src, 0) + 1
        ts = _parse_event_ts(e.get("timestamp"))
        if ts is None:
            continue
        existing = newest.get(src)
        existing_ts = _parse_event_ts(existing.get("timestamp")) if existing else None
        if existing is None or existing_ts is None or ts > existing_ts:
            newest[src] = e

    try:
        async with get_session() as session:
            for src, event in newest.items():
                result = await session.execute(
                    select(SourceCheckpointModel)
                    .where(SourceCheckpointModel.source == src)
                )
                checkpoint = result.scalar_one_or_none()

                observed_count = event_counts.get(src, 0)
                event_ts = _parse_event_ts(event.get("timestamp")) or datetime.now(timezone.utc)
                now = datetime.now(timezone.utc)
                meta = dict(checkpoint.meta or {}) if checkpoint else {}
                meta.update({
                    "canonical_source": source_registry.canonicalize(src) or src,
                    "last_batch_count": observed_count,
                    "last_scheduler_counts": source_counts,
                })
                if checkpoint:
                    checkpoint.last_event_ts = event_ts
                    checkpoint.last_event_id = event.get("event_id", "")
                    checkpoint.last_run_at = now
                    checkpoint.events_fetched = checkpoint.events_fetched + observed_count
                    checkpoint.meta = meta
                else:
                    session.add(SourceCheckpointModel(
                        source=src,
                        last_event_ts=event_ts,
                        last_event_id=event.get("event_id", ""),
                        last_run_at=now,
                        events_fetched=observed_count,
                        meta=meta,
                    ))
    except Exception as exc:
        logger.warning("Failed to update checkpoints: %s", exc)

async def live_ingest_job(
    orchestrator: "Orchestrator",
    enricher:     "Enricher",
    nlp:          "NLPPipeline",
    repo:         "EventRepository",
    graph:        "GraphDB",
    event_bus:    Optional["EventBus"] = None,
    embedder:     Optional["EmbeddingService"] = None,
) -> None:
    """Runs the live-source ingest pipeline."""
    logger.info("[Scheduler] Live ingest starting")
    start_time = time.monotonic()
    try:
        import asyncio
        loop   = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, lambda: orchestrator.run_live_only(limit=300)
        )
        events = result.events

        # Store tracked flights and vessels as assets, not routine events.
        try:
            from extractors.ais import AISExtractor
            from extractors.opensky import OpenSkyExtractor
            from storage.asset_repo import AssetRepository
            opensky = OpenSkyExtractor()
            ais = AISExtractor()
            asset_records = []
            for ev in list(events):
                if ev.get("source") == "opensky":
                    raw = ev.get("raw")
                    if raw:
                        asset = opensky.normalize_asset(raw)
                        if asset:
                            asset_records.append(asset)
                        # Keep only anomalous flight events.
                        if not ev.get("extras", {}).get("anomaly"):
                            events.remove(ev)
                elif ev.get("source") == "ais":
                    raw = ev.get("raw")
                    if raw:
                        asset = ais.normalize_asset(raw)
                        if asset:
                            asset_records.append(asset)
                        # Keep only anomalous vessel events.
                        if not ev.get("extras", {}).get("anomaly"):
                            events.remove(ev)
            if asset_records:
                asset_repo = AssetRepository()
                await asset_repo.upsert_assets(asset_records)
                logger.info("[Scheduler] Live ingest: tracked %d physical assets", len(asset_records))
        except Exception as exc:
            logger.warning("[Scheduler] Asset extraction failed: %s", exc)

        # Validate the payload before enrichment.
        from core.validator import validate_batch
        events, rejected = validate_batch(events)
        if rejected:
            logger.warning("[Scheduler] Live ingest: %d events rejected by validator", len(rejected))

        # Live sources only need sentiment enrichment.
        await loop.run_in_executor(None, lambda: nlp.process(events))

        # Create signals when embeddings are available.
        if embedder and embedder.available and events:
            try:
                from intelligence.signal_processor import SignalProcessor
                from storage.signal_repo import SignalRepository
                sig_proc = SignalProcessor(embedder=embedder)
                signals = await loop.run_in_executor(
                    None, lambda: sig_proc.create_signals_sync(events)
                )
                if signals:
                    sig_repo = SignalRepository()
                    await sig_repo.upsert_signals(signals)
                    if graph and graph.available:
                        await graph.write_signals(signals)
                    logger.info("[Scheduler] Live ingest: created %d signals", len(signals))
            except Exception as exc:
                logger.warning("[Scheduler] Signal creation failed: %s", exc)

        # Record lineage for the ingest stage.
        from storage.database import get_session
        from storage.lineage import LineageTracker
        batch_id = None
        async with get_session() as session:
            tracker = LineageTracker(session)
            batch_id = await tracker.record_stage(
                stage="raw_ingest",
                event_count=len(events),
                source="live",
                meta={"source_counts": result.source_counts},
            )

        # Persist relational data.
        from storage.event_repo import EventRepository
        async with get_session() as session:
            r = EventRepository(session)
            count = await r.upsert_many(events)

        # Persist graph data.
        await graph.write_events(events)

        # Advance source checkpoints.
        await _update_checkpoints(events, result.source_counts)

        # Record lineage for the enrichment stage.
        async with get_session() as session:
            tracker = LineageTracker(session)
            await tracker.record_stage(
                stage="nlp_enriched",
                event_count=len(events),
                parent_batch=batch_id,
            )

        elapsed = time.monotonic() - start_time

        # Publish the ingest completion event.
        if event_bus:
            try:
                await event_bus.publish("ingest_complete", {
                    "batch_id": batch_id,
                    "event_count": len(events),
                    "source_counts": result.source_counts,
                    "job_type": "live",
                    "duration_ms": int(elapsed * 1000),
                    "timestamp": utcnow_iso(),
                })
                # Refresh the live stream cache.
                serializable = []
                for e in events:
                    clean = {k: v for k, v in e.items() if k != "raw"}
                    serializable.append(clean)
                await event_bus.cache_set(
                    "precomputed:live_streams", serializable, ttl_seconds=1200
                )
            except Exception as exc:
                logger.warning("[Scheduler] Event bus publish failed: %s", exc)

        # Update cached job metrics.
        if event_bus:
            try:
                await event_bus.cache_set("metrics:job:live_ingest:last_duration_ms", int(elapsed * 1000))
                await event_bus.cache_set("metrics:job:live_ingest:last_run", utcnow_iso())
                await refresh_precomputed_views(event_bus)
            except Exception:
                pass

        logger.info(
            "[Scheduler] Live ingest done: %d events in %.1fs (%s)",
            result.total, elapsed,
            ", ".join(f"{k}={v}" for k, v in result.source_counts.items()),
        )
    except Exception as exc:
        logger.error("[Scheduler] Live ingest failed: %s", exc)


async def text_ingest_job(
    orchestrator: "Orchestrator",
    enricher:     "Enricher",
    nlp:          "NLPPipeline",
    graph:        "GraphDB",
    event_bus:    Optional["EventBus"] = None,
    embedder:     Optional["EmbeddingService"] = None,
) -> None:
    """Runs the text-source ingest pipeline for active queries."""
    logger.info("[Scheduler] Text ingest starting")
    start_time = time.monotonic()
    try:
        import asyncio
        loop = asyncio.get_running_loop()

        # Load active queries from the database.
        from storage.database import get_session
        from storage.event_repo import EventRepository
        async with get_session() as session:
            repo    = EventRepository(session)
            queries = await repo.get_active_queries()

        queries = _resolve_text_queries(queries)

        if not queries:
            logger.info("[Scheduler] No active queries â€” skipping text ingest")
            return

        logger.info("[Scheduler] Running text ingest for %d queries: %s", len(queries), queries)

        # Update query run metadata.
        async with get_session() as session:
            from sqlalchemy import update
            from storage.database import TrackedQueryModel
            await session.execute(
                update(TrackedQueryModel)
                .where(TrackedQueryModel.query.in_(queries))
                .values(last_run=datetime.now(timezone.utc),
                        run_count=TrackedQueryModel.run_count + 1)
            )

        all_events = []
        total_source_counts: Dict[str, int] = {}
        for query in queries:
            result = await loop.run_in_executor(
                None,
                lambda q=query: orchestrator.run_text_only(query=q, limit=20),
            )
            # Enrich article bodies before NLP.
            await loop.run_in_executor(None, lambda: enricher.enrich(result.events))
            all_events.extend(result.events)
            for k, v in result.source_counts.items():
                total_source_counts[k] = total_source_counts.get(k, 0) + v

        # Validate the payload before enrichment.
        from core.validator import validate_batch
        all_events, rejected = validate_batch(all_events)
        if rejected:
            logger.warning("[Scheduler] Text ingest: %d events rejected by validator", len(rejected))

        # Text sources run the full NLP pipeline.
        await loop.run_in_executor(None, lambda: nlp.process(all_events))

        # Create signals when embeddings are available.
        if embedder and embedder.available and all_events:
            try:
                from intelligence.signal_processor import SignalProcessor
                from storage.signal_repo import SignalRepository
                sig_proc = SignalProcessor(embedder=embedder)
                signals = await loop.run_in_executor(
                    None, lambda: sig_proc.create_signals_sync(all_events)
                )
                if signals:
                    sig_repo = SignalRepository()
                    await sig_repo.upsert_signals(signals)
                    if graph and graph.available:
                        await graph.write_signals(signals)
                    logger.info("[Scheduler] Text ingest: created %d signals", len(signals))
            except Exception as exc:
                logger.warning("[Scheduler] Signal creation failed: %s", exc)

        # Record lineage for the ingest stage.
        from storage.lineage import LineageTracker
        async with get_session() as session:
            tracker = LineageTracker(session)
            batch_id = await tracker.record_stage(
                stage="raw_ingest",
                event_count=len(all_events),
                source="text",
                event_ids=[e.get("event_id", "") for e in all_events],
                meta={"queries": queries, "source_counts": total_source_counts},
            )

        # Persist relational data.
        async with get_session() as session:
            repo  = EventRepository(session)
            count = await repo.upsert_many(all_events)

        # Persist graph data.
        await graph.write_events(all_events)

        # Advance source checkpoints.
        await _update_checkpoints(all_events, total_source_counts)

        # Record lineage for the enrichment stage.
        async with get_session() as session:
            tracker = LineageTracker(session)
            await tracker.record_stage(
                stage="nlp_enriched",
                event_count=len(all_events),
                parent_batch=batch_id,
            )

        elapsed = time.monotonic() - start_time

        # Publish the ingest completion event.
        if event_bus:
            try:
                await event_bus.publish("ingest_complete", {
                    "batch_id": batch_id,
                    "event_count": len(all_events),
                    "source_counts": total_source_counts,
                    "job_type": "text",
                    "queries": queries,
                    "duration_ms": int(elapsed * 1000),
                    "timestamp": utcnow_iso(),
                })
            except Exception as exc:
                logger.warning("[Scheduler] Event bus publish failed: %s", exc)

        # Update cached job metrics.
        if event_bus:
            try:
                await event_bus.cache_set("metrics:job:text_ingest:last_duration_ms", int(elapsed * 1000))
                await event_bus.cache_set("metrics:job:text_ingest:last_run", utcnow_iso())
                await refresh_precomputed_views(event_bus)
            except Exception:
                pass

        logger.info("[Scheduler] Text ingest done: %d total events in %.1fs", len(all_events), elapsed)

    except Exception as exc:
        logger.error("[Scheduler] Text ingest failed: %s", exc)


async def narrative_detection_job(
    graph: "GraphDB",
    event_bus: Optional["EventBus"] = None,
) -> None:
    """
    Runs narrative detection every NARRATIVE_INTERVAL_SECONDS (default: 30 min).
    Detects velocity spikes, cross-source amplification, and sentiment divergence.
    Results are persisted to PostgreSQL and Neo4j.
    """
    logger.info("[Scheduler] Narrative detection starting")
    start_time = time.monotonic()
    try:
        import asyncio
        from intelligence.narrative_detector import NarrativeDetector
        from storage.intelligence_repo import NarrativeRepository

        from storage.database import get_session
        async with get_session() as session:
            detector   = NarrativeDetector(session=session, graph=graph)
            signals    = await detector.detect(window_hours=6, baseline_days=7)

            n_count = 0
            if signals:
                repo  = NarrativeRepository(session)
                saved = await repo.upsert_signals(signals)
                n_count = saved
                logger.info("[Scheduler] Narrative detection: %d signals (%d persisted)", len(signals), saved)

                if graph and graph.available:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(
                        None,
                        lambda: graph.write_narrative_nodes([s.to_dict() for s in signals])
                    )
            else:
                logger.info("[Scheduler] Narrative detection: no signals")

        elapsed = time.monotonic() - start_time

        # Refresh the cached narrative summary.
        if event_bus:
            try:
                async with get_session() as session:
                    from sqlalchemy import text
                    result = await session.execute(text(
                        "SELECT signal_type, severity, COUNT(*) as cnt "
                        "FROM narratives WHERE status = 'active' "
                        "GROUP BY signal_type, severity"
                    ))
                    summary = {}
                    for row in result:
                        key = row[0]
                        summary.setdefault(key, {"total": 0, "by_severity": {}})
                        summary[key]["by_severity"][row[1]] = row[2]
                        summary[key]["total"] += row[2]
                    await event_bus.cache_set("precomputed:narratives_summary", summary)

                await event_bus.cache_set("metrics:job:narrative:last_duration_ms", int(elapsed * 1000))
                await event_bus.cache_set("metrics:job:narrative:last_run", utcnow_iso())
            except Exception as exc:
                logger.warning("[Scheduler] Narrative precompute failed: %s", exc)

    except Exception as exc:
        logger.error("[Scheduler] Narrative detection failed: %s", exc)


async def anomaly_scan_job(
    event_bus: Optional["EventBus"] = None,
    swarm: Optional[Any] = None,
) -> None:
    """
    Runs anomaly scanning every ANOMALY_INTERVAL_SECONDS (default: 60 min).
    Detects entity spikes, geo clusters, and source silence.
    """
    logger.info("[Scheduler] Anomaly scan starting")
    start_time = time.monotonic()
    try:
        from intelligence.anomaly_detector import AnomalyDetector
        from storage.intelligence_repo import AlertRepository

        from storage.database import get_session
        async with get_session() as session:
            detector = AnomalyDetector(session=session)
            alerts   = await detector.scan(window_hours=1, baseline_days=7)

            a_count = 0
            if alerts:
                repo  = AlertRepository(session)
                saved = await repo.upsert_alerts(alerts)
                a_count = saved
                logger.info("[Scheduler] Anomaly scan: %d alerts (%d persisted)", len(alerts), saved)
                
                # Refresh the executive summary cache.
                try:
                    from agents.coordinator_agent import CoordinatorAgent
                    from agents.swarm import SwarmManager
                    # Use app-level swarm (has LLM attached) when available.
                    active_swarm = swarm if swarm is not None else SwarmManager()
                    coord = CoordinatorAgent(active_swarm)
                    # Convert typed alerts before passing them to the coordinator.
                    alert_dicts = [a.to_dict() for a in alerts]
                    ceo_summary = await coord.generate_ceo_summary(alert_dicts)
                    if ceo_summary and event_bus:
                        await event_bus.cache_set("precomputed:jarvis_insight", ceo_summary)
                except Exception as ex:
                    logger.warning("[Scheduler] Jarvis CEO summary failed: %s", ex)
            else:
                logger.info("[Scheduler] Anomaly scan: no anomalies")

        elapsed = time.monotonic() - start_time

        # Refresh the cached alert summary.
        if event_bus:
            try:
                async with get_session() as session:
                    from sqlalchemy import text
                    result = await session.execute(text(
                        "SELECT severity, COUNT(*) as cnt "
                        "FROM alerts WHERE acknowledged = false "
                        "GROUP BY severity"
                    ))
                    summary = {row[0]: row[1] for row in result}
                    summary["total"] = sum(summary.values())
                    await event_bus.cache_set("precomputed:alerts_summary", summary)

                await event_bus.cache_set("metrics:job:anomaly:last_duration_ms", int(elapsed * 1000))
                await event_bus.cache_set("metrics:job:anomaly:last_run", utcnow_iso())

                # Broadcast to SignalR clients via Redis Pub/Sub
                try:
                    await event_bus.publish("intelligence_complete", {
                        "alerts_count": str(a_count),
                        "narratives_count": "0",
                        "trigger": "anomaly_scan",
                    })
                except Exception as pub_exc:
                    logger.warning("[Scheduler] SignalR broadcast failed: %s", pub_exc)
            except Exception as exc:
                logger.warning("[Scheduler] Anomaly precompute failed: %s", exc)

    except Exception as exc:
        logger.error("[Scheduler] Anomaly scan failed: %s", exc)


async def correlation_job(
    graph: "GraphDB",
    event_bus: Optional["EventBus"] = None,
) -> None:
    """Runs a scheduled correlation pass as a fallback for missed event triggers."""
    logger.info("[Scheduler] Correlation job starting")
    start_time = time.monotonic()
    try:
        from intelligence.correlation_engine import CorrelationEngine
        from intelligence.composite_detector import CompositeEventDetector

        engine = CorrelationEngine()
        clusters = await engine.correlate()

        if clusters:
            if graph and graph.available:
                import asyncio
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, graph.write_signal_clusters, clusters)
            detector = CompositeEventDetector()
            c_count = await detector.detect(clusters)
            logger.info(
                "[Scheduler] Correlation: %d clusters, %d composite events",
                len(clusters), c_count,
            )

            if event_bus:
                await event_bus.publish("correlation_complete", {
                    "cluster_count": len(clusters),
                    "composite_events": c_count,
                    "trigger": "scheduler",
                })
        else:
            logger.info("[Scheduler] Correlation: no clusters formed")

        elapsed = time.monotonic() - start_time
        if event_bus:
            try:
                await event_bus.cache_set("metrics:job:correlation:last_duration_ms", int(elapsed * 1000))
                await event_bus.cache_set("metrics:job:correlation:last_run", utcnow_iso())
                await refresh_precomputed_views(event_bus)
            except Exception:
                pass

    except Exception as exc:
        logger.error("[Scheduler] Correlation job failed: %s", exc)


async def influence_update_job(
    graph: "GraphDB",
    event_bus: Optional["EventBus"] = None,
) -> None:
    """Recomputes actor influence scores and writes them to Neo4j."""
    logger.info("[Scheduler] Influence scoring starting")
    start_time = time.monotonic()
    try:
        from intelligence.influence_scorer import InfluenceScorer

        from storage.database import get_session
        async with get_session() as session:
            scorer = InfluenceScorer(session=session, graph=graph)
            scores = await scorer.update_scores(top_k=500)
            logger.info("[Scheduler] Influence scoring: updated %d actors", len(scores))

        elapsed = time.monotonic() - start_time

        # Refresh the cached influence network.
        if event_bus and graph and graph.available:
            try:
                import asyncio
                loop = asyncio.get_running_loop()
                network = await loop.run_in_executor(
                    None, lambda: graph.get_influence_network(limit=200, min_strength=0.1)
                )
                if network:
                    await event_bus.cache_set("precomputed:influence_network", network)

                await event_bus.cache_set("metrics:job:influence:last_duration_ms", int(elapsed * 1000))
                await event_bus.cache_set("metrics:job:influence:last_run", utcnow_iso())
                await refresh_precomputed_views(event_bus)
            except Exception as exc:
                logger.warning("[Scheduler] Influence precompute failed: %s", exc)

    except Exception as exc:
        logger.error("[Scheduler] Influence scoring failed: %s", exc)


async def bot_score_job(
    session_factory,
    event_bus: Optional["EventBus"] = None,
) -> None:
    """
    Computes bot/inauthentic-behaviour scores for all active actors.
    Caches to Redis. Runs every 2 hours.
    """
    logger.info("[Scheduler] Bot score job starting")
    try:
        from storage.database import get_session
        from intelligence.bot_score import BotScorer

        async with get_session() as session:
            scorer = BotScorer(session)
            results = await scorer.score_actors(window_hours=24, min_events=3)

        if event_bus and results:
            payload = {
                "total": len(results),
                "high_risk": sum(1 for r in results if r.risk_level == "HIGH"),
                "actors": [
                    {
                        "actor_name": r.actor_name,
                        "actor_id": r.actor_id,
                        "bot_score": r.bot_score,
                        "risk_level": r.risk_level,
                        "signals": r.signals,
                        "event_count": r.event_count,
                        "sources": r.sources,
                        "computed_at": r.computed_at,
                    }
                    for r in results[:200]  # cap to 200 to keep Redis lean
                ],
                "generated_at": __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ).isoformat(),
            }
            await event_bus.cache_set("precomputed:bot_scores", payload, ttl_seconds=7200)
            logger.info("[Scheduler] Bot scores: %d actors scored, %d HIGH risk", len(results), payload["high_risk"])
    except Exception as exc:
        logger.error("[Scheduler] Bot score job failed: %s", exc)


async def escalation_job(
    session_factory,
    event_bus: Optional["EventBus"] = None,
) -> None:
    """
    Computes per-region escalation probability scores.
    Caches to Redis. Runs every 30 minutes.
    """
    logger.info("[Scheduler] Escalation scoring job starting")
    try:
        from storage.database import get_session
        from intelligence.escalation_scorer import EscalationScorer

        async with get_session() as session:
            scorer = EscalationScorer(session)
            scores = await scorer.score_all_regions(window_hours=6)

        if event_bus and scores:
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
            await event_bus.cache_set("precomputed:escalation_scores", payload, ttl_seconds=1800)
            # Notify the dashboard that cached intelligence changed.
            await event_bus.publish("intelligence_update", {"type": "escalation_update"})
            logger.info("[Scheduler] Escalation: %d regions scored", len(scores))
    except Exception as exc:
        logger.error("[Scheduler] Escalation job failed: %s", exc)


async def credibility_job(
    event_bus: Optional["EventBus"] = None,
) -> None:
    """
    Computes source credibility scores with exponential decay.
    Caches to Redis. Runs every 6 hours.
    """
    logger.info("[Scheduler] Credibility scoring job starting")
    try:
        from storage.database import get_session
        from intelligence.credibility import CredibilityTracker

        async with get_session() as session:
            tracker = CredibilityTracker(session)
            scores = await tracker.compute_all()

        if event_bus and scores:
            payload = {
                "sources": [
                    {
                        "source_key": s.source_key,
                        "display_name": s.display_name,
                        "credibility_score": s.credibility_score,
                        "tier": s.tier,
                        "penalty_count": s.penalty_count,
                        "boost_count": s.boost_count,
                        "last_computed": s.last_computed,
                    }
                    for s in scores
                ],
                "generated_at": __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ).isoformat(),
            }
            await event_bus.cache_set("precomputed:source_credibility", payload, ttl_seconds=21600)
            logger.info("[Scheduler] Credibility: %d sources scored", len(scores))
    except Exception as exc:
        logger.error("[Scheduler] Credibility job failed: %s", exc)

def create_scheduler(
    orchestrator: "Orchestrator",
    enricher:     "Enricher",
    nlp:          "NLPPipeline",
    graph:        "GraphDB",
    event_bus:    Optional["EventBus"] = None,
    embedder:     Optional["EmbeddingService"] = None,
    swarm:        Optional[Any] = None,
) -> AsyncIOScheduler:
    """Builds the APScheduler instance used by the API process."""
    scheduler = AsyncIOScheduler(timezone="UTC")

    # Live sources.
    scheduler.add_job(
        live_ingest_job,
        trigger    = IntervalTrigger(seconds=settings.live_interval_seconds),
        id         = "live_ingest",
        name       = "Live data ingest (USGS, OpenSky, Stocks)",
        kwargs     = dict(
            orchestrator = orchestrator,
            enricher     = enricher,
            nlp          = nlp,
            repo         = None,
            graph        = graph,
            event_bus    = event_bus,
            embedder     = embedder,
        ),
        replace_existing    = True,
        misfire_grace_time  = 60,
        max_instances       = 1,
        coalesce            = True,
    )

    # Text sources.
    scheduler.add_job(
        text_ingest_job,
        trigger    = IntervalTrigger(seconds=settings.text_interval_seconds),
        id         = "text_ingest",
        name       = "Text data ingest (News, Socials, RSS, HN, Telegram, WHO)",
        kwargs     = dict(
            orchestrator = orchestrator,
            enricher     = enricher,
            nlp          = nlp,
            graph        = graph,
            event_bus    = event_bus,
            embedder     = embedder,
        ),
        replace_existing    = True,
        misfire_grace_time  = 300,
        max_instances       = 1,
        coalesce            = True,
    )

    # Narrative detection.
    scheduler.add_job(
        narrative_detection_job,
        trigger    = IntervalTrigger(seconds=settings.narrative_interval_seconds),
        id         = "narrative_detection",
        name       = "Narrative & forced narrative detection",
        kwargs     = dict(graph=graph, event_bus=event_bus),
        replace_existing    = True,
        misfire_grace_time  = 120,
        max_instances       = 1,
        coalesce            = True,
    )

    # Anomaly scan.
    scheduler.add_job(
        anomaly_scan_job,
        trigger    = IntervalTrigger(seconds=settings.anomaly_interval_seconds),
        id         = "anomaly_scan",
        name       = "Anomaly & statistical spike detection",
        kwargs     = dict(event_bus=event_bus, swarm=swarm),
        replace_existing    = True,
        misfire_grace_time  = 180,
        max_instances       = 1,
        coalesce            = True,
    )

    # Correlation and composite events.
    scheduler.add_job(
        correlation_job,
        trigger    = IntervalTrigger(seconds=settings.live_interval_seconds),
        id         = "correlation",
        name       = "Signal correlation & composite event detection",
        kwargs     = dict(graph=graph, event_bus=event_bus),
        replace_existing    = True,
        misfire_grace_time  = 120,
        max_instances       = 1,
        coalesce            = True,
    )

    # Influence scoring.
    scheduler.add_job(
        influence_update_job,
        trigger    = IntervalTrigger(seconds=settings.influence_interval_seconds),
        id         = "influence_update",
        name       = "Actor influence score computation",
        kwargs     = dict(graph=graph, event_bus=event_bus),
        replace_existing    = True,
        misfire_grace_time  = 300,
        max_instances       = 1,
        coalesce            = True,
    )

    # Bot scoring.
    scheduler.add_job(
        bot_score_job,
        trigger    = IntervalTrigger(hours=2),
        id         = "bot_score",
        name       = "Inauthentic-behaviour / bot score computation",
        kwargs     = dict(session_factory=None, event_bus=event_bus),
        replace_existing    = True,
        misfire_grace_time  = 600,
        max_instances       = 1,
        coalesce            = True,
    )

    # Escalation scoring.
    scheduler.add_job(
        escalation_job,
        trigger    = IntervalTrigger(minutes=30),
        id         = "escalation_scoring",
        name       = "Per-region escalation probability scoring",
        kwargs     = dict(session_factory=None, event_bus=event_bus),
        replace_existing    = True,
        misfire_grace_time  = 300,
        max_instances       = 1,
        coalesce            = True,
    )

    # Source credibility.
    scheduler.add_job(
        credibility_job,
        trigger    = IntervalTrigger(hours=6),
        id         = "credibility_scoring",
        name       = "Source credibility decay scoring",
        kwargs     = dict(event_bus=event_bus),
        replace_existing    = True,
        misfire_grace_time  = 600,
        max_instances       = 1,
        coalesce            = True,
    )

    logger.info(
        "Scheduler configured: live every %ds, text every %ds, narrative every %ds, "
        "anomaly every %ds, influence every %ds + bot/escalation/credibility jobs",
        settings.live_interval_seconds,
        settings.text_interval_seconds,
        settings.narrative_interval_seconds,
        settings.anomaly_interval_seconds,
        settings.influence_interval_seconds,
    )
    return scheduler



