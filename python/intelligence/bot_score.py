"""
intelligence/bot_score.py
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Inauthentic-behaviour detection for actors observed in the event stream.

Six behavioural signals are computed entirely from the existing PostgreSQL
`events` table and combined into a single bot-probability score:

  1. posting_velocity_z    â€” events/hour vs. 7-day actor baseline (z-score)
  2. repetition_rate       â€” fraction of actor's events with near-identical
                             titles (SequenceMatcher ratio â‰¥ 0.80 similarity)
  3. cross_platform_sync   â€” actor appears on 3+ distinct source types inside
                             the same 1-hour bucket (coordination signal)
  4. source_diversity_low  â€” actor's events come from â‰¤ 2 distinct sources
                             (low diversity â†’ artificially narrow reach)
  5. off_hours_ratio       â€” fraction of events timestamped 02:00â€“05:00 UTC
                             (classic bot-farm shift indicator)
  6. propagation_speed     â€” minutes from actor's first mention to the 3rd
                             independent cross-platform copy (fast = suspicious)

Bot score = weighted sum of signals, clamped to [0.0, 1.0].
  Weights: velocity=0.25, repetition=0.20, sync=0.20, diversity=0.15,
           off_hours=0.10, propagation=0.10

Risk levels:  â‰¥ 0.70 â†’ HIGH  |  â‰¥ 0.40 â†’ MEDIUM  |  < 0.40 â†’ LOW

Usage:
    scorer  = BotScorer(session)
    results = await scorer.score_actors(window_hours=24, min_events=3)
"""

from __future__ import annotations

import difflib
import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from storage.database import EventModel

logger = logging.getLogger("vision_i.intelligence.bot_score")
_W_VELOCITY    = 0.25
_W_REPETITION  = 0.20
_W_SYNC        = 0.20
_W_DIVERSITY   = 0.15
_W_OFF_HOURS   = 0.10
_W_PROPAGATION = 0.10
_RISK_HIGH   = 0.70
_RISK_MEDIUM = 0.40

# Similarity threshold for "near-identical" title pairs
_TITLE_SIMILARITY_THRESHOLD = 0.80

# Off-hours UTC window (bot-farm shift)
_OFF_HOUR_START = 2   # 02:00 UTC
_OFF_HOUR_END   = 5   # 05:00 UTC (exclusive)

# Number of baseline days for velocity comparison
_BASELINE_DAYS = 7

# Source-type mapping for cross-platform sync detection
_SOURCE_TYPE_MAP: Dict[str, str] = {
    "newsapi":   "news",
    "gdelt":     "news",
    "reddit":    "social",
    "telegram":  "social",
    "hackernews":"forum",
    "youtube":   "video",
    "opensky":   "ais_flight",
    "ais":       "ais_flight",
}


def _source_type(source: str) -> str:
    """Classify a source key into a broad type bucket."""
    lower = (source or "").lower()
    for key, stype in _SOURCE_TYPE_MAP.items():
        if key in lower:
            return stype
    if lower.startswith("rss_"):
        return "rss"
    if "state" in lower or "gov" in lower or "nato" in lower or "un_" in lower:
        return "official"
    return f"other:{lower[:16]}"


def _similarity(a: str, b: str) -> float:
    """Return SequenceMatcher similarity ratio between two strings."""
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _repetition_rate(titles: List[str]) -> float:
    """
    Fraction of title pairs that are near-identical (similarity â‰¥ threshold).
    Returns 0.0 when there are fewer than 2 titles.
    """
    if len(titles) < 2:
        return 0.0
    total_pairs     = 0
    duplicate_pairs = 0
    for i in range(len(titles)):
        for j in range(i + 1, len(titles)):
            total_pairs += 1
            if _similarity(titles[i], titles[j]) >= _TITLE_SIMILARITY_THRESHOLD:
                duplicate_pairs += 1
    return duplicate_pairs / max(total_pairs, 1)


def _risk_level(score: float) -> str:
    if score >= _RISK_HIGH:
        return "HIGH"
    if score >= _RISK_MEDIUM:
        return "MEDIUM"
    return "LOW"

@dataclass
class BotScoreResult:
    actor_name:  str
    actor_id:    str
    bot_score:   float               # 0.0 â€“ 1.0
    risk_level:  str                 # LOW | MEDIUM | HIGH
    signals:     Dict[str, float]    # individual signal values (0.0 â€“ 1.0)
    event_count: int
    sources:     List[str]
    computed_at: str                 # ISO timestamp

    def to_dict(self) -> Dict:
        return {
            "actor_name":  self.actor_name,
            "actor_id":    self.actor_id,
            "bot_score":   round(self.bot_score, 4),
            "risk_level":  self.risk_level,
            "signals":     {k: round(v, 4) for k, v in self.signals.items()},
            "event_count": self.event_count,
            "sources":     self.sources,
            "computed_at": self.computed_at,
        }

class BotScorer:
    """
    Reads actor behaviour from PostgreSQL and returns BotScoreResult objects.

    One instance lives on app.state; all methods are stateless between calls.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def score_actors(
        self,
        window_hours: int = 24,
        min_events:   int = 3,
    ) -> List[BotScoreResult]:
        """
        Score all actors that appear at least `min_events` times within
        the last `window_hours` hours.

        Returns a list of BotScoreResult objects sorted by bot_score descending.
        """
        now          = datetime.now(timezone.utc)
        window_start = now - timedelta(hours=window_hours)
        baseline_start = now - timedelta(days=_BASELINE_DAYS)

        results: List[BotScoreResult] = []
        try:
            actor_events = await self._fetch_actor_events(window_start, now)
            baseline_map = await self._fetch_baseline_counts(baseline_start, window_start)

            for actor_name, evs in actor_events.items():
                if len(evs) < min_events:
                    continue
                result = self._compute_result(
                    actor_name, evs, baseline_map, window_hours,
                )
                results.append(result)

            results.sort(key=lambda r: r.bot_score, reverse=True)
            logger.info(
                "BotScorer.score_actors: scored %d actors (window=%dh, min=%d)",
                len(results), window_hours, min_events,
            )

        except Exception as exc:
            logger.error("BotScorer.score_actors() failed: %s", exc)

        return results

    async def score_actor(
        self,
        actor_name:   str,
        window_hours: int = 24,
    ) -> Optional[BotScoreResult]:
        """
        Score a single actor by name. Returns None if insufficient data.
        """
        now          = datetime.now(timezone.utc)
        window_start = now - timedelta(hours=window_hours)
        baseline_start = now - timedelta(days=_BASELINE_DAYS)

        try:
            actor_events = await self._fetch_actor_events(window_start, now, actor_name)
            evs = actor_events.get(actor_name)
            if not evs:
                logger.debug("BotScorer.score_actor: no events found for '%s'", actor_name)
                return None

            baseline_map = await self._fetch_baseline_counts(baseline_start, window_start, actor_name)
            return self._compute_result(actor_name, evs, baseline_map, window_hours)

        except Exception as exc:
            logger.error("BotScorer.score_actor(%r) failed: %s", actor_name, exc)
            return None

    async def _fetch_actor_events(
        self,
        from_time:    datetime,
        to_time:      datetime,
        actor_filter: Optional[str] = None,
    ) -> Dict[str, List[Dict]]:
        """
        Fetch events in the time window and group by actor name.
        Uses jsonb_array_elements to unnest the actors JSONB array in SQL.
        """
        # Pull all columns we need in one query; actor expansion done in Python
        # to stay compatible with any SQLAlchemy dialect nuance on Windows.
        rows = (
            await self._session.execute(
                select(
                    EventModel.event_id,
                    EventModel.title,
                    EventModel.source,
                    EventModel.timestamp,
                    EventModel.actors,
                    EventModel.tags,
                )
                .where(
                    EventModel.timestamp >= from_time,
                    EventModel.timestamp <= to_time,
                )
                .order_by(EventModel.timestamp.asc())
                .limit(50_000)
            )
        ).fetchall()

        actor_events: Dict[str, List[Dict]] = defaultdict(list)
        for row in rows:
            for actor in (row.actors or []):
                name = (actor.get("name") or "").strip()
                if not name or len(name) < 3:
                    continue
                if actor_filter and name.lower() != actor_filter.lower():
                    continue
                actor_events[name].append({
                    "event_id":  row.event_id,
                    "title":     row.title or "",
                    "source":    row.source or "",
                    "timestamp": row.timestamp,
                })
        return actor_events

    async def _fetch_baseline_counts(
        self,
        baseline_start: datetime,
        baseline_end:   datetime,
        actor_filter:   Optional[str] = None,
    ) -> Dict[str, int]:
        """
        Count actor appearances in the 7-day baseline window.
        Returns {actor_name: total_event_count}.
        """
        rows = (
            await self._session.execute(
                select(EventModel.actors)
                .where(
                    EventModel.timestamp >= baseline_start,
                    EventModel.timestamp <= baseline_end,
                )
                .limit(200_000)
            )
        ).fetchall()

        baseline: Dict[str, int] = defaultdict(int)
        for row in rows:
            for actor in (row.actors or []):
                name = (actor.get("name") or "").strip()
                if not name or len(name) < 3:
                    continue
                if actor_filter and name.lower() != actor_filter.lower():
                    continue
                baseline[name] += 1
        return baseline

    def _compute_result(
        self,
        actor_name:   str,
        evs:          List[Dict],
        baseline_map: Dict[str, int],
        window_hours: int,
    ) -> BotScoreResult:
        """Compute all six signals and combine into a BotScoreResult."""

        titles      = [e["title"] for e in evs]
        timestamps  = [e["timestamp"] for e in evs if e["timestamp"]]
        sources_raw = [e["source"] for e in evs]

        # 1. Posting velocity z-score (normalised to 0â€“1)
        sig_velocity = self._signal_velocity(
            len(evs), baseline_map.get(actor_name, 0), window_hours,
        )

        # 2. Repetition rate
        sig_repetition = _repetition_rate(titles)

        # 3. Cross-platform sync in 1-hour buckets
        sig_sync = self._signal_cross_platform_sync(evs)

        # 4. Source diversity (low diversity â†’ high score)
        distinct_sources = list(set(sources_raw))
        sig_diversity = self._signal_source_diversity_low(distinct_sources)

        # 5. Off-hours ratio
        sig_off_hours = self._signal_off_hours_ratio(timestamps)

        # 6. Propagation speed (minutes, normalised)
        sig_propagation = self._signal_propagation_speed(evs)

        signals = {
            "posting_velocity_z":   round(sig_velocity,    4),
            "repetition_rate":      round(sig_repetition,  4),
            "cross_platform_sync":  round(sig_sync,        4),
            "source_diversity_low": round(sig_diversity,   4),
            "off_hours_ratio":      round(sig_off_hours,   4),
            "propagation_speed":    round(sig_propagation, 4),
        }

        raw_score = (
            _W_VELOCITY    * sig_velocity    +
            _W_REPETITION  * sig_repetition  +
            _W_SYNC        * sig_sync        +
            _W_DIVERSITY   * sig_diversity   +
            _W_OFF_HOURS   * sig_off_hours   +
            _W_PROPAGATION * sig_propagation
        )
        bot_score = max(0.0, min(1.0, raw_score))

        return BotScoreResult(
            actor_name  = actor_name,
            actor_id    = f"actor:{actor_name.lower().replace(' ', '_')}",
            bot_score   = round(bot_score, 4),
            risk_level  = _risk_level(bot_score),
            signals     = signals,
            event_count = len(evs),
            sources     = distinct_sources[:20],
            computed_at = datetime.now(timezone.utc).isoformat() + "Z",
        )

    @staticmethod
    def _signal_velocity(
        current_count:  int,
        baseline_total: int,
        window_hours:   int,
    ) -> float:
        """
        Z-score of events/hour vs. 7-day hourly baseline, mapped to [0, 1].
        A z-score â‰¥ 5 maps to 1.0.
        """
        if window_hours <= 0:
            return 0.0
        current_rate   = current_count / max(window_hours, 1)
        baseline_hours = _BASELINE_DAYS * 24
        baseline_rate  = baseline_total / max(baseline_hours, 1)
        expected       = baseline_rate * window_hours
        std_dev        = math.sqrt(max(expected, 1.0))
        z_score        = (current_count - expected) / std_dev
        # Normalise: z=0 â†’ 0, zâ‰¥5 â†’ 1
        return max(0.0, min(1.0, z_score / 5.0))

    @staticmethod
    def _signal_cross_platform_sync(evs: List[Dict]) -> float:
        """
        Checks whether the actor appears on 3+ distinct source *types*
        within any single 1-hour bucket.  Returns 1.0 if true, 0.0 otherwise.
        """
        # Group events into 1-hour buckets by timestamp
        hourly_types: Dict[int, set] = defaultdict(set)
        for ev in evs:
            ts = ev.get("timestamp")
            if ts is None:
                continue
            # Floor to the hour epoch
            if hasattr(ts, "timestamp"):
                hour_key = int(ts.timestamp()) // 3600
            else:
                continue
            hourly_types[hour_key].add(_source_type(ev["source"]))

        for stype_set in hourly_types.values():
            if len(stype_set) >= 3:
                return 1.0
        return 0.0

    @staticmethod
    def _signal_source_diversity_low(distinct_sources: List[str]) -> float:
        """
        Returns a high score when the actor's events come from very few sources.
        â‰¤ 1 source â†’ 1.0, 2 sources â†’ 0.75, 3 â†’ 0.40, 4+ â†’ 0.0.
        """
        n = len(distinct_sources)
        if n <= 1:
            return 1.0
        if n == 2:
            return 0.75
        if n == 3:
            return 0.40
        return 0.0

    @staticmethod
    def _signal_off_hours_ratio(timestamps: List[datetime]) -> float:
        """Fraction of events timestamped in the 02:00â€“05:00 UTC window."""
        if not timestamps:
            return 0.0
        off_hours_count = sum(
            1 for ts in timestamps
            if _OFF_HOUR_START <= ts.hour < _OFF_HOUR_END
        )
        return off_hours_count / len(timestamps)

    @staticmethod
    def _signal_propagation_speed(evs: List[Dict]) -> float:
        """
        Time in minutes from actor's first mention to the 3rd independent
        cross-platform copy.  Fast propagation (< 60 min) â†’ close to 1.0.
        If 3+ cross-platform copies never occur, returns 0.0.
        """
        if len(evs) < 3:
            return 0.0

        sorted_evs = sorted(
            [e for e in evs if e.get("timestamp")],
            key=lambda e: e["timestamp"],
        )
        if len(sorted_evs) < 3:
            return 0.0

        first_ts = sorted_evs[0]["timestamp"]

        # Find first event that is on a DIFFERENT source type from the first
        seen_types = {_source_type(sorted_evs[0]["source"])}
        third_ts   = None
        for ev in sorted_evs[1:]:
            stype = _source_type(ev["source"])
            seen_types.add(stype)
            if len(seen_types) >= 3:
                third_ts = ev["timestamp"]
                break

        if third_ts is None:
            return 0.0

        delta_minutes = (third_ts - first_ts).total_seconds() / 60.0
        if delta_minutes <= 0:
            return 1.0

        # Normalise: 0 min â†’ 1.0, 60+ min â†’ 0.0
        return max(0.0, min(1.0, 1.0 - (delta_minutes / 60.0)))

