"""
intelligence/composite_detector.py
────────────────────────────────────
Composite event detection from correlated signal clusters.

Takes SignalCluster objects from the CorrelationEngine and promotes
qualifying clusters into high-confidence composite events with:
  - confidence_score   (multi-factor weighted score)
  - supporting_signals (list of signal_ids that back this event)
  - reasoning          (human-readable explanation of detection)

Qualification thresholds:
  - min_signals >= 3   (at least 3 correlated signals)
  - min_sources >= 2   (from at least 2 distinct sources)
  - min_composite_score >= 0.6  (cluster correlation score)
"""

import logging
from typing import Any, Dict, List, TYPE_CHECKING

from config.settings import settings
from core.utils import stable_id, utcnow_iso
from intelligence.signal_processor import SOURCE_WEIGHTS

if TYPE_CHECKING:
    from intelligence.correlation_engine import SignalCluster

logger = logging.getLogger("vision_i.intelligence.composite_detector")


class CompositeEventDetector:
    """Promotes qualifying signal clusters into composite intelligence events."""

    def __init__(self) -> None:
        self._min_signals = settings.correlation_min_signals
        self._min_sources = settings.correlation_min_sources
        self._min_score = 0.6

    async def detect(self, clusters: List["SignalCluster"]) -> int:
        """
        Evaluate clusters and create composite events for qualifying ones.

        Returns the number of composite events created.
        """
        if not clusters:
            return 0

        qualifying = [c for c in clusters if self._qualifies(c)]
        if not qualifying:
            logger.info("Composite detector: no qualifying clusters (of %d)", len(clusters))
            return 0

        logger.info(
            "Composite detector: %d of %d clusters qualify",
            len(qualifying), len(clusters),
        )

        events = []
        for cluster in qualifying:
            event = self._build_event(cluster)
            if event:
                events.append(event)

        if not events:
            return 0

        # Persist composite events
        from storage.database import get_session
        from storage.event_repo import EventRepository
        async with get_session() as session:
            repo = EventRepository(session)
            count = await repo.upsert_many(events)

        logger.info("Composite detector: created %d composite events", count)
        return count

    def _qualifies(self, cluster: "SignalCluster") -> bool:
        """Check if a cluster meets the thresholds for composite event creation."""
        if len(cluster.signal_ids) < self._min_signals:
            return False
        if len(cluster.sources) < self._min_sources:
            return False
        if cluster.scores.get("composite", 0) < self._min_score:
            return False
        return True

    def _build_event(self, cluster: "SignalCluster") -> Dict[str, Any]:
        """Build a VisionEvent dict from a qualifying cluster."""
        rep = cluster.representative_signal
        signal_count = len(cluster.signal_ids)
        sources = cluster.sources

        # Confidence scoring — four weighted factors
        source_diversity = min(len(sources) / 5.0, 1.0)  # 5+ sources = max

        source_quality = 0.5
        weights = [SOURCE_WEIGHTS.get(s, 0.5) for s in sources]
        if weights:
            source_quality = sum(weights) / len(weights)

        agreement = cluster.scores.get("semantic", 0.0)
        volume = min(signal_count / 10.0, 1.0)

        confidence_score = (
            0.30 * source_diversity
            + 0.25 * source_quality
            + 0.25 * agreement
            + 0.20 * volume
        )
        confidence_score = round(min(max(confidence_score, 0.0), 1.0), 3)

        # Reasoning text
        shared_str = ", ".join(cluster.shared_actors[:5]) if cluster.shared_actors else "none"
        reasoning = (
            f"Detected from {signal_count} signals across {len(sources)} sources "
            f"({', '.join(sources)}). "
            f"Shared entities: [{shared_str}]. "
            f"Average semantic similarity: {cluster.scores.get('semantic', 0):.2f}. "
            f"Time span: {cluster.time_span_hours:.1f} hours. "
            f"Source quality: {source_quality:.2f}."
        )

        # Determine event type from dominant type in signals
        type_counts: Dict[str, int] = {}
        for sig in cluster.signals:
            et = (sig.get("meta") or {}).get("event_type", "unknown")
            type_counts[et] = type_counts.get(et, 0) + 1
        event_type = max(type_counts, key=type_counts.get) if type_counts else "composite"

        title = rep.get("title", "Composite Event")
        if cluster.shared_actors:
            title = f"[Composite] {cluster.shared_actors[0]}: {title}"
        else:
            title = f"[Composite] {title}"

        # Build a stable event_id from cluster signals
        event_id = stable_id("composite", cluster.cluster_id)

        return {
            "event_id":           event_id,
            "source":             "composite",
            "source_id":          cluster.cluster_id,
            "event_type":         event_type,
            "title":              title[:500],
            "description":        reasoning,
            "body":               reasoning,
            "url":                rep.get("meta", {}).get("url"),
            "language":           "en",
            "timestamp":          rep.get("timestamp") or utcnow_iso(),
            "ingest_time":        utcnow_iso(),
            "sentiment_label":    None,
            "sentiment_score":    rep.get("sentiment_score"),
            "location_lat":       rep.get("location_lat"),
            "location_lon":       rep.get("location_lon"),
            "location_name":      rep.get("location_name"),
            "actors":             [{"name": a, "type": "UNKNOWN"} for a in cluster.shared_actors[:10]],
            "tags":               ["composite", "multi-source", event_type],
            "extras":             {
                "cluster_id":         cluster.cluster_id,
                "sources":            sources,
                "scores":             cluster.scores,
                "time_span_hours":    cluster.time_span_hours,
            },
            "confidence_score":    confidence_score,
            "supporting_signals":  cluster.signal_ids,
            "signal_count":        signal_count,
            "reasoning":           reasoning,
        }
