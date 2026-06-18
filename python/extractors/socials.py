"""
extractors/socials.py
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Social media extractors for Reddit (OSINT-targeted) and YouTube.

Each platform is its own class that inherits BaseExtractor directly,
so they can be used standalone or composed via SocialExtractor.

Reddit:  uses the public JSON API (no auth needed, rate-limited).
         Main ingest targets curated OSINT/news subreddits only.
         On-demand correlation (per-event) can search site-wide.

YouTube: kept for on-demand event correlation only. NOT included in
         the default SocialExtractor sources to avoid entertainment noise.
"""

import time
from typing import Any, Dict, List, Optional

import requests

from core.base import BaseExtractor
from core.schema import VisionEvent
from core.utils import stable_id, to_iso, utcnow_iso
# Only these subreddits are searched during scheduled ingestion so the event
# feed stays clean. On-demand correlation (EventDetail) still searches globally.

OSINT_SUBREDDITS = [
    "worldnews",
    "geopolitics",
    "GlobalNews",
    "worldpolitics",
    "UkraineWarVideoReport",
    "CredibleDefense",
    "europe",
    "MiddleEast",
    "ChinaPolicy",
    "cybersecurity",
    "natsec",
    "OSINT",
    "intelligence",
    "Terrorism",
]

class RedditExtractor(BaseExtractor):
    """
    Searches Reddit using the public JSON search API.

    fetch() params:
        query      str   search query
        limit      int   max posts (default 25)
        sort       str   "new" | "hot" | "relevance" | "top" (default "relevance")
        subreddit  str   restrict to a specific subreddit (optional)
        osint_only bool  if True (default), restrict search to OSINT_SUBREDDITS
                         set False for on-demand per-event correlation
    """

    source_name = "reddit"
    _HEADERS    = {"User-Agent": "VisionI-Research/1.0 (contact: vision_i_bot)"}

    def fetch(
        self,
        query: str = "",
        limit: int = 25,
        sort: str = "relevance",
        subreddit: Optional[str] = None,
        osint_only: bool = True,
        **_,
    ) -> List[Any]:
        # When osint_only is set and no specific subreddit requested,
        # fan-out across curated OSINT subreddits and merge results.
        if osint_only and not subreddit:
            return self._fetch_osint_subreddits(query, limit, sort)

        if subreddit:
            url = f"https://www.reddit.com/r/{subreddit}/search.json"
        else:
            url = "https://www.reddit.com/search.json"

        params = {
            "q":       query,
            "sort":    sort,
            "limit":   limit,
            "type":    "link",
            "restrict_sr": "1" if subreddit else "0",
        }

        return self._get_posts(url, params)

    def _get_posts(self, url: str, params: dict) -> List[Any]:
        for attempt in range(3):
            try:
                resp = requests.get(url, headers=self._HEADERS, params=params, timeout=15)

                if resp.status_code == 429:
                    wait = 15 * (attempt + 1)
                    self.logger.warning("Reddit: rate limited (429) â€” sleeping %ds", wait)
                    time.sleep(wait)
                    continue

                if resp.status_code == 403:
                    self.logger.warning("Reddit: 403 Forbidden â€” public API may be restricted")
                    return []

                resp.raise_for_status()
                children = resp.json().get("data", {}).get("children", [])
                return [c["data"] for c in children if c.get("data")]

            except requests.RequestException as exc:
                if attempt < 2:
                    self.logger.warning("Reddit fetch attempt %d failed: %s â€” retrying", attempt + 1, exc)
                    time.sleep(5 * (attempt + 1))
                else:
                    self.logger.error("Reddit fetch failed after 3 attempts: %s", exc)
        return []

    def _fetch_osint_subreddits(self, query: str, limit: int, sort: str) -> List[Any]:
        """Fan-out across curated OSINT subreddits, return merged deduplicated results."""
        seen_ids: set = set()
        results:  List[Any] = []
        # Distribute limit across subreddits (min 3 per sub)
        per_sub = max(3, limit // len(OSINT_SUBREDDITS))

        for sub in OSINT_SUBREDDITS:
            if len(results) >= limit * 2:
                break
            url = f"https://www.reddit.com/r/{sub}/search.json"
            params = {
                "q": query,
                "sort": sort,
                "limit": per_sub,
                "type": "link",
                "restrict_sr": "1",
            }
            posts = self._get_posts(url, params)
            for p in posts:
                pid = p.get("id")
                if pid and pid not in seen_ids:
                    seen_ids.add(pid)
                    results.append(p)
            time.sleep(0.5)  # polite rate limiting between subreddits

        self.logger.info("Reddit OSINT fan-out: %d posts from %d subreddits", len(results), len(OSINT_SUBREDDITS))
        return results[:limit]

    def normalize(self, item: Any) -> VisionEvent:
        post_id  = item.get("id") or utcnow_iso()
        created  = item.get("created_utc")
        content  = (item.get("selftext") or "").strip()
        title    = item.get("title") or "Untitled"
        permalink= item.get("permalink") or ""

        return VisionEvent(
            event_id   = stable_id(self.source_name, post_id),
            source     = self.source_name,
            source_id  = post_id,
            event_type = "social",
            title      = title,
            description= content[:300] if content else title,
            body       = content,
            url        = f"https://reddit.com{permalink}",
            language   = "en",
            author     = item.get("author"),
            timestamp  = to_iso(created),
            ingest_time= utcnow_iso(),
            actors     = [],
            location   = None,
            sentiment  = None,  # filled by NLP pipeline
            tags       = [item.get("subreddit_name_prefixed", "").lstrip("r/")],
            extras     = {
                "subreddit":    item.get("subreddit"),
                "score":        item.get("score"),
                "upvote_ratio": item.get("upvote_ratio"),
                "num_comments": item.get("num_comments"),
                "flair":        item.get("link_flair_text"),
                "is_self":      item.get("is_self"),
            },
            raw = item,
        )

class YouTubeExtractor(BaseExtractor):
    """
    Searches YouTube using yt-dlp (no API key required).

    fetch() params:
        query   str  search query
        limit   int  max videos (default 10)
    """

    source_name = "youtube"

    def fetch(self, query: str = "", limit: int = 10, **_) -> List[Any]:
        try:
            import yt_dlp
        except ImportError:
            self.logger.warning("yt-dlp not installed â€” falling back to YouTube RSS")
            return self._fetch_rss(query, limit)

        opts = {
            "quiet":        True,
            "no_warnings":  True,
            "extract_flat": True,
        }

        # Try yt-dlp search first, fall back to URL-based search, then RSS
        for search_url in [
            f"ytsearch{limit}:{query}",
            f"https://www.youtube.com/results?search_query={requests.utils.quote(query)}",
        ]:
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    result = ydl.extract_info(search_url, download=False)
                    entries = (result or {}).get("entries") or []
                    if entries:
                        return entries[:limit]
            except Exception as exc:
                self.logger.debug("YouTube search '%s' failed: %s", search_url[:40], exc)

        self.logger.warning("yt-dlp search unavailable â€” falling back to YouTube RSS")
        return self._fetch_rss(query, limit)

    def _fetch_rss(self, query: str, limit: int) -> List[Any]:
        """Fallback: scrape YouTube RSS feed for trending/search results."""
        try:
            url = f"https://www.youtube.com/results?search_query={requests.utils.quote(query)}"
            resp = requests.get(url, timeout=10, headers={
                "User-Agent": "Mozilla/5.0 (compatible; VisionI-Research/1.0)"
            })
            if resp.status_code != 200:
                return []
            # Extract video IDs from page
            import re
            video_ids = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', resp.text)
            unique_ids = list(dict.fromkeys(video_ids))[:limit]
            return [
                {"id": vid, "title": f"YouTube video {vid}",
                 "webpage_url": f"https://youtube.com/watch?v={vid}"}
                for vid in unique_ids
            ]
        except Exception as exc:
            self.logger.warning("YouTube RSS fallback failed: %s", exc)
            return []

    def normalize(self, item: Any) -> VisionEvent:
        vid_id = item.get("id") or utcnow_iso()
        title  = item.get("title") or "Untitled"

        # yt-dlp returns upload_date as YYYYMMDD string
        raw_date = item.get("upload_date") or item.get("timestamp")

        return VisionEvent(
            event_id   = stable_id(self.source_name, vid_id),
            source     = self.source_name,
            source_id  = vid_id,
            event_type = "video",
            title      = title,
            description= (item.get("description") or "")[:500],
            body       = item.get("description") or "",
            url        = item.get("webpage_url") or f"https://youtube.com/watch?v={vid_id}",
            language   = item.get("language") or "en",
            author     = item.get("uploader") or item.get("channel"),
            timestamp  = to_iso(raw_date),
            ingest_time= utcnow_iso(),
            actors     = [],
            location   = None,
            sentiment  = None,  # filled by NLP pipeline
            tags       = item.get("tags") or [],
            extras     = {
                "view_count":     item.get("view_count"),
                "like_count":     item.get("like_count"),
                "comment_count":  item.get("comment_count"),
                "duration":       item.get("duration"),
                "channel_id":     item.get("channel_id"),
                "thumbnail":      item.get("thumbnail"),
            },
            raw = item,
        )

class SocialExtractor:
    """
    Collects from social sources in one call.
    Not a BaseExtractor subclass â€” it delegates to concrete extractors.

    Default sources: Reddit only (OSINT-targeted subreddits).
    YouTube is intentionally excluded from scheduled ingest to avoid
    entertainment noise â€” use YouTubeExtractor directly for on-demand
    per-event correlation only.

    Usage (scheduled ingest â€” OSINT Reddit only):
        se     = SocialExtractor()
        events = se.collect(query="Iran sanctions", limit=20)

    Usage (on-demand correlation â€” all platforms, site-wide):
        se = SocialExtractor(sources=[RedditExtractor(), YouTubeExtractor()],
                             osint_only=False)
        events = se.collect(query="Iran blockade Hormuz", limit=20)
    """

    def __init__(
        self,
        sources: Optional[List[BaseExtractor]] = None,
        osint_only: bool = True,
    ) -> None:
        # Default: Reddit only with OSINT subreddit targeting
        self.sources    = sources or [RedditExtractor()]
        self.osint_only = osint_only

    def collect(self, query: str, limit: int = 20) -> List[VisionEvent]:
        all_events: List[VisionEvent] = []

        for src in self.sources:
            try:
                # Pass osint_only flag to Reddit; others ignore it
                if isinstance(src, RedditExtractor):
                    events = src.run(query=query, limit=limit, osint_only=self.osint_only)
                else:
                    events = src.run(query=query, limit=limit)
                all_events.extend(events)
            except Exception as exc:
                src.logger.error("collect() failed for %s: %s", src.source_name, exc)

        return all_events

