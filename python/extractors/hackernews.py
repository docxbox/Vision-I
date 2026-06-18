"""
extractors/hackernews.py
─────────────────────────
Fetches trending stories from the Hacker News Firebase API.

Docs: https://github.com/HackerNews/API
No API key required. Rate-limit: be polite, ~50 req/min.

Four feeds:
  - topstories    : Front page stories (500 IDs)
  - newstories    : Newest (500 IDs)
  - beststories   : Best rated (200 IDs)
  - askstories    : Ask HN (200 IDs)

Useful as a signal for:
  - Tech / security narrative detection
  - Early emerging stories before mainstream media
  - Community sentiment on geopolitical events
  - Coordination patterns (e.g. same story hitting multiple outlets)

fetch() params:
    feed        str   "top" | "new" | "best" | "ask" (default "top")
    limit       int   max stories to return (default 25)
    query       str   optional keyword filter on title
    min_score   int   minimum story score threshold (default 10)
"""

import time
from typing import Any, Dict, List, Optional

import requests

from core.base import BaseExtractor
from core.schema import VisionEvent
from core.utils import stable_id, to_iso, utcnow_iso

_BASE     = "https://hacker-news.firebaseio.com/v0"
_FEEDS    = {
    "top":  f"{_BASE}/topstories.json",
    "new":  f"{_BASE}/newstories.json",
    "best": f"{_BASE}/beststories.json",
    "ask":  f"{_BASE}/askstories.json",
}
_ITEM_URL = f"{_BASE}/item/{{item_id}}.json"


class HackerNewsExtractor(BaseExtractor):
    """
    Pulls trending / recent stories from Hacker News.

    Stories are tagged with their score and comment count — useful as
    an amplification signal in the narrative detection layer.
    """

    source_name = "hackernews"
    _HEADERS    = {"User-Agent": "VisionI-HN/1.0"}

    def __init__(self, timeout: int = 8) -> None:
        super().__init__()
        self._session = requests.Session()
        self._session.headers.update(self._HEADERS)
        self._timeout = timeout

    def _get_ids(self, feed: str) -> List[int]:
        url = _FEEDS.get(feed, _FEEDS["top"])
        try:
            resp = self._session.get(url, timeout=self._timeout)
            resp.raise_for_status()
            return resp.json() or []
        except Exception as exc:
            self.logger.error("HN: failed to get story IDs (%s): %s", feed, exc)
            return []

    def _get_item(self, item_id: int) -> Optional[Dict]:
        try:
            resp = self._session.get(
                _ITEM_URL.format(item_id=item_id), timeout=self._timeout
            )
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None

    def fetch(
        self,
        feed: str = "top",
        limit: int = 25,
        query: str = "",
        min_score: int = 10,
        **_,
    ) -> List[Dict]:
        ids = self._get_ids(feed)
        if not ids:
            return []

        query_lower = query.lower() if query else ""
        results: List[Dict] = []
        checked   = 0
        max_check = min(len(ids), limit * 4)   # scan up to 4x to fill quota after filtering

        for item_id in ids[:max_check]:
            if len(results) >= limit:
                break

            item = self._get_item(item_id)
            if not item:
                continue

            checked += 1

            # Skip deleted/dead items and non-story types
            if item.get("deleted") or item.get("dead"):
                continue
            if item.get("type") not in ("story", "job"):
                continue

            score = item.get("score", 0)
            if score < min_score:
                continue

            title = item.get("title") or ""
            if query_lower and query_lower not in title.lower():
                continue

            item["_feed"] = feed
            results.append(item)

            # Brief pause every 10 requests to be polite
            if checked % 10 == 0:
                time.sleep(0.1)

        self.logger.info("HN '%s': fetched %d/%d stories (checked %d IDs)",
                         feed, len(results), limit, checked)
        return results

    def normalize(self, item: Any) -> VisionEvent:
        story_id  = str(item.get("id", ""))
        title     = item.get("title") or "Untitled"
        url       = item.get("url")
        score     = item.get("score", 0)
        comments  = item.get("descendants", 0)
        author    = item.get("by")
        timestamp = to_iso(item.get("time"))   # Unix timestamp
        item_type = item.get("type", "story")
        feed      = item.get("_feed", "top")

        # Derive event_type from story type
        event_type = "social" if item_type == "job" else "news"

        # Score-based amplitude: high-score stories = more viral / amplified
        if score >= 500:
            amplitude = "viral"
        elif score >= 100:
            amplitude = "trending"
        else:
            amplitude = "normal"

        return VisionEvent(
            event_id   = stable_id(self.source_name, story_id or title),
            source     = self.source_name,
            source_id  = story_id,
            event_type = event_type,
            title      = title,
            description= f"HN {amplitude} story: score={score}, comments={comments}",
            body       = item.get("text") or "",
            url        = url or f"https://news.ycombinator.com/item?id={story_id}",
            language   = "en",
            author     = author,
            timestamp  = timestamp,
            ingest_time= utcnow_iso(),
            actors     = [{"name": author, "type": "PERSON"}] if author else [],
            location   = None,
            sentiment  = None,
            tags       = ["hackernews", feed, amplitude],
            extras     = {
                "hn_id":       story_id,
                "score":       score,
                "comments":    comments,
                "type":        item_type,
                "feed":        feed,
                "amplitude":   amplitude,
            },
            raw = item,
        )

    def health(self) -> Dict:
        try:
            ids = self._get_ids("top")
            return {"source": self.source_name, "status": "ok" if ids else "empty",
                    "sample_count": min(len(ids), 1)}
        except Exception as exc:
            return {"source": self.source_name, "status": "error", "detail": str(exc)}
