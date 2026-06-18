"""
intelligence/anomaly_detector.py
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Statistical anomaly detection over ingested event streams.

Monitors three dimensions for anomalous activity:
  1. Entity Frequency Anomaly   â€” actor/topic appears at unusual rate
  2. Geographic Cluster Anomaly â€” unusual concentration of events in a region
  3. Source Silencing Anomaly   â€” a normally active source goes quiet

Uses a simple but effective online z-score method with a rolling 7-day
per-entity hourly baseline stored in PostgreSQL.

All detection happens asynchronously, purely from PostgreSQL data.

Usage:
    detector = AnomalyDetector(session)
    alerts   = await detector.scan(window_hours=1)
"""

import logging
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from storage.database import AlertModel, EventModel

logger = logging.getLogger("vision_i.intelligence.anomaly_detector")
_Z_THRESHOLD_HIGH     = 3.0   # Anomaly severity: high
_Z_THRESHOLD_MEDIUM   = 2.0   # Anomaly severity: medium
_MIN_BASELINE_EVENTS  = 10    # Minimum historical events needed for z-score
_GEO_CLUSTER_RADIUS   = 5.0   # Degrees lat/lon for geographic clustering
_SOURCE_SILENCE_RATIO = 0.2   # Source is silent if below 20% of its baseline rate


class AnomalyAlert:
    """Represents a single anomaly alert."""

    def __init__(
        self,
        alert_type:  str,      # entity_spike | geographic_cluster | source_silence | sentiment_deterioration
        severity:    str,      # low | medium | high | critical
        title:       str,
        description: str,
        entity:      Optional[str],
        entity_type: Optional[str],
        event_count: int,
        baseline:    float,
        z_score:     float,
        sources:     List[str],
        location:    Optional[str],
        metadata:    Dict[str, Any],
    ) -> None:
        self.alert_type  = alert_type
        self.severity    = severity
        self.title       = title
        self.description = description
        self.entity      = entity
        self.entity_type = entity_type
        self.event_count = event_count
        self.baseline    = round(baseline, 2)
        self.z_score     = round(z_score, 2)
        self.sources     = sources
        self.location    = location
        self.metadata    = metadata
        self.detected_at = datetime.now(timezone.utc)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "alert_type":   self.alert_type,
            "severity":     self.severity,
            "title":        self.title,
            "description":  self.description,
            "entity":       self.entity,
            "entity_type":  self.entity_type,
            "event_count":  self.event_count,
            "baseline":     self.baseline,
            "z_score":      self.z_score,
            "sources":      self.sources,
            "location":     self.location,
            "metadata":     self.metadata,
            "detected_at":  self.detected_at.isoformat() + "Z",
        }


class AnomalyDetector:
    """
    Runs anomaly detection over the PostgreSQL event store.

    One instance lives on app.state. All methods are stateless between calls.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def scan(
        self,
        window_hours:   int   = 1,
        baseline_days:  int   = 7,
        top_k:          int   = 50,
    ) -> List[AnomalyAlert]:
        """
        Run all anomaly algorithms and return a list of alerts.

        params:
            window_hours  â€” current observation window (e.g. last 1 hour)
            baseline_days â€” how many prior days to use as baseline
            top_k         â€” max alerts to return
        """
        now           = datetime.now(timezone.utc)
        window_end    = now
        window_start  = now - timedelta(hours=window_hours)
        baseline_start = now - timedelta(days=baseline_days)

        alerts: List[AnomalyAlert] = []

        try:
            alerts.extend(await self._detect_entity_spikes(
                window_start, window_end, baseline_start, window_start,
                window_hours, baseline_days,
            ))
            alerts.extend(await self._detect_geographic_clusters(
                window_start, window_end, baseline_start, window_start,
            ))
            alerts.extend(await self._detect_source_silence(
                window_start, window_end, baseline_start, window_start,
                window_hours, baseline_days,
            ))
            alerts.extend(await self._detect_sentiment_shifts(
                window_start, window_end, baseline_start, window_start,
            ))

            alerts.sort(key=lambda a: (
                {"critical": 4, "high": 3, "medium": 2, "low": 1}.get(a.severity, 0),
                a.z_score,
            ), reverse=True)

            logger.info("AnomalyDetector: %d alerts generated", len(alerts))

        except Exception as exc:
            logger.error("AnomalyDetector.scan() failed: %s", exc)

        return alerts[:top_k]

    async def _detect_entity_spikes(
        self,
        window_start:   datetime,
        window_end:     datetime,
        baseline_start: datetime,
        baseline_end:   datetime,
        window_hours:   int,
        baseline_days:  int,
    ) -> List[AnomalyAlert]:
        alerts: List[AnomalyAlert] = []

        # Count entity mentions in current window
        recent_rows = (
            await self._session.execute(
                select(EventModel.actors, EventModel.source, EventModel.title)
                .where(and_(
                    EventModel.timestamp >= window_start,
                    EventModel.timestamp <= window_end,
                ))
            )
        ).fetchall()

        current_counts: Counter = Counter()
        entity_sources: Dict[str, set] = defaultdict(set)
        entity_titles:  Dict[str, List[str]] = defaultdict(list)

        for row in recent_rows:
            for actor in (row.actors or []):
                name = (actor.get("name") or "").strip()
                if name and len(name) > 2:
                    current_counts[name] += 1
                    entity_sources[name].add(row.source or "unknown")
                    if row.title:
                        entity_titles[name].append(row.title)

        # Count entity mentions in baseline period
        baseline_rows = (
            await self._session.execute(
                select(EventModel.actors)
                .where(and_(
                    EventModel.timestamp >= baseline_start,
                    EventModel.timestamp <= baseline_end,
                ))
            )
        ).fetchall()

        baseline_counts: Counter = Counter()
        for row in baseline_rows:
            for actor in (row.actors or []):
                name = (actor.get("name") or "").strip()
                if name and len(name) > 2:
                    baseline_counts[name] += 1

        # Normalise baseline to same time window length
        baseline_hours  = (baseline_end - baseline_start).total_seconds() / 3600
        normalise_factor = window_hours / max(baseline_hours, 1)

        for entity, count in current_counts.most_common(500):
            if count < 3:
                continue

            baseline_total    = baseline_counts.get(entity, 0)
            expected          = baseline_total * normalise_factor

            if baseline_total < _MIN_BASELINE_EVENTS and expected < 1:
                # Novel entity â€” use raw count as proxy
                z_score  = float(count) * 2.0
                severity = "medium" if count >= 5 else "low"
            else:
                std_dev  = math.sqrt(max(expected, 1.0))
                z_score  = (count - expected) / std_dev
                if z_score < _Z_THRESHOLD_MEDIUM:
                    continue
                severity = "critical" if z_score >= 5 else ("high" if z_score >= _Z_THRESHOLD_HIGH else "medium")

            alerts.append(AnomalyAlert(
                alert_type  = "entity_spike",
                severity    = severity,
                title       = f"Unusual activity: {entity}",
                description = (
                    f"'{entity}' mentioned {count}x in last {window_hours}h "
                    f"(baseline: {expected:.1f}x, z={z_score:.1f})"
                ),
                entity      = entity,
                entity_type = "PERSON_OR_ORG",
                event_count = count,
                baseline    = expected,
                z_score     = z_score,
                sources     = list(entity_sources[entity]),
                location    = None,
                metadata    = {
                    "sample_titles":  entity_titles[entity][:3],
                    "baseline_total": baseline_total,
                },
            ))

        return alerts

    async def _detect_sentiment_shifts(
        self,
        window_start: datetime,
        window_end: datetime,
        baseline_start: datetime,
        baseline_end: datetime,
    ) -> List[AnomalyAlert]:
        alerts: List[AnomalyAlert] = []

        current_rows = (
            await self._session.execute(
                select(
                    EventModel.location_name,
                    EventModel.sentiment_score,
                    EventModel.source,
                    EventModel.title,
                )
                .where(and_(
                    EventModel.timestamp >= window_start,
                    EventModel.timestamp <= window_end,
                    EventModel.sentiment_score.isnot(None),
                ))
            )
        ).fetchall()

        baseline_rows = (
            await self._session.execute(
                select(
                    EventModel.location_name,
                    EventModel.sentiment_score,
                )
                .where(and_(
                    EventModel.timestamp >= baseline_start,
                    EventModel.timestamp <= baseline_end,
                    EventModel.sentiment_score.isnot(None),
                ))
            )
        ).fetchall()

        current_scores: Dict[str, List[float]] = defaultdict(list)
        baseline_scores: Dict[str, List[float]] = defaultdict(list)
        current_sources: Dict[str, set[str]] = defaultdict(set)
        current_titles: Dict[str, List[str]] = defaultdict(list)

        for row in current_rows:
            region = (row.location_name or "Unspecified").strip()
            current_scores[region].append(float(row.sentiment_score))
            if row.source:
                current_sources[region].add(row.source)
            if row.title:
                current_titles[region].append(row.title)

        for row in baseline_rows:
            region = (row.location_name or "Unspecified").strip()
            baseline_scores[region].append(float(row.sentiment_score))

        for region, scores in current_scores.items():
            if len(scores) < 4 or len(baseline_scores.get(region, [])) < 8:
                continue

            current_avg = sum(scores) / len(scores)
            baseline_avg = sum(baseline_scores[region]) / len(baseline_scores[region])
            delta = baseline_avg - current_avg
            if delta < 0.22:
                continue

            severity = "critical" if delta >= 0.4 else ("high" if delta >= 0.3 else "medium")
            alerts.append(AnomalyAlert(
                alert_type="sentiment_deterioration",
                severity=severity,
                title=f"Sentiment deterioration: {region}",
                description=(
                    f"Average sentiment in {region} deteriorated from {baseline_avg:.2f} "
                    f"to {current_avg:.2f} in the active window."
                ),
                entity=region,
                entity_type="LOCATION",
                event_count=len(scores),
                baseline=baseline_avg,
                z_score=delta * 10,
                sources=sorted(current_sources[region]),
                location=region,
                metadata={
                    "current_avg": round(current_avg, 4),
                    "baseline_avg": round(baseline_avg, 4),
                    "delta": round(delta, 4),
                    "sample_titles": current_titles[region][:3],
                },
            ))

        return alerts

    async def _detect_geographic_clusters(
        self,
        window_start:   datetime,
        window_end:     datetime,
        baseline_start: datetime,
        baseline_end:   datetime,
    ) -> List[AnomalyAlert]:
        alerts: List[AnomalyAlert] = []

        # Fetch geolocated events in current window
        geo_rows = (
            await self._session.execute(
                select(
                    EventModel.location_lat,
                    EventModel.location_lon,
                    EventModel.location_name,
                    EventModel.title,
                    EventModel.source,
                )
                .where(and_(
                    EventModel.timestamp >= window_start,
                    EventModel.timestamp <= window_end,
                    EventModel.location_lat.isnot(None),
                    EventModel.location_lon.isnot(None),
                ))
            )
        ).fetchall()

        if len(geo_rows) < 5:
            return []

        # Simple grid-based clustering: bucket by rounded lat/lon (5Â° cells)
        cell_events: Dict[Tuple, List] = defaultdict(list)
        for row in geo_rows:
            cell = (
                round(row.location_lat / _GEO_CLUSTER_RADIUS) * _GEO_CLUSTER_RADIUS,
                round(row.location_lon / _GEO_CLUSTER_RADIUS) * _GEO_CLUSTER_RADIUS,
            )
            cell_events[cell].append(row)

        # Fetch baseline counts per cell
        baseline_geo = (
            await self._session.execute(
                select(EventModel.location_lat, EventModel.location_lon)
                .where(and_(
                    EventModel.timestamp >= baseline_start,
                    EventModel.timestamp <= baseline_end,
                    EventModel.location_lat.isnot(None),
                    EventModel.location_lon.isnot(None),
                ))
            )
        ).fetchall()

        baseline_cells: Counter = Counter()
        for row in baseline_geo:
            cell = (
                round(row.location_lat / _GEO_CLUSTER_RADIUS) * _GEO_CLUSTER_RADIUS,
                round(row.location_lon / _GEO_CLUSTER_RADIUS) * _GEO_CLUSTER_RADIUS,
            )
            baseline_cells[cell] += 1

        baseline_hours = (baseline_end - baseline_start).total_seconds() / 3600
        window_hours   = (window_end - window_start).total_seconds() / 3600
        nf             = window_hours / max(baseline_hours, 1)

        for cell, evs in cell_events.items():
            if len(evs) < 3:
                continue

            expected  = baseline_cells.get(cell, 0) * nf
            count     = len(evs)
            std_dev   = math.sqrt(max(expected, 1.0))
            z_score   = (count - expected) / std_dev

            if z_score < _Z_THRESHOLD_MEDIUM:
                continue

            location_name = (
                next((r.location_name for r in evs if r.location_name), None)
                or f"({cell[0]:.0f}Â°, {cell[1]:.0f}Â°)"
            )
            severity = "high" if z_score >= _Z_THRESHOLD_HIGH else "medium"
            sources  = list({r.source for r in evs if r.source})

            alerts.append(AnomalyAlert(
                alert_type  = "geographic_cluster",
                severity    = severity,
                title       = f"Geographic cluster: {location_name}",
                description = (
                    f"{count} events near {location_name} in last "
                    f"{window_hours:.0f}h (baseline: {expected:.1f}, z={z_score:.1f})"
                ),
                entity      = None,
                entity_type = "LOCATION",
                event_count = count,
                baseline    = expected,
                z_score     = z_score,
                sources     = sources,
                location    = location_name,
                metadata    = {
                    "cell_lat":       cell[0],
                    "cell_lon":       cell[1],
                    "sample_titles":  [r.title for r in evs[:3]],
                },
            ))

        return alerts

    async def _detect_source_silence(
        self,
        window_start:   datetime,
        window_end:     datetime,
        baseline_start: datetime,
        baseline_end:   datetime,
        window_hours:   int,
        baseline_days:  int,
    ) -> List[AnomalyAlert]:
        """
        Flags when a normally active source has gone unusually quiet.
        Useful for detecting censorship, service outages, or strategic silence.
        """
        alerts: List[AnomalyAlert] = []

        # Count current events per source
        current_counts_rows = (
            await self._session.execute(
                select(EventModel.source, func.count().label("cnt"))
                .where(and_(
                    EventModel.timestamp >= window_start,
                    EventModel.timestamp <= window_end,
                ))
                .group_by(EventModel.source)
            )
        ).fetchall()
        current: Counter = Counter({r.source: r.cnt for r in current_counts_rows})

        # Baseline counts per source
        baseline_counts_rows = (
            await self._session.execute(
                select(EventModel.source, func.count().label("cnt"))
                .where(and_(
                    EventModel.timestamp >= baseline_start,
                    EventModel.timestamp <= baseline_end,
                ))
                .group_by(EventModel.source)
            )
        ).fetchall()
        baseline: Counter = Counter({r.source: r.cnt for r in baseline_counts_rows})

        baseline_hours  = (baseline_end - baseline_start).total_seconds() / 3600
        normalise_factor = window_hours / max(baseline_hours, 1)

        for source, baseline_total in baseline.most_common():
            expected = baseline_total * normalise_factor
            if expected < 2:   # source was barely active anyway
                continue

            actual = current.get(source, 0)
            ratio  = actual / expected

            if ratio >= _SOURCE_SILENCE_RATIO:
                continue   # Still active enough

            silence_score = (1.0 - ratio)
            severity = "high" if actual == 0 else ("medium" if ratio < 0.1 else "low")

            alerts.append(AnomalyAlert(
                alert_type  = "source_silence",
                severity    = severity,
                title       = f"Source silence: {source}",
                description = (
                    f"'{source}' produced {actual} events in last {window_hours}h "
                    f"vs. expected {expected:.1f} ({ratio*100:.0f}% of baseline)"
                ),
                entity      = source,
                entity_type = "SOURCE",
                event_count = actual,
                baseline    = expected,
                z_score     = silence_score * 10,
                sources     = [source],
                location    = None,
                metadata    = {
                    "baseline_total": baseline_total,
                    "actual":         actual,
                    "ratio":          round(ratio, 4),
                    "completely_silent": actual == 0,
                },
            ))

        return alerts

