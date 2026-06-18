"""
intelligence/social_enricher.py
--------------------------------
Auto-enrich significant events with social amplification (X/Twitter).

This module is designed to be triggered after ingest_complete so high-signal
events quickly gain social context without waiting for periodic text ingests.
"""

from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from core.utils import utcnow_iso
from extractors.socials import SocialExtractor
from extractors.twitter import TwitterExtractor
from nlp.pipeline import NLPPipeline
from storage.database import EventModel
from storage.event_repo import EventRepository, _row_to_event

logger = logging.getLogger("vision_i.intelligence.social_enricher")

_KEYWORDS = {
    "attack", "strike", "missile", "drone", "bomb", "explosion", "shooting",
    "protest", "riot", "unrest", "blockade", "embargo", "sanction",
    "earthquake", "flood", "wildfire", "storm", "hurricane", "typhoon",
    "cyber", "ransomware", "outage", "blackout", "pipeline", "port",
    "collision", "sink", "aviation", "airspace",
}

_STOPWORDS = {
    "the", "and", "for", "with", "from", "into", "amid", "after", "before",
    "has", "have", "will", "says", "said", "report", "reports", "update",
}


def _tokenize(text: str) -> List[str]:
    return [
        tok for tok in re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split()
        if tok and tok not in _STOPWORDS and len(tok) >= 4
    ]


def _score_event(event: Dict[str, Any]) -> float:
    score = 0.0
    title = (event.get("title") or "").lower()
    body = (event.get("body") or event.get("description") or "").lower()
    tags = {str(t).lower() for t in (event.get("tags") or [])}
    event_type = (event.get("event_type") or "").lower()

    if "anomaly" in tags or "anomaly" in event_type:
        score += 2.0
    if "conflict" in event_type or "disaster" in event_type or "security" in event_type:
        score += 1.5

    for kw in _KEYWORDS:
        if kw in title or kw in body:
            score += 0.75

    sentiment = (event.get("sentiment") or {}).get("score")
    if sentiment is not None and sentiment < 0.4:
        score += 0.5

    return score


def _build_query(event: Dict[str, Any], max_terms: int = 8) -> str:
    title = event.get("title") or ""
    body = event.get("body") or ""
    location = (event.get("location") or {}).get("name")
    actors = [a.get("name") for a in (event.get("actors") or []) if a.get("name")]

    tokens = _tokenize(title) + _tokenize(body)
    unique_tokens = []
    for tok in tokens:
        if tok not in unique_tokens:
            unique_tokens.append(tok)
        if len(unique_tokens) >= max_terms:
            break

    phrases = []
    for name in actors[:2]:
        if len(name) >= 3:
            phrases.append(f'"{name}"')
    if location:
        phrases.append(f'"{location}"')

    if unique_tokens:
        phrases.extend(unique_tokens[:max_terms])

    if not phrases:
        return title[:120]

    query = " OR ".join(phrases[: max_terms + 3])
    return query[:512]


class SocialEnricher:
    def __init__(
        self,
        window_hours: int = 6,
        max_events: int = 6,
        limit_per_event: int = 25,
        min_score: float = 2.0,
        cooldown_minutes: int = 45,
    ) -> None:
        self.window_hours = window_hours
        self.max_events = max_events
        self.limit_per_event = limit_per_event
        self.min_score = min_score
        self.cooldown_minutes = cooldown_minutes
        self._twitter = TwitterExtractor()
        self._social_fallback = SocialExtractor()
        self._use_social_fallback = settings.social_enrich_fallback_socials

    def _twitter_ready(self) -> bool:
        return self._twitter.health().get("status") == "ok"

    def _fetch_social_posts(self, query: str) -> List[Dict[str, Any]]:
        posts: List[Dict[str, Any]] = []
        if self._twitter_ready():
            posts = self._twitter.run(query=query, limit=self.limit_per_event, lang="en")
            if posts:
                for post in posts:
                    extras = post.get("extras") or {}
                    extras["trigger_social_source"] = "twitter"
                    post["extras"] = extras
                return posts

        if self._use_social_fallback:
            logger.info("Social enrich: falling back to Reddit/YouTube")
            posts = self._social_fallback.collect(query=query, limit=self.limit_per_event)
            for post in posts:
                extras = post.get("extras") or {}
                extras["trigger_social_source"] = "reddit_youtube"
                post["extras"] = extras
        return posts

    async def enrich_recent_events(
        self,
        session: AsyncSession,
        nlp: NLPPipeline,
        event_bus: Optional[Any] = None,
    ) -> int:
        if not settings.social_enrich_enabled:
            return 0

        candidates = await self._select_candidates(session)
        if not candidates:
            return 0

        recent_cache = None
        if event_bus:
            recent_cache = await event_bus.cache_get("social_enrich:recent")
        recent_ids = set(recent_cache or [])

        total_added = 0
        repo = EventRepository(session)

        for event in candidates:
            event_id = event.get("event_id")
            if not event_id or event_id in recent_ids:
                continue

            query = _build_query(event)
            if not query:
                continue

            posts = self._fetch_social_posts(query)
            if not posts:
                continue

            for post in posts:
                extras = post.get("extras") or {}
                extras.update({
                    "trigger_event_id": event_id,
                    "trigger_query": query,
                    "trigger_type": "auto_social",
                    "triggered_at": utcnow_iso(),
                })
                post["extras"] = extras

            # Enrich sentiment/NER for social posts
            nlp.process(posts, max_nlp=min(len(posts), 50))
            total_added += await repo.upsert_many(posts)

            recent_ids.add(event_id)
            if len(recent_ids) > 50:
                recent_ids = set(list(recent_ids)[-50:])

        if event_bus:
            await event_bus.cache_set(
                "social_enrich:recent",
                list(recent_ids),
                ttl_seconds=int(self.cooldown_minutes * 60),
            )

        logger.info("Social enrich: added %d social events", total_added)
        return total_added

    async def _select_candidates(self, session: AsyncSession) -> List[Dict[str, Any]]:
        now = datetime.now(timezone.utc)
        since = now - timedelta(hours=self.window_hours)

        rows = (
            await session.execute(
                select(EventModel)
                .where(
                    and_(
                        EventModel.timestamp.is_not(None),
                        EventModel.timestamp >= since,
                    )
                )
                .order_by(EventModel.timestamp.desc())
                .limit(400)
            )
        ).scalars().all()

        scored: List[Tuple[float, Dict[str, Any]]] = []
        for row in rows:
            event = _row_to_event(row)
            source = (event.get("source") or "").lower()
            if source in {"twitter", "reddit", "youtube"}:
                continue
            score = _score_event(event)
            if score >= self.min_score:
                scored.append((score, event))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [e for _, e in scored[: self.max_events]]
