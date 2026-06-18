"""
intelligence/narrative_detector.py
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Detects coordinated narratives, forced narrative patterns, and information
operations across ingested event streams.

Four detection algorithms:
  1. Topic Velocity Spike
     A topic/entity appears in significantly more events than its rolling
     7-day hourly baseline.  Signal: score = (current_rate - baseline) / baseline

  2. Cross-Source Amplification
     The same talking point (similar title text) appears across 3+ distinct
     source types (e.g. GDELT + social + news + telegram) within a short window.
     Signal: source_diversity_score = unique_sources / total_sources

  3. Sentiment Divergence
     Mainstream media sentiment on a topic diverges from social media sentiment
     by more than SENTIMENT_DIVERGENCE_THRESHOLD.
     Signal: divergence_score = abs(mainstream_avg - social_avg)

  4. Actor Co-Activation
     Multiple actors that do not normally co-occur suddenly co-mention the same
     topic within a short window. Computed from Neo4j CO_MENTIONED_WITH baseline.

All algorithms operate purely on data already in PostgreSQL â€” no external calls.
Results are returned as NarrativeSignal objects and optionally persisted.

Usage:
    detector = NarrativeDetector(session, graph)
    signals  = await detector.detect(window_hours=6)
"""

import logging
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from storage.database import EventModel

logger = logging.getLogger("vision_i.intelligence.narrative_detector")

# Minimum events in window to bother analysing
_MIN_EVENTS_FOR_ANALYSIS = 5

# Minimum sources for cross-source amplification
_MIN_SOURCES_CROSS_AMP = 3

# Velocity: z-score threshold for "spike" classification
_VELOCITY_Z_THRESHOLD = 2.0

# Sentiment divergence threshold (0â€“1 scale)
_SENTIMENT_DIVERGENCE_THRESHOLD = 0.35

# Social sources for divergence detection
_SOCIAL_SOURCES = {"reddit", "youtube", "telegram", "hackernews"}
_NEWS_SOURCES   = {"newsapi", "rss_bbc_world", "rss_reuters", "rss_ap_news",
                   "rss_al_jazeera", "rss_xinhua", "rss_cnbc_world",
                   "rss_bloomberg_mrkts", "rss_bellingcat", "rss_crisis_group"}

class NarrativeSignal:
    """Represents a single detected narrative signal."""

    def __init__(
        self,
        signal_type:   str,         # velocity_spike | cross_source_amp | sentiment_divergence | actor_coactivation
        topic:         str,         # the topic/entity/keyword
        strength:      float,       # 0.0 â€“ 1.0 normalised signal strength
        confidence:    float,       # 0.0 â€“ 1.0 statistical confidence
        event_count:   int,
        source_count:  int,
        sources:       List[str],
        actors:        List[str],
        sample_titles: List[str],
        window_start:  datetime,
        window_end:    datetime,
        metadata:      Dict[str, Any],
    ) -> None:
        self.signal_type  = signal_type
        self.topic        = topic
        self.strength     = round(strength, 4)
        self.confidence   = round(confidence, 4)
        self.event_count  = event_count
        self.source_count = source_count
        self.sources      = sources
        self.actors       = actors
        self.sample_titles = sample_titles[:5]
        self.window_start = window_start
        self.window_end   = window_end
        self.metadata     = metadata
        self.detected_at  = datetime.now(timezone.utc)
        # Region (location_name | country code) â†’ percentage of events in this signal
        self.geographic_spread: Dict[str, float] = {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "signal_type":         self.signal_type,
            "topic":               self.topic,
            "strength":            self.strength,
            "confidence":          self.confidence,
            "event_count":         self.event_count,
            "source_count":        self.source_count,
            "sources":             self.sources,
            "actors":              self.actors,
            "sample_titles":       self.sample_titles,
            "window_start":        self.window_start.isoformat() + "Z",
            "window_end":          self.window_end.isoformat() + "Z",
            "detected_at":         self.detected_at.isoformat() + "Z",
            "metadata":            self.metadata,
            "geographic_spread":   self.geographic_spread,
        }

    @property
    def severity(self) -> str:
        if self.strength >= 0.75:
            return "critical"
        elif self.strength >= 0.5:
            return "high"
        elif self.strength >= 0.25:
            return "medium"
        return "low"

class NarrativeDetector:
    """
    Reads from PostgreSQL and returns a list of NarrativeSignal objects.

    One instance lives on app.state; all methods are stateless between calls.
    """

    def __init__(self, session: AsyncSession, graph=None) -> None:
        self._session = session
        self._graph   = graph   # optional GraphDB instance

    async def detect(
        self,
        window_hours:     int   = 6,
        baseline_days:    int   = 7,
        min_events:       int   = _MIN_EVENTS_FOR_ANALYSIS,
        top_k:            int   = 20,
    ) -> List[NarrativeSignal]:
        """
        Run all detection algorithms and return merged, deduplicated signals.

        params:
            window_hours   â€” analysis window (recent N hours)
            baseline_days  â€” historical baseline (prior N days)
            min_events     â€” skip topics with fewer events than this
            top_k          â€” max signals to return per algorithm
        """
        now        = datetime.now(timezone.utc)
        window_end = now
        window_start = now - timedelta(hours=window_hours)
        baseline_start = now - timedelta(days=baseline_days)

        signals: List[NarrativeSignal] = []

        try:
            recent_events = await self._fetch_events(window_start, window_end)
            if len(recent_events) < min_events:
                logger.info("NarrativeDetector: only %d events in window â€” skipping", len(recent_events))
                return []

            # 1. Velocity spike detection
            signals.extend(await self._detect_velocity_spikes(
                recent_events, window_start, window_end,
                baseline_start, now, top_k,
            ))

            # 2. Cross-source amplification
            signals.extend(self._detect_cross_source_amplification(
                recent_events, window_start, window_end, top_k,
            ))

            # 3. Sentiment divergence
            signals.extend(self._detect_sentiment_divergence(
                recent_events, window_start, window_end, top_k,
            ))

            # 4. Actor co-activation
            signals.extend(await self._detect_actor_coactivation(
                recent_events, window_start, window_end,
                baseline_start, now, top_k,
            ))

            # Deduplicate signals with same topic+type
            signals = self._deduplicate_signals(signals)

            # Sort by strength descending
            signals.sort(key=lambda s: s.strength, reverse=True)

            logger.info(
                "NarrativeDetector: %d signals detected (%s window, %d events)",
                len(signals), f"{window_hours}h", len(recent_events),
            )

        except Exception as exc:
            logger.error("NarrativeDetector.detect() failed: %s", exc)

        return signals[:top_k]

    async def _fetch_events(
        self,
        from_time: datetime,
        to_time:   datetime,
    ) -> List[Dict]:
        """Fetch events from PostgreSQL for a time range."""
        rows = (
            await self._session.execute(
                select(
                    EventModel.event_id,
                    EventModel.title,
                    EventModel.source,
                    EventModel.event_type,
                    EventModel.timestamp,
                    EventModel.sentiment_label,
                    EventModel.sentiment_score,
                    EventModel.actors,
                    EventModel.tags,
                    EventModel.location_name,
                )
                .where(and_(
                    EventModel.timestamp >= from_time,
                    EventModel.timestamp <= to_time,
                ))
                .order_by(EventModel.timestamp.desc())
                .limit(5000)
            )
        ).fetchall()

        return [
            {
                "event_id":        r.event_id,
                "title":           r.title or "",
                "source":          r.source or "",
                "event_type":      r.event_type or "news",
                "timestamp":       r.timestamp,
                "sentiment_label": r.sentiment_label,
                "sentiment_score": r.sentiment_score,
                "actors":          r.actors or [],
                "tags":            r.tags or [],
                "location_name":   r.location_name,
            }
            for r in rows
        ]

    async def _detect_velocity_spikes(
        self,
        recent_events: List[Dict],
        window_start:  datetime,
        window_end:    datetime,
        baseline_start: datetime,
        baseline_end:   datetime,
        top_k: int,
    ) -> List[NarrativeSignal]:
        """
        Computes per-entity mention velocity vs. 7-day baseline.
        Returns signals where z-score > _VELOCITY_Z_THRESHOLD.
        """
        signals: List[NarrativeSignal] = []

        # Count entity mentions in recent window
        recent_counts: Counter = Counter()
        entity_events: Dict[str, List[Dict]] = defaultdict(list)

        for ev in recent_events:
            for actor in ev["actors"]:
                name = (actor.get("name") or "").strip()
                if name and len(name) > 2:
                    recent_counts[name] += 1
                    entity_events[name].append(ev)
            for tag in ev["tags"]:
                if tag and len(tag) > 3:
                    tag_key = f"#{tag}"
                    recent_counts[tag_key] += 1
                    entity_events[tag_key].append(ev)

        if not recent_counts:
            return []

        # Fetch baseline counts from DB (prior N days, same hour-window length)
        window_hours = (window_end - window_start).total_seconds() / 3600
        baseline_window_hours = (baseline_end - baseline_start).total_seconds() / 3600
        baseline_periods = baseline_window_hours / window_hours  # how many windows fit in baseline

        # For speed, use the raw events approach rather than N DB queries
        baseline_events = await self._fetch_events(baseline_start, window_start)

        baseline_counts: Counter = Counter()
        for ev in baseline_events:
            for actor in ev["actors"]:
                name = (actor.get("name") or "").strip()
                if name and len(name) > 2:
                    baseline_counts[name] += 1
            for tag in ev["tags"]:
                if tag and len(tag) > 3:
                    baseline_counts[f"#{tag}"] += 1

        # Compute z-scores
        for entity, recent_count in recent_counts.most_common(200):
            if recent_count < 3:
                continue

            baseline_total = baseline_counts.get(entity, 0)
            # Expected count per window = baseline_total / baseline_periods
            expected = baseline_total / max(baseline_periods, 1)

            if expected < 1:
                # Never seen before â€” treat as novel emergence
                z_score = min(recent_count * 2.0, 10.0)
                novelty = True
            else:
                # Poisson-based z-score approximation
                std_dev  = math.sqrt(expected)
                z_score  = (recent_count - expected) / max(std_dev, 0.5)
                novelty  = False

            if z_score < _VELOCITY_Z_THRESHOLD:
                continue

            evs = entity_events[entity]
            sources = list({e["source"] for e in evs})
            actors_seen = list({
                a.get("name", "") for e in evs
                for a in e.get("actors", [])
                if a.get("name") and a.get("name") != entity
            })[:10]

            strength   = min(z_score / 10.0, 1.0)
            confidence = min(recent_count / 20.0, 1.0) if not novelty else min(recent_count / 10.0, 0.9)

            sig = NarrativeSignal(
                signal_type  = "velocity_spike",
                topic        = entity,
                strength     = strength,
                confidence   = confidence,
                event_count  = recent_count,
                source_count = len(sources),
                sources      = sources,
                actors       = actors_seen,
                sample_titles= [e["title"] for e in evs[:5]],
                window_start = window_start,
                window_end   = window_end,
                metadata     = {
                    "z_score":         round(z_score, 2),
                    "expected_count":  round(expected, 1),
                    "baseline_count":  baseline_total,
                    "novelty":         novelty,
                },
            )
            sig.geographic_spread = self._compute_geo_spread(evs)
            signals.append(sig)

        signals.sort(key=lambda s: s.strength, reverse=True)
        return signals[:top_k]

    def _detect_cross_source_amplification(
        self,
        recent_events: List[Dict],
        window_start:  datetime,
        window_end:    datetime,
        top_k: int,
    ) -> List[NarrativeSignal]:
        """
        Groups events with similar titles and checks if they appear across
        diverse source *types* (news + social + rss + telegram, etc.).

        Two events are "similar" if they share â‰¥2 significant n-grams.
        """
        signals: List[NarrativeSignal] = []
        if not recent_events:
            return []

        # Extract bigrams from titles for similarity grouping
        def bigrams(text: str) -> set:
            words = re.findall(r"\b[a-z]{3,}\b", text.lower())
            # Filter stopwords
            sw = {"the", "and", "for", "that", "with", "this", "from", "are", "was", "has"}
            words = [w for w in words if w not in sw]
            return {f"{words[i]}_{words[i+1]}" for i in range(len(words) - 1)}

        # Cluster events by shared bigrams
        clusters: Dict[str, List[Dict]] = defaultdict(list)
        for ev in recent_events:
            for bg in bigrams(ev["title"]):
                clusters[bg].append(ev)

        # Find clusters that span many source types
        for bigram, evs in clusters.items():
            if len(evs) < _MIN_EVENTS_FOR_ANALYSIS:
                continue

            # Classify each source as news/social/official/other
            def source_type(src: str) -> str:
                if src.startswith("rss_") or src == "newsapi":
                    return "news"
                if src in _SOCIAL_SOURCES:
                    return "social"
                if "state" in src or "un_" in src or "nato" in src:
                    return "official"
                return "other"

            source_types = {source_type(e["source"]) for e in evs}
            unique_sources = {e["source"] for e in evs}

            if len(source_types) < 2:
                continue   # all from same type â€” not amplification

            diversity_score = len(source_types) / 4.0   # max 4 types
            freq_score = min(len(evs) / 20.0, 1.0)
            strength = (diversity_score * 0.6) + (freq_score * 0.4)

            if strength < 0.25:
                continue

            actors_seen = list({
                a.get("name", "") for e in evs
                for a in e.get("actors", [])
                if a.get("name")
            })[:8]

            topic = bigram.replace("_", " ").title()

            sig = NarrativeSignal(
                signal_type  = "cross_source_amplification",
                topic        = topic,
                strength     = strength,
                confidence   = min(len(evs) / 15.0, 1.0),
                event_count  = len(evs),
                source_count = len(unique_sources),
                sources      = list(unique_sources)[:10],
                actors       = actors_seen,
                sample_titles= [e["title"] for e in evs[:5]],
                window_start = window_start,
                window_end   = window_end,
                metadata     = {
                    "source_types":    list(source_types),
                    "diversity_score": round(diversity_score, 3),
                    "bigram_key":      bigram,
                },
            )
            sig.geographic_spread = self._compute_geo_spread(evs)
            signals.append(sig)

        signals.sort(key=lambda s: s.strength, reverse=True)
        return signals[:top_k]

    def _detect_sentiment_divergence(
        self,
        recent_events: List[Dict],
        window_start:  datetime,
        window_end:    datetime,
        top_k: int,
    ) -> List[NarrativeSignal]:
        """
        Detects topics where mainstream media sentiment diverges from social
        media sentiment â€” a marker of potential narrative manipulation.
        """
        signals: List[NarrativeSignal] = []
        if not recent_events:
            return []

        # Group by actor/entity
        entity_scores: Dict[str, Dict[str, List[float]]] = defaultdict(
            lambda: {"news": [], "social": []}
        )

        for ev in recent_events:
            score = ev.get("sentiment_score")
            if score is None:
                continue

            source_type = "social" if ev["source"] in _SOCIAL_SOURCES else "news"
            for actor in ev["actors"]:
                name = (actor.get("name") or "").strip()
                if name and len(name) > 2:
                    entity_scores[name][source_type].append(score)

        for entity, buckets in entity_scores.items():
            news_scores   = buckets["news"]
            social_scores = buckets["social"]

            if len(news_scores) < 3 or len(social_scores) < 3:
                continue

            news_avg   = sum(news_scores)   / len(news_scores)
            social_avg = sum(social_scores) / len(social_scores)
            divergence = abs(news_avg - social_avg)

            if divergence < _SENTIMENT_DIVERGENCE_THRESHOLD:
                continue

            evs_for_entity = [
                e for e in recent_events
                if any(a.get("name") == entity for a in e.get("actors", []))
            ]
            sources = list({e["source"] for e in evs_for_entity})

            strength   = min(divergence / 0.6, 1.0)   # 0.6 = max realistic divergence
            confidence = min((len(news_scores) + len(social_scores)) / 20.0, 1.0)

            # Direction: which side is negative?
            direction = "social_negative" if social_avg < news_avg else "news_negative"

            signals.append(NarrativeSignal(
                signal_type  = "sentiment_divergence",
                topic        = entity,
                strength     = strength,
                confidence   = confidence,
                event_count  = len(evs_for_entity),
                source_count = len(sources),
                sources      = sources,
                actors       = [entity],
                sample_titles= [e["title"] for e in evs_for_entity[:5]],
                window_start = window_start,
                window_end   = window_end,
                metadata     = {
                    "news_avg_sentiment":   round(news_avg, 4),
                    "social_avg_sentiment": round(social_avg, 4),
                    "divergence":           round(divergence, 4),
                    "direction":            direction,
                    "news_sample_count":    len(news_scores),
                    "social_sample_count":  len(social_scores),
                },
            ))

        signals.sort(key=lambda s: s.strength, reverse=True)
        return signals[:top_k]

    async def _detect_actor_coactivation(
        self,
        recent_events:  List[Dict],
        window_start:   datetime,
        window_end:     datetime,
        baseline_start: datetime,
        now:            datetime,
        top_k:          int,
    ) -> List["NarrativeSignal"]:
        """
        Detects actor pairs that suddenly co-mention a topic together more than
        their historical baseline — a marker of coordinated information operations.

        Uses PostgreSQL-only baseline (no Neo4j required). If Neo4j CO_MENTIONED_WITH
        edges are available via self._graph they take precedence as the baseline.
        """
        signals: List[NarrativeSignal] = []
        if not recent_events:
            return []

        # ── Build current-window pair co-occurrence ────────────────────────────
        pair_events: Dict[Tuple[str, str], List[Dict]] = defaultdict(list)
        for ev in recent_events:
            raw_actors = [
                (a.get("name") or a.get("display_name") or "").strip()
                for a in ev.get("actors", [])
            ]
            actors = list(dict.fromkeys(a for a in raw_actors if len(a) > 2))[:8]
            for i in range(len(actors)):
                for j in range(i + 1, len(actors)):
                    pair = (min(actors[i], actors[j]), max(actors[i], actors[j]))
                    pair_events[pair].append(ev)

        if not pair_events:
            return []

        # ── Build baseline pair counts ─────────────────────────────────────────
        window_hours   = max((window_end - window_start).total_seconds() / 3600, 1)
        baseline_hours = max((window_start - baseline_start).total_seconds() / 3600, 1)
        scale          = window_hours / baseline_hours   # normalise to same duration

        baseline_pair_counts: Dict[Tuple[str, str], int] = defaultdict(int)

        # Prefer Neo4j co-mention baseline when graph is available.
        graph_used = False
        if self._graph and getattr(self._graph, "available", False):
            try:
                for pair in pair_events:
                    actor_id = f"actor:{pair[0].lower().replace(' ', '_')}"
                    result   = self._graph.ego_graph(actor_id, depth=1, limit=50)
                    for edge in (result.get("edges") or []):
                        target_raw = edge.get("to", edge.get("target", ""))
                        target     = target_raw.replace("actor:", "").replace("_", " ")
                        rel_label  = edge.get("label", "").upper()
                        if rel_label in ("CO_MENTIONED_WITH", "RELATED_TO"):
                            weight = edge.get("weight", 1)
                            norm_pair = (
                                min(pair[0], target),
                                max(pair[0], target),
                            )
                            baseline_pair_counts[norm_pair] += int(weight)
                graph_used = True
            except Exception as _exc:
                logger.debug("Actor co-activation: graph query failed (%s) — using SQL baseline", _exc)

        # SQL baseline fallback (or supplement when graph had no data).
        if not graph_used:
            try:
                baseline_events = await self._fetch_events(baseline_start, window_start)
                for ev in baseline_events:
                    raw_actors = [
                        (a.get("name") or a.get("display_name") or "").strip()
                        for a in ev.get("actors", [])
                    ]
                    actors = list(dict.fromkeys(a for a in raw_actors if len(a) > 2))[:8]
                    for i in range(len(actors)):
                        for j in range(i + 1, len(actors)):
                            pair = (min(actors[i], actors[j]), max(actors[i], actors[j]))
                            baseline_pair_counts[pair] += 1
            except Exception as _exc:
                logger.warning("Actor co-activation: SQL baseline query failed: %s", _exc)

        # ── Z-score anomaly test per pair ──────────────────────────────────────
        for pair, evs in pair_events.items():
            count_now      = len(evs)
            count_baseline = baseline_pair_counts.get(pair, 0)
            expected       = count_baseline * scale

            if count_now < 3:
                continue

            if expected == 0:
                # Novel pair never seen in baseline — treat as elevated signal.
                z_score    = 3.0
                strength   = min(count_now / 10.0, 1.0)
                confidence = min(count_now / 8.0, 0.85)
            else:
                z_score = (count_now - expected) / max(math.sqrt(expected), 0.5)
                if z_score < _VELOCITY_Z_THRESHOLD:
                    continue
                strength   = min((z_score - _VELOCITY_Z_THRESHOLD) / 5.0, 1.0)
                confidence = min(count_now / 15.0, 0.85)

            actor_a, actor_b = pair
            topic   = f"{actor_a} ↔ {actor_b}"
            sources = list({e["source"] for e in evs})

            sig = NarrativeSignal(
                signal_type  = "actor_coactivation",
                topic        = topic,
                strength     = strength,
                confidence   = confidence,
                event_count  = count_now,
                source_count = len(sources),
                sources      = sources,
                actors       = list(pair),
                sample_titles= [e["title"] for e in evs[:5]],
                window_start = window_start,
                window_end   = window_end,
                metadata     = {
                    "actor_a":        actor_a,
                    "actor_b":        actor_b,
                    "count_window":   count_now,
                    "count_baseline": count_baseline,
                    "expected_rate":  round(expected, 2),
                    "z_score":        round(z_score, 2),
                    "novel":          count_baseline == 0,
                    "baseline_source": "neo4j" if graph_used else "sql",
                },
            )
            sig.geographic_spread = self._compute_geo_spread(evs)
            signals.append(sig)

        signals.sort(key=lambda s: s.strength, reverse=True)
        return signals[:top_k]

    @staticmethod
    def _compute_geo_spread(events: List[Dict]) -> Dict[str, float]:
        """
        Returns region â†’ percentage map (sums to 100). Buckets unknown locations
        as 'unknown'. Falls back to empty dict if no events.
        """
        if not events:
            return {}
        buckets: Counter = Counter()
        for ev in events:
            name = (ev.get("location_name") or "").strip() or "Unknown"
            buckets[name] += 1
        total = sum(buckets.values()) or 1
        # Keep top 8 regions, lump remainder into "Other"
        top = buckets.most_common(8)
        spread = {region: round(cnt * 100.0 / total, 1) for region, cnt in top}
        leftover = total - sum(cnt for _, cnt in top)
        if leftover > 0:
            spread["Other"] = round(leftover * 100.0 / total, 1)
        return spread

    @staticmethod
    def _deduplicate_signals(signals: List[NarrativeSignal]) -> List[NarrativeSignal]:
        """Keep highest-strength signal per (topic, signal_type) pair."""
        seen: Dict[str, NarrativeSignal] = {}
        for sig in signals:
            key = f"{sig.topic}::{sig.signal_type}"
            if key not in seen or sig.strength > seen[key].strength:
                seen[key] = sig
        return list(seen.values())

