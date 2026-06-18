"""
intelligence/event_intelligence.py
----------------------------------
Builds an event-centric war-room view across news, social, narratives, and
physical-world signals.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.utils import to_iso
from storage.database import AssetModel, EventModel, NarrativeModel, SignalModel
from storage.event_repo import _row_to_event

_SOCIAL_TYPES = {"social", "video"}
_PHYSICAL_SOURCES = {"opensky", "ais"}
_STOPWORDS = {
    "the", "and", "for", "with", "from", "into", "that", "this", "after", "before",
    "have", "has", "will", "they", "their", "about", "over", "under", "into", "amid",
}


def _to_iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() + "Z" if value else None


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(to_iso(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _tokenize(*parts: str) -> List[str]:
    tokens: List[str] = []
    for part in parts:
        for token in _norm(part).split():
            if len(token) >= 4 and token not in _STOPWORDS:
                tokens.append(token)
    return tokens


def _event_actor_names(event: Dict[str, Any]) -> List[str]:
    return [
        (actor.get("name") or "").strip()
        for actor in (event.get("actors") or [])
        if (actor.get("name") or "").strip()
    ]


def _event_sentiment_value(event: Dict[str, Any]) -> Optional[float]:
    sentiment = event.get("sentiment") or {}
    value = sentiment.get("score")
    return float(value) if value is not None else None


def _is_social(event: Dict[str, Any]) -> bool:
    return (event.get("event_type") or "").lower() in _SOCIAL_TYPES


def _is_physical(event: Dict[str, Any]) -> bool:
    return (event.get("source") or "").lower() in _PHYSICAL_SOURCES


def _distance_score(base_loc: Optional[Dict[str, Any]], candidate_loc: Optional[Dict[str, Any]]) -> float:
    if not base_loc or not candidate_loc:
        return 0.0
    if (base_loc.get("name") or "").lower() and (base_loc.get("name") or "").lower() == (candidate_loc.get("name") or "").lower():
        return 2.0
    if None in (base_loc.get("lat"), base_loc.get("lon"), candidate_loc.get("lat"), candidate_loc.get("lon")):
        return 0.0
    dlat = float(base_loc["lat"]) - float(candidate_loc["lat"])
    dlon = float(base_loc["lon"]) - float(candidate_loc["lon"])
    distance = math.sqrt(dlat * dlat + dlon * dlon)
    if distance <= 1.0:
        return 1.5
    if distance <= 3.0:
        return 0.75
    return 0.0


def _shared_terms(base_terms: Sequence[str], candidate_terms: Sequence[str]) -> List[str]:
    return sorted(set(base_terms).intersection(candidate_terms))


def _match_score(base_event: Dict[str, Any], candidate: Dict[str, Any], base_terms: Sequence[str]) -> Tuple[float, List[str]]:
    reasons: List[str] = []
    score = 0.0

    base_actor_names = {name.lower() for name in _event_actor_names(base_event)}
    candidate_actor_names = {name.lower() for name in _event_actor_names(candidate)}
    shared_actors = sorted(base_actor_names.intersection(candidate_actor_names))
    if shared_actors:
        score += 2.5 + (0.4 * len(shared_actors))
        reasons.append("actors")

    base_tags = {str(tag).lower() for tag in (base_event.get("tags") or [])}
    candidate_tags = {str(tag).lower() for tag in (candidate.get("tags") or [])}
    if base_tags and candidate_tags and base_tags.intersection(candidate_tags):
        score += 1.5
        reasons.append("tags")

    shared_terms = _shared_terms(
        base_terms,
        _tokenize(candidate.get("title") or "", candidate.get("body") or "", candidate.get("description") or ""),
    )
    if shared_terms:
        score += min(2.0, 0.5 * len(shared_terms))
        reasons.append("keywords")

    loc_score = _distance_score(base_event.get("location"), candidate.get("location"))
    if loc_score:
        score += loc_score
        reasons.append("location")

    return score, reasons


def _amplification_score(event: Dict[str, Any]) -> float:
    extras = event.get("extras") or {}
    return float(
        (extras.get("score") or 0)
        + (extras.get("num_comments") or 0) * 1.5
        + (extras.get("comment_count") or 0) * 1.5
        + (extras.get("like_count") or 0) * 0.5
        + (extras.get("view_count") or 0) * 0.01
    )


class EventIntelligenceService:
    """Assemble a unified event intelligence view from existing stores."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def build_event_view(self, event_id: str, horizon_hours: int = 48) -> Optional[Dict[str, Any]]:
        base_row = (
            await self._session.execute(
                select(EventModel).where(EventModel.event_id == event_id)
            )
        ).scalar_one_or_none()
        if not base_row:
            return None

        base_event = _row_to_event(base_row)
        t0 = base_row.timestamp or datetime.now(timezone.utc)
        window_end = t0 + timedelta(hours=horizon_hours)
        base_terms = _tokenize(
            base_event.get("title") or "",
            base_event.get("description") or "",
            " ".join(_event_actor_names(base_event)),
            " ".join(str(tag) for tag in (base_event.get("tags") or [])),
            ((base_event.get("location") or {}).get("name") or ""),
        )

        candidate_rows = (
            await self._session.execute(
                select(EventModel)
                .where(
                    and_(
                        EventModel.event_id != event_id,
                        EventModel.timestamp.is_not(None),
                        EventModel.timestamp >= t0,
                        EventModel.timestamp <= window_end,
                    )
                )
                .order_by(EventModel.timestamp.asc())
                .limit(800)
            )
        ).scalars().all()

        related: List[Dict[str, Any]] = []
        for row in candidate_rows:
            candidate = _row_to_event(row)
            score, reasons = _match_score(base_event, candidate, base_terms)
            if score >= 1.5:
                candidate["_match_score"] = round(score, 3)
                candidate["_match_reasons"] = reasons
                related.append(candidate)

        related.sort(key=lambda item: (item.get("_match_score", 0), item.get("timestamp") or ""), reverse=True)

        related_news = [item for item in related if not _is_social(item) and not _is_physical(item)][:50]
        social_reactions = [item for item in related if _is_social(item)]
        physical_events = [item for item in related if _is_physical(item)]

        narratives = await self._related_narratives(base_event, t0, window_end, base_terms)
        physical_assets = await self._related_assets(base_event, t0, window_end)
        signals = await self._related_signals([event_id] + [item["event_id"] for item in related], t0, window_end)

        reaction_timeline = self._build_timeline(t0, related_news, social_reactions, physical_events, narratives)
        influencer_amplification = self._build_influencer_amplification(social_reactions)
        divergence_score = self._compute_divergence(related_news, social_reactions, influencer_amplification["amplification_score"])
        narrative_clusters = self._build_narrative_clusters(signals, related_news, social_reactions)

        return {
            "event": base_event,
            "t0": _to_iso(t0),
            "related_news": related_news,
            "social_reactions": social_reactions[:50],
            "signals": signals[:50],
            "narratives": narratives,
            "actors": _event_actor_names(base_event),
            "physical_signals": {
                "events": physical_events[:50],
                "assets": physical_assets,
            },
            "reaction_timeline": reaction_timeline[:150],
            "narrative_clusters": narrative_clusters,
            "divergence_score": divergence_score,
            "influencer_amplification": influencer_amplification,
        }

    async def _related_narratives(
        self,
        base_event: Dict[str, Any],
        t0: datetime,
        window_end: datetime,
        base_terms: Sequence[str],
    ) -> List[Dict[str, Any]]:
        rows = (
            await self._session.execute(
                select(NarrativeModel)
                .where(
                    and_(
                        NarrativeModel.detected_at >= t0,
                        NarrativeModel.detected_at <= window_end,
                    )
                )
                .order_by(NarrativeModel.detected_at.asc())
                .limit(200)
            )
        ).scalars().all()

        actors = {name.lower() for name in _event_actor_names(base_event)}
        out: List[Dict[str, Any]] = []
        for row in rows:
            topic_tokens = _tokenize(row.topic or "")
            narrative_actors = {str(name).lower() for name in (row.actors or [])}
            if actors.intersection(narrative_actors) or _shared_terms(base_terms, topic_tokens):
                out.append(
                    {
                        "narrative_id": row.narrative_id,
                        "signal_type": row.signal_type,
                        "topic": row.topic,
                        "strength": row.strength,
                        "confidence": row.confidence,
                        "severity": row.severity,
                        "event_count": row.event_count,
                        "source_count": row.source_count,
                        "sources": row.sources or [],
                        "actors": row.actors or [],
                        "sample_titles": row.sample_titles or [],
                        "detected_at": _to_iso(row.detected_at),
                        "meta": row.meta_data or {},
                    }
                )
        return out[:25]

    async def _related_assets(
        self,
        base_event: Dict[str, Any],
        t0: datetime,
        window_end: datetime,
    ) -> List[Dict[str, Any]]:
        rows = (
            await self._session.execute(
                select(AssetModel)
                .where(
                    and_(
                        AssetModel.last_seen.is_not(None),
                        AssetModel.last_seen >= (t0 - timedelta(hours=6)),
                        AssetModel.last_seen <= window_end,
                    )
                )
                .order_by(AssetModel.last_seen.desc())
                .limit(200)
            )
        ).scalars().all()

        out: List[Dict[str, Any]] = []
        base_loc = base_event.get("location")
        for row in rows:
            loc_score = _distance_score(
                base_loc,
                {"lat": row.last_lat, "lon": row.last_lon, "name": None},
            )
            if loc_score <= 0 and base_loc and base_loc.get("name"):
                continue
            out.append(
                {
                    "asset_id": row.asset_id,
                    "asset_type": row.asset_type,
                    "name": row.name,
                    "callsign": row.callsign,
                    "identifier": row.identifier,
                    "origin_country": row.origin_country,
                    "last_lat": row.last_lat,
                    "last_lon": row.last_lon,
                    "last_altitude": row.last_altitude,
                    "last_speed": row.last_speed,
                    "last_heading": row.last_heading,
                    "last_seen": _to_iso(row.last_seen),
                    "meta": row.meta or {},
                    "proximity_score": round(loc_score, 3),
                }
            )
        return out[:25]

    async def _related_signals(
        self,
        source_event_ids: Iterable[str],
        t0: datetime,
        window_end: datetime,
    ) -> List[Dict[str, Any]]:
        ids = list(dict.fromkeys(source_event_ids))
        if not ids:
            return []
        rows = (
            await self._session.execute(
                select(SignalModel)
                .where(
                    and_(
                        SignalModel.source_event_id.in_(ids),
                        SignalModel.timestamp.is_not(None),
                        SignalModel.timestamp >= t0,
                        SignalModel.timestamp <= window_end,
                    )
                )
                .order_by(SignalModel.timestamp.asc())
                .limit(400)
            )
        ).scalars().all()
        return [
            {
                "signal_id": row.signal_id,
                "source_event_id": row.source_event_id,
                "source": row.source,
                "signal_type": row.signal_type,
                "title": row.title,
                "timestamp": _to_iso(row.timestamp),
                "cluster_id": row.cluster_id,
                "confidence": row.confidence,
                "actors": row.actors or [],
                "meta": row.meta or {},
            }
            for row in rows
        ]

    def _build_timeline(
        self,
        t0: datetime,
        related_news: Sequence[Dict[str, Any]],
        social_reactions: Sequence[Dict[str, Any]],
        physical_events: Sequence[Dict[str, Any]],
        narratives: Sequence[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        for item in related_news:
            entries.append({
                "kind": "news",
                "timestamp": item.get("timestamp"),
                "source": item.get("source"),
                "title": item.get("title"),
                "delta_minutes": self._delta_minutes(t0, item.get("timestamp")),
                "sentiment_score": _event_sentiment_value(item),
            })
        for item in social_reactions:
            entries.append({
                "kind": "social",
                "timestamp": item.get("timestamp"),
                "source": item.get("source"),
                "title": item.get("title"),
                "delta_minutes": self._delta_minutes(t0, item.get("timestamp")),
                "sentiment_score": _event_sentiment_value(item),
                "amplification_score": round(_amplification_score(item), 2),
            })
        for item in physical_events:
            entries.append({
                "kind": "physical",
                "timestamp": item.get("timestamp"),
                "source": item.get("source"),
                "title": item.get("title"),
                "delta_minutes": self._delta_minutes(t0, item.get("timestamp")),
            })
        for item in narratives:
            entries.append({
                "kind": "narrative",
                "timestamp": item.get("detected_at"),
                "source": item.get("signal_type"),
                "title": item.get("topic"),
                "delta_minutes": self._delta_minutes(t0, item.get("detected_at")),
                "strength": item.get("strength"),
            })
        entries.sort(key=lambda item: item.get("timestamp") or "")
        return entries

    def _build_influencer_amplification(self, social_reactions: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        ranked = []
        for item in social_reactions:
            score = _amplification_score(item)
            if score <= 0:
                continue
            ranked.append({
                "event_id": item.get("event_id"),
                "source": item.get("source"),
                "author": item.get("author"),
                "title": item.get("title"),
                "timestamp": item.get("timestamp"),
                "amplification_score": round(score, 2),
            })
        ranked.sort(key=lambda item: item["amplification_score"], reverse=True)
        total_score = round(sum(item["amplification_score"] for item in ranked), 2)
        return {
            "amplification_score": total_score,
            "top_amplifiers": ranked[:15],
        }

    def _build_narrative_clusters(
        self,
        signals: Sequence[Dict[str, Any]],
        related_news: Sequence[Dict[str, Any]],
        social_reactions: Sequence[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        grouped: Dict[str, Dict[str, Any]] = {}
        for signal in signals:
            cluster_id = signal.get("cluster_id")
            if not cluster_id:
                continue
            bucket = grouped.setdefault(cluster_id, {
                "cluster_id": cluster_id,
                "signal_count": 0,
                "sources": set(),
                "titles": [],
            })
            bucket["signal_count"] += 1
            bucket["sources"].add(signal.get("source"))
            if signal.get("title"):
                bucket["titles"].append(signal["title"])

        if not grouped:
            combined = related_news + social_reactions
            token_counts = Counter(
                token
                for event in combined
                for token in _tokenize(event.get("title") or "", event.get("body") or "")
            )
            return [
                {
                    "cluster_id": f"topic:{token}",
                    "signal_count": count,
                    "sources": sorted({event.get("source") for event in combined if token in _tokenize(event.get("title") or "", event.get("body") or "")}),
                    "titles": [event.get("title") for event in combined if token in _tokenize(event.get("title") or "", event.get("body") or "")][:5],
                }
                for token, count in token_counts.most_common(5)
            ]

        out = []
        for cluster_id, bucket in grouped.items():
            out.append({
                "cluster_id": cluster_id,
                "signal_count": bucket["signal_count"],
                "sources": sorted(bucket["sources"]),
                "titles": bucket["titles"][:5],
            })
        out.sort(key=lambda item: item["signal_count"], reverse=True)
        return out[:20]

    def _compute_divergence(
        self,
        related_news: Sequence[Dict[str, Any]],
        social_reactions: Sequence[Dict[str, Any]],
        amplification_score: float,
    ) -> float:
        news_scores = [_event_sentiment_value(item) for item in related_news]
        social_scores = [_event_sentiment_value(item) for item in social_reactions]
        news_scores = [value for value in news_scores if value is not None]
        social_scores = [value for value in social_scores if value is not None]

        if not news_scores and not social_scores:
            return 0.0
        news_avg = sum(news_scores) / len(news_scores) if news_scores else 0.5
        social_avg = sum(social_scores) / len(social_scores) if social_scores else 0.5
        raw = abs(news_avg - social_avg)
        amplification_factor = min(0.35, amplification_score / 400.0)
        return round(min(1.0, raw + amplification_factor), 4)

    @staticmethod
    def _delta_minutes(t0: datetime, timestamp: Optional[str]) -> Optional[int]:
        ts = _parse_ts(timestamp)
        if not ts:
            return None
        return int((ts - t0).total_seconds() // 60)
