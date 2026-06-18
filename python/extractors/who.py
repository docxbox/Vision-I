"""
extractors/who.py
──────────────────
Fetches disease outbreak news from the World Health Organization RSS feed.

Feed: https://www.who.int/feeds/entity/don/en/rss.xml
Auth: None required.
Data: Global disease outbreaks, public health emergencies.
"""

from typing import Any, Dict, List, Optional

import feedparser

from core.base import BaseExtractor
from core.schema import VisionEvent
from core.utils import stable_id, to_iso, utcnow_iso


_WHO_RSS = "https://www.who.int/feeds/entity/don/en/rss.xml"


class WHOExtractor(BaseExtractor):
    """
    Fetches disease outbreak news from the WHO RSS feed.

    fetch() params:
        limit   int   max items (default 20)
    """

    source_name = "who"

    def fetch(self, limit: int = 20, **_) -> List[Any]:
        try:
            feed = feedparser.parse(_WHO_RSS)
            if feed.bozo and not feed.entries:
                self.logger.warning("WHO: feed parse error: %s", feed.bozo_exception)
                return []

            entries = feed.entries[:limit]
            self.logger.info("WHO: fetched %d outbreak items", len(entries))
            return entries
        except Exception as exc:
            self.logger.error("WHO fetch failed: %s", exc)
            return []

    def normalize(self, item: Any) -> VisionEvent:
        title = getattr(item, "title", "WHO Disease Outbreak") or "WHO Disease Outbreak"
        link = getattr(item, "link", "") or ""
        summary = getattr(item, "summary", "") or ""
        published = getattr(item, "published", "") or ""

        ts = to_iso(published) if published else utcnow_iso()
        eid = stable_id(self.source_name, link or title)

        # Try to extract location from title (common pattern: "Disease - Country")
        location_name = None
        if " - " in title:
            parts = title.split(" - ")
            if len(parts) >= 2:
                location_name = parts[-1].strip()

        # Extract disease name from title
        disease = title.split(" - ")[0].strip() if " - " in title else title

        description = summary[:2000] if summary else title

        return VisionEvent(
            event_id    = eid,
            source      = self.source_name,
            source_id   = link,
            event_type  = "health",
            title       = title[:500],
            description = description,
            body        = description,
            url         = link,
            language    = "en",
            timestamp   = ts,
            ingest_time = utcnow_iso(),
            actors      = [
                {"name": "WHO", "type": "ORG"},
                {"name": disease, "type": "UNKNOWN"},
            ],
            location    = {
                "lat":  None,
                "lon":  None,
                "name": location_name,
            },
            sentiment   = {"label": "NEGATIVE", "score": 0.2},
            tags        = ["health", "disease", "outbreak", "who", "pandemic"],
            extras      = {
                "disease":  disease,
                "location": location_name,
            },
            raw = {"title": title, "link": link, "summary": summary[:500], "published": published},
        )
