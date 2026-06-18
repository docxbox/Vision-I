"""
extractors/twitter.py
─────────────────────
Twitter / X recent search via the v2 API.

Docs: https://developer.twitter.com/en/docs/twitter-api/tweets/search/api-reference/get-tweets-search-recent

Requires `TWITTER_BEARER_TOKEN` in env.

fetch() params:
    query        str   v2 search query (required)
    limit        int   max results (default 25, max 100)
    lang         str   ISO language filter (default "en")
"""

import os
from typing import Any, Dict, List, Optional

import requests

from core.base import BaseExtractor
from core.schema import VisionEvent
from core.utils import stable_id, to_iso, utcnow_iso

_BASE = "https://api.twitter.com/2/tweets/search/recent"


class TwitterExtractor(BaseExtractor):
    source_name = "twitter"

    def __init__(self, timeout: int = 10) -> None:
        super().__init__()
        self._timeout = timeout
        self._token = os.getenv("TWITTER_BEARER_TOKEN", "").strip()
        self._session = requests.Session()
        if self._token:
            self._session.headers.update({"Authorization": f"Bearer {self._token}"})
        self._session.headers.update({"User-Agent": "VisionI-Twitter/1.0"})

    def fetch(
        self,
        query: str = "",
        limit: int = 25,
        lang: str = "en",
        **_,
    ) -> List[Dict]:
        if not self._token:
            self.logger.warning("Twitter: TWITTER_BEARER_TOKEN not configured")
            return []
        if not query:
            return []

        params = {
            "query": f"{query} lang:{lang} -is:retweet" if lang else f"{query} -is:retweet",
            "max_results": max(10, min(int(limit), 100)),
            "tweet.fields": "id,text,created_at,author_id,public_metrics,lang,entities,geo",
            "expansions": "author_id,geo.place_id",
            "user.fields": "id,name,username,verified,public_metrics",
            "place.fields": "full_name,country,country_code,geo",
        }

        try:
            resp = self._session.get(_BASE, params=params, timeout=self._timeout)
            resp.raise_for_status()
            payload = resp.json() or {}
        except Exception as exc:
            self.logger.error("Twitter: search failed: %s", exc)
            return []

        tweets = payload.get("data") or []
        users = {u["id"]: u for u in (payload.get("includes", {}).get("users") or [])}
        places = {p["id"]: p for p in (payload.get("includes", {}).get("places") or [])}

        for tweet in tweets:
            author = users.get(tweet.get("author_id"))
            if author:
                tweet["_author"] = author
            place_id = (tweet.get("geo") or {}).get("place_id")
            if place_id and place_id in places:
                tweet["_place"] = places[place_id]

        self.logger.info("Twitter: fetched %d tweets for '%s'", len(tweets), query)
        return tweets

    def normalize(self, item: Any) -> VisionEvent:
        tweet_id = str(item.get("id") or "")
        text = (item.get("text") or "").strip()
        metrics = item.get("public_metrics") or {}
        author = item.get("_author") or {}
        place = item.get("_place") or {}

        username = author.get("username") or author.get("name") or "unknown"
        name = author.get("name") or username

        amplification = (
            metrics.get("retweet_count", 0)
            + metrics.get("quote_count", 0)
            + metrics.get("reply_count", 0)
        )
        if amplification >= 1000:
            tier = "viral"
        elif amplification >= 100:
            tier = "trending"
        else:
            tier = "normal"

        location: Optional[Dict[str, Any]] = None
        if place:
            location = {
                "name": place.get("full_name"),
                "country": place.get("country_code"),
            }

        return VisionEvent(
            event_id=stable_id(self.source_name, tweet_id or text[:64]),
            source=self.source_name,
            source_id=tweet_id,
            event_type="social",
            title=(text[:140] + "…") if len(text) > 140 else text or f"Tweet by @{username}",
            description=f"@{username} · ❤ {metrics.get('like_count', 0)} · ↻ {metrics.get('retweet_count', 0)} · 💬 {metrics.get('reply_count', 0)}",
            body=text,
            url=f"https://x.com/{username}/status/{tweet_id}" if tweet_id else None,
            language=item.get("lang") or "en",
            author=username,
            timestamp=to_iso(item.get("created_at")),
            ingest_time=utcnow_iso(),
            actors=[{"name": name, "type": "PERSON", "handle": username}],
            location=location,
            sentiment=None,
            tags=["twitter", tier],
            extras={
                "tweet_id": tweet_id,
                "amplification": tier,
                "metrics": metrics,
                "verified": author.get("verified", False),
                "follower_count": (author.get("public_metrics") or {}).get("followers_count"),
            },
            raw=item,
        )

    def health(self) -> Dict:
        if not self._token:
            return {"source": self.source_name, "status": "unconfigured", "detail": "TWITTER_BEARER_TOKEN missing"}
        return {"source": self.source_name, "status": "ok"}
