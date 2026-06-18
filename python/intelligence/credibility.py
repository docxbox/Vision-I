"""
intelligence/credibility.py
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Source credibility tracking with exponential decay and event-driven adjustments.

Model
â”€â”€â”€â”€â”€â”€
Each source starts from a heuristic tier baseline score and decays slowly
over time if not verified:

    credibility(t) = base_score * decay_factor ^ (days_since_last_verified)

Where:
  - base_score    = derived from source tier (see SOURCE_TIERS)
  - decay_factor  = 0.98 (sticky â€” credibility erodes slowly)

Adjustments
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  +0.05  for every corroboration boost (event confirmed by 2+ independent
         sources within 4 hours)
  -0.10  for every penalty (event later flagged as propaganda or bot-amplified)

The cumulative boost/penalty is applied on top of the decayed base score and
the result is clamped to [0.0, 1.0].

Persistence
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Boost and penalty counts are stored in JSON at:
    <project_root>/data/source_credibility.json

The file schema is:
    {
        "source_key": {
            "penalties": int,
            "boosts":    int,
            "last_updated": "ISO timestamp"
        },
        ...
    }

`aiofiles` is used for async file I/O when available; falls back to synchronous
I/O wrapped in asyncio's default executor.

Usage:
    tracker = CredibilityTracker(session)
    scores  = await tracker.compute_all()
    score   = await tracker.get_source_score("opensky")
    await tracker.apply_penalty("telegram", reason="bot amplification detected")
    await tracker.apply_boost("newsapi", reason="corroborated by 3 sources")
    conf    = await tracker.weight_event_confidence("reddit", base_confidence=0.75)
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from storage.database import EventModel

logger = logging.getLogger("vision_i.intelligence.credibility")
_DECAY_FACTOR       = 0.98     # per-day decay multiplier
_BOOST_INCREMENT    = 0.05     # credibility gain per corroboration
_PENALTY_DECREMENT  = 0.10     # credibility loss per flag
_CORROBORATION_HOURS = 4       # window to look for cross-source confirmation
_MIN_CORROBORATION_SOURCES = 2 # how many distinct sources = "corroborated"

# Path for persistent JSON file (relative to this file's project root)
_DATA_DIR  = Path(__file__).resolve().parent.parent.parent / "data"
_CRED_FILE = _DATA_DIR / "source_credibility.json"
# Tier 1 â†’ 0.95: authoritative primary sources
# Tier 2 â†’ 0.75: major aggregators / curated feeds
# Tier 3 â†’ 0.55: social / community platforms
# Tier 4 â†’ 0.35: unknown / unclassified

SOURCE_TIERS: Dict[str, int] = {
    # Tier 1 â€” ground-truth / official
    "opensky":      1,
    "ais":          1,
    "usgs":         1,
    "noaa":         1,
    "nato":         1,
    "un_ocha":      1,
    "rss_reuters":  1,
    "rss_ap_news":  1,
    "rss_bbc_world":1,
    # Tier 2 â€” major news / curated aggregators
    "newsapi":          2,
    "gdelt":            2,
    "rss_al_jazeera":   2,
    "rss_xinhua":       2,
    "rss_cnbc_world":   2,
    "rss_bloomberg_mrkts": 2,
    "rss_bellingcat":   2,
    "rss_crisis_group": 2,
    # Tier 3 â€” social / community
    "rss":          3,
    "reddit":       3,
    "twitter":      3,
    "telegram":     3,
    "youtube":      3,
    # Tier 2.5 â€” curated forum (hack news sits between 2/3)
    "hackernews":   2,
}

TIER_BASE_SCORES: Dict[int, float] = {
    1: 0.95,
    2: 0.75,
    3: 0.55,
    4: 0.35,
}


def _tier_for_source(source_key: str) -> int:
    """Return the tier for a source key, defaulting to 4 (unknown)."""
    lower = (source_key or "").lower()
    # Exact match first
    if lower in SOURCE_TIERS:
        return SOURCE_TIERS[lower]
    # Prefix / substring matching for dynamic source names
    for key, tier in SOURCE_TIERS.items():
        if key in lower:
            return tier
    return 4


def _base_score_for_source(source_key: str) -> float:
    """Return the base credibility score for a given source."""
    return TIER_BASE_SCORES.get(_tier_for_source(source_key), 0.35)

@dataclass
class SourceCredibility:
    source_key:        str
    display_name:      str
    credibility_score: float   # 0.0 â€“ 1.0, final decayed+adjusted value
    tier:              int     # 1â€“4
    base_score:        float   # tier baseline before decay/adjustments
    penalty_count:     int
    boost_count:       int
    last_computed:     str     # ISO timestamp

    def to_dict(self) -> Dict:
        return {
            "source_key":        self.source_key,
            "display_name":      self.display_name,
            "credibility_score": round(self.credibility_score, 4),
            "tier":              self.tier,
            "base_score":        round(self.base_score, 4),
            "penalty_count":     self.penalty_count,
            "boost_count":       self.boost_count,
            "last_computed":     self.last_computed,
        }

def _read_json_sync() -> Dict:
    """Read the credibility JSON file synchronously."""
    try:
        if _CRED_FILE.exists():
            with open(_CRED_FILE, "r", encoding="utf-8") as fh:
                return json.load(fh)
    except Exception as exc:
        logger.warning("credibility: could not read %s: %s", _CRED_FILE, exc)
    return {}


def _write_json_sync(data: Dict) -> None:
    """Write the credibility JSON file synchronously."""
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(_CRED_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
    except Exception as exc:
        logger.error("credibility: could not write %s: %s", _CRED_FILE, exc)


async def _read_json() -> Dict:
    """Read JSON file, using aiofiles if available, else executor fallback."""
    try:
        import aiofiles  # type: ignore
        if not _CRED_FILE.exists():
            return {}
        async with aiofiles.open(_CRED_FILE, "r", encoding="utf-8") as fh:
            raw = await fh.read()
        return json.loads(raw)
    except ImportError:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _read_json_sync)
    except Exception as exc:
        logger.warning("credibility: async read failed: %s", exc)
        return {}


async def _write_json(data: Dict) -> None:
    """Write JSON file, using aiofiles if available, else executor fallback."""
    try:
        import aiofiles  # type: ignore
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(data, indent=2, ensure_ascii=False)
        async with aiofiles.open(_CRED_FILE, "w", encoding="utf-8") as fh:
            await fh.write(raw)
    except ImportError:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _write_json_sync, data)
    except Exception as exc:
        logger.error("credibility: async write failed: %s", exc)


def _compute_decayed_score(
    base_score:           float,
    days_since_verified:  float,
    penalty_count:        int,
    boost_count:          int,
) -> float:
    """
    Apply exponential decay and then add boosts / subtract penalties.
    Result is clamped to [0.0, 1.0].
    """
    decayed = base_score * (_DECAY_FACTOR ** days_since_verified)
    adjusted = (
        decayed
        + (boost_count   * _BOOST_INCREMENT)
        - (penalty_count * _PENALTY_DECREMENT)
    )
    return max(0.0, min(1.0, adjusted))

class CredibilityTracker:
    """
    Computes and persists source credibility scores.

    One instance lives on app.state; all methods are stateless between calls.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def compute_all(self) -> List[SourceCredibility]:
        """
        Compute credibility for every source that has produced at least one
        event in the database, plus all sources listed in SOURCE_TIERS.

        Returns a list sorted by credibility_score descending.
        """
        results: List[SourceCredibility] = []
        try:
            # Discover all sources active in the DB
            db_sources = await self._fetch_active_sources()
            # Merge with the hardcoded known sources
            all_sources: set = set(db_sources) | set(SOURCE_TIERS.keys())

            # Load persisted adjustment counts
            persisted = await _read_json()
            now_str   = datetime.now(timezone.utc).isoformat() + "Z"

            # Corroboration boosts from recent events (automatic, one-shot per run)
            corroboration_counts = await self._count_corroborations()

            for source_key in sorted(all_sources):
                entry     = persisted.get(source_key, {})
                penalties = int(entry.get("penalties", 0))
                boosts    = int(entry.get("boosts", 0))

                # Add any auto-detected corroborations (no double-counting guard here;
                # the caller should call apply_boost explicitly for persistent tracking)
                auto_boosts = corroboration_counts.get(source_key, 0)

                tier       = _tier_for_source(source_key)
                base_score = TIER_BASE_SCORES.get(tier, 0.35)

                # Days since last update (approximation â€” treat JSON timestamp as proxy)
                last_updated_str = entry.get("last_updated")
                if last_updated_str:
                    try:
                        last_dt = datetime.fromisoformat(
                            last_updated_str.replace("Z", "+00:00")
                        )
                        days_since = (
                            datetime.now(timezone.utc) - last_dt
                        ).total_seconds() / 86_400
                    except ValueError:
                        days_since = 0.0
                else:
                    days_since = 0.0

                score = _compute_decayed_score(
                    base_score, days_since,
                    penalties,
                    boosts + auto_boosts,
                )

                results.append(SourceCredibility(
                    source_key        = source_key,
                    display_name      = source_key.replace("_", " ").title(),
                    credibility_score = round(score, 4),
                    tier              = tier,
                    base_score        = base_score,
                    penalty_count     = penalties,
                    boost_count       = boosts + auto_boosts,
                    last_computed     = now_str,
                ))

            results.sort(key=lambda r: r.credibility_score, reverse=True)
            logger.info(
                "CredibilityTracker.compute_all: scored %d sources", len(results),
            )

        except Exception as exc:
            logger.error("CredibilityTracker.compute_all() failed: %s", exc)

        return results

    async def get_source_score(self, source_key: str) -> float:
        """
        Return the current credibility score for a single source key.
        Returns the tier baseline if the source has no persisted data.
        """
        try:
            persisted = await _read_json()
            entry     = persisted.get(source_key, {})
            penalties = int(entry.get("penalties", 0))
            boosts    = int(entry.get("boosts",    0))

            last_updated_str = entry.get("last_updated")
            days_since = 0.0
            if last_updated_str:
                try:
                    last_dt = datetime.fromisoformat(
                        last_updated_str.replace("Z", "+00:00")
                    )
                    days_since = (
                        datetime.now(timezone.utc) - last_dt
                    ).total_seconds() / 86_400
                except ValueError:
                    pass

            base_score = _base_score_for_source(source_key)
            return _compute_decayed_score(base_score, days_since, penalties, boosts)

        except Exception as exc:
            logger.error("CredibilityTracker.get_source_score(%r) failed: %s", source_key, exc)
            return _base_score_for_source(source_key)

    async def apply_penalty(self, source_key: str, reason: str) -> None:
        """
        Increment the penalty counter for a source and persist it.
        Logs the reason for audit purposes.
        """
        try:
            persisted = await _read_json()
            entry = persisted.setdefault(source_key, {"penalties": 0, "boosts": 0})
            entry["penalties"] = int(entry.get("penalties", 0)) + 1
            entry["last_updated"] = datetime.now(timezone.utc).isoformat() + "Z"
            await _write_json(persisted)
            logger.info(
                "CredibilityTracker: penalty applied to '%s' (reason=%s, total=%d)",
                source_key, reason, entry["penalties"],
            )
        except Exception as exc:
            logger.error(
                "CredibilityTracker.apply_penalty(%r) failed: %s", source_key, exc,
            )

    async def apply_boost(self, source_key: str, reason: str) -> None:
        """
        Increment the boost counter for a source and persist it.
        Logs the reason for audit purposes.
        """
        try:
            persisted = await _read_json()
            entry = persisted.setdefault(source_key, {"penalties": 0, "boosts": 0})
            entry["boosts"] = int(entry.get("boosts", 0)) + 1
            entry["last_updated"] = datetime.now(timezone.utc).isoformat() + "Z"
            await _write_json(persisted)
            logger.info(
                "CredibilityTracker: boost applied to '%s' (reason=%s, total=%d)",
                source_key, reason, entry["boosts"],
            )
        except Exception as exc:
            logger.error(
                "CredibilityTracker.apply_boost(%r) failed: %s", source_key, exc,
            )

    async def weight_event_confidence(
        self,
        event_source:    str,
        base_confidence: float,
    ) -> float:
        """
        Adjust a raw confidence value by multiplying it with the source's
        credibility score.  Result is clamped to [0.0, 1.0].

        Example:
            base_confidence=0.85, source_credibility=0.55
            â†’ weighted = 0.85 * 0.55 = 0.4675
        """
        try:
            cred = await self.get_source_score(event_source)
            return max(0.0, min(1.0, base_confidence * cred))
        except Exception as exc:
            logger.error(
                "CredibilityTracker.weight_event_confidence(%r) failed: %s",
                event_source, exc,
            )
            return base_confidence

    async def _fetch_active_sources(self) -> List[str]:
        """Return distinct source keys seen in the events table."""
        try:
            rows = (
                await self._session.execute(
                    select(EventModel.source).distinct().limit(500)
                )
            ).fetchall()
            return [r.source for r in rows if r.source]
        except Exception as exc:
            logger.error(
                "CredibilityTracker._fetch_active_sources() failed: %s", exc,
            )
            return []

    async def _count_corroborations(
        self,
        window_hours: int = 24,
    ) -> Dict[str, int]:
        """
        For each source, count how many of its events from the last
        `window_hours` were corroborated by 2+ *other* distinct sources
        within 4 hours.

        Returns {source_key: corroboration_count}.
        """
        try:
            now          = datetime.now(timezone.utc)
            window_start = now - timedelta(hours=window_hours)

            rows = (
                await self._session.execute(
                    select(
                        EventModel.event_id,
                        EventModel.title,
                        EventModel.source,
                        EventModel.timestamp,
                    )
                    .where(
                        EventModel.timestamp >= window_start,
                        EventModel.timestamp <= now,
                    )
                    .order_by(EventModel.timestamp.asc())
                    .limit(1_000)  # cap at 1k to keep O(nÂ²) loop under ~0.1 s
                )
            ).fetchall()

            events = [
                {
                    "event_id":  r.event_id,
                    "title":     r.title or "",
                    "source":    r.source or "",
                    "timestamp": r.timestamp,
                }
                for r in rows
                if r.timestamp is not None
            ]

            corroboration_counts: Dict[str, int] = {}

            for idx, ev in enumerate(events):
                ev_ts  = ev["timestamp"]
                window = timedelta(hours=_CORROBORATION_HOURS)
                # Find events with similar title in the corroboration window
                other_sources: set = set()
                for other in events:
                    if other["event_id"] == ev["event_id"]:
                        continue
                    other_ts = other["timestamp"]
                    if other_ts < ev_ts or other_ts > ev_ts + window:
                        continue
                    if other["source"] == ev["source"]:
                        continue
                    # Cheap title similarity check (bigram overlap proxy)
                    ratio = _title_overlap(ev["title"], other["title"])
                    if ratio >= 0.5:
                        other_sources.add(other["source"])

                if len(other_sources) >= _MIN_CORROBORATION_SOURCES:
                    src = ev["source"]
                    corroboration_counts[src] = corroboration_counts.get(src, 0) + 1

            return corroboration_counts

        except Exception as exc:
            logger.error(
                "CredibilityTracker._count_corroborations() failed: %s", exc,
            )
            return {}

def _title_overlap(a: str, b: str) -> float:
    """
    Quick title similarity using word-set Jaccard coefficient.
    Avoids the O(nÂ²) character-level SequenceMatcher for the inner loop.
    """
    _stopwords = {"the", "a", "an", "is", "in", "on", "at", "of", "to", "and",
                  "for", "that", "with", "this", "from", "are", "was", "has"}

    def words(text: str) -> set:
        return {
            w.lower() for w in text.split()
            if len(w) > 2 and w.lower() not in _stopwords
        }

    wa, wb = words(a), words(b)
    if not wa or not wb:
        return 0.0
    intersection = len(wa & wb)
    union        = len(wa | wb)
    return intersection / union

