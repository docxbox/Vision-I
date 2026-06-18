"""
extractors/bluesky.py
──────────────────────
Bluesky public search extractor — no authentication required.

Uses the public AT Protocol XRPC endpoint:
  https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts
"""

import logging
from typing import Any, Dict, List

logger = logging.getLogger("vision_i.extractors.bluesky")


async def fetch(query: str = "", limit: int = 25, **kwargs) -> Dict[str, Any]:
    """
    Search Bluesky for posts matching ``query``.

    Parameters
    ----------
    query : Search term (required for useful results; empty returns trending).
    limit : Maximum number of posts (capped at 100 by the API).

    Returns
    -------
    dict with keys: source, total, events, error (None on success)
    """
    from core.http_source import fetch_source
    from core.utils import utcnow_iso

    capped_limit = min(max(1, limit), 100)
    url = (
        f"https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts"
        f"?q={query}&limit={capped_limit}"
    )

    result = await fetch_source(url, source_tag="bluesky", timeout=15)

    if result["status"] != "ok":
        logger.warning("bluesky fetch failed: %s", result["error"])
        return {
            "source": "bluesky",
            "total":  0,
            "events": [],
            "error":  result["error"],
        }

    posts: List[Dict] = result["data"].get("posts", []) if isinstance(result["data"], dict) else []
    events: List[Dict[str, Any]] = []

    for p in posts:
        author = p.get("author", {}) if isinstance(p, dict) else {}
        record = p.get("record", {}) if isinstance(p, dict) else {}
        uri    = p.get("uri", "") if isinstance(p, dict) else ""
        post_id = uri.split("/")[-1] if uri else ""
        handle  = author.get("handle", "") if isinstance(author, dict) else ""

        events.append({
            "event_id":   post_id,
            "title":      (record.get("text", "") if isinstance(record, dict) else "")[:200],
            "source":     "bluesky",
            "event_type": "social",
            "timestamp":  (record.get("createdAt", utcnow_iso()) if isinstance(record, dict) else utcnow_iso()),
            "url":        f"https://bsky.app/profile/{handle}/post/{post_id}" if handle and post_id else "",
            "author":     handle,
            "like_count": p.get("likeCount", 0) if isinstance(p, dict) else 0,
            "reply_count": p.get("replyCount", 0) if isinstance(p, dict) else 0,
            "repost_count": p.get("repostCount", 0) if isinstance(p, dict) else 0,
        })

    logger.info("bluesky: fetched %d posts for query=%r", len(events), query)
    return {
        "source": "bluesky",
        "total":  len(events),
        "events": events,
        "error":  None,
    }
