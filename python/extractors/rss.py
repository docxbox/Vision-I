"""
extractors/rss.py
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Multi-feed RSS / Atom extractor.

Polls a configurable list of RSS feeds including:
  - Major international news agencies (BBC, Reuters, AP, Al Jazeera, Xinhua)
  - Official government / institutional feeds (UN, State Dept, EU, NATO)
  - Intelligence / security feeds (Bellingcat, ACLED, ReliefWeb)
  - Regional outlets for non-Western perspectives

No API key required for any of these feeds.
Uses the `feedparser` library for robust parsing of both RSS 2.0 and Atom 1.0.

fetch() params:
    feeds       list  override default feed list
    limit       int   max articles per feed (default 15)
    query       str   optional keyword filter applied to title/summary
"""

import hashlib
import re
import time
from typing import Any, Dict, List, Optional

from core.base import BaseExtractor
from core.market_entities import (
    detect_market_entities,
    market_actor_payloads,
    market_entities_from_symbols,
    merge_market_matches,
)
from core.schema import VisionEvent
from core.utils import stable_id, to_iso, utcnow_iso
# Format: (label, url, region, language)
DEFAULT_FEEDS: List[Dict[str, str]] = [
    {"label": "Reuters",        "url": "https://feeds.reuters.com/reuters/topNews",          "region": "global", "lang": "en"},
    {"label": "AP News",        "url": "https://rsshub.app/apnews/topics/apf-topnews",       "region": "global", "lang": "en"},
    {"label": "BBC World",      "url": "http://feeds.bbci.co.uk/news/world/rss.xml",          "region": "global", "lang": "en"},
    {"label": "Al Jazeera",     "url": "https://www.aljazeera.com/xml/rss/all.xml",           "region": "MENA",   "lang": "en"},
    {"label": "Xinhua",         "url": "http://www.xinhuanet.com/english/rss/worldrss.xml",   "region": "Asia",   "lang": "en"},
    {"label": "RT",             "url": "https://www.rt.com/rss/",                             "region": "global", "lang": "en"},
    {"label": "DW News",        "url": "https://rss.dw.com/rdf/rss-en-world",                 "region": "Europe", "lang": "en"},
    {"label": "France24",       "url": "https://www.france24.com/en/rss",                     "region": "Europe", "lang": "en"},
    {"label": "NHK World",      "url": "https://www3.nhk.or.jp/rss/news/cat6.xml",            "region": "Asia",   "lang": "en"},
    {"label": "South China MP", "url": "https://www.scmp.com/rss/91/feed",                    "region": "Asia",   "lang": "en"},
    {"label": "The Guardian",   "url": "https://www.theguardian.com/world/rss",               "region": "global", "lang": "en"},
    {"label": "UN News",        "url": "https://news.un.org/feed/subscribe/en/news/all/rss.xml", "region": "global", "lang": "en"},
    {"label": "NATO News",      "url": "https://www.nato.int/cps/en/natohq/news.rss",         "region": "global", "lang": "en"},
    {"label": "EU Council",     "url": "https://www.consilium.europa.eu/en/press/press-releases/rss/", "region": "Europe", "lang": "en"},
    {"label": "US State Dept",  "url": "https://www.state.gov/rss-feeds/press-releases/",     "region": "Americas", "lang": "en"},
    {"label": "White House",    "url": "https://www.whitehouse.gov/feed/",                    "region": "Americas", "lang": "en"},
    {"label": "ReliefWeb",      "url": "https://reliefweb.int/headlines/rss.xml",             "region": "global", "lang": "en"},
    {"label": "ACLED",          "url": "https://acleddata.com/feed/",                         "region": "global", "lang": "en"},
    {"label": "OSINT Combine",  "url": "https://www.osintcombine.com/feed",                   "region": "global", "lang": "en"},
    {"label": "Lawfare",        "url": "https://www.lawfaremedia.org/feed",                   "region": "global", "lang": "en"},
    {"label": "Mercopress",     "url": "https://en.mercopress.com/rss.xml",                   "region": "LatAm",  "lang": "en"},
    {"label": "AllAfrica",      "url": "https://allafrica.com/tools/headlines/rdf/latest/headlines.rdf", "region": "Africa", "lang": "en"},
    {"label": "CNBC World",     "url": "https://www.cnbc.com/id/100727362/device/rss/rss.html", "region": "global", "lang": "en"},
    {"label": "Bloomberg Mrkts","url": "https://feeds.bloomberg.com/markets/news.rss",          "region": "global", "lang": "en"},
    {"label": "Reuters Finance", "url": "https://feeds.reuters.com/reuters/businessNews",       "region": "global", "lang": "en"},
    {"label": "Defense News",   "url": "https://www.defensenews.com/arc/outboundfeeds/rss/",   "region": "global", "lang": "en"},
    {"label": "War on the Rocks","url": "https://warontherocks.com/feed/",                     "region": "global", "lang": "en"},
    {"label": "The Drive/War Zone","url": "https://www.thedrive.com/the-war-zone/rss",         "region": "global", "lang": "en"},
    {"label": "Foreign Affairs", "url": "https://www.foreignaffairs.com/rss.xml",              "region": "global", "lang": "en"},
    {"label": "Radio Free Europe","url": "https://www.rferl.org/api/eprss/?language=EN&tmsec=rl_news", "region": "Europe", "lang": "en"},
    {"label": "VOA News",       "url": "https://feeds.voanews.com/voaenglish",                 "region": "global", "lang": "en"},
    {"label": "Global Voices",  "url": "https://globalvoices.org/feed/",                       "region": "global", "lang": "en"},
    {"label": "Bellingcat",     "url": "https://www.bellingcat.com/feed/",                     "region": "global", "lang": "en"},
    {"label": "Crisis Group",   "url": "https://www.crisisgroup.org/rss.xml",                  "region": "global", "lang": "en"},
]


def _is_market_feed(label: str) -> bool:
    normalized = (label or "").lower()
    return any(token in normalized for token in ("bloomberg", "finance", "cnbc", "reuters"))


def _ticker_symbols_from_tags(tags: List[str]) -> List[str]:
    symbols: List[str] = []
    for tag in tags or []:
        text = str(tag or "").strip()
        match = re.match(r"^[A-Z]{2,5}:([A-Z.]{1,6})$", text)
        if match:
            symbols.append(match.group(1).replace(".", "-"))
        elif re.match(r"^\$?[A-Z]{1,6}$", text):
            symbols.append(text.lstrip("$"))
    return symbols


class RSSExtractor(BaseExtractor):
    """
    Polls multiple RSS/Atom feeds and returns normalised VisionEvents.

    Each article is tagged with its feed label, region, and language.
    Duplicate URLs across feeds are de-duplicated by event_id.
    """

    source_name = "rss"

    _HEADERS = {"User-Agent": "VisionI-RSS/1.0 (intelligence-research-platform)"}

    def __init__(self, feeds: Optional[List[Dict[str, str]]] = None) -> None:
        super().__init__()
        self._feeds = feeds or DEFAULT_FEEDS

    def fetch(
        self,
        feeds: Optional[List[Dict[str, str]]] = None,
        limit: int = 15,
        query: str = "",
        **_,
    ) -> List[Dict]:
        try:
            import feedparser
        except ImportError:
            self.logger.error("feedparser not installed. Run: pip install feedparser")
            return []

        active_feeds = feeds or self._feeds
        results: List[Dict] = []
        query_lower = query.lower() if query else ""

        for feed_meta in active_feeds:
            url   = feed_meta.get("url", "")
            label = feed_meta.get("label", url)
            if not url:
                continue

            try:
                parsed = feedparser.parse(url, request_headers=self._HEADERS)
                entries = parsed.entries or []

                count = 0
                for entry in entries:
                    if count >= limit:
                        break

                    title   = (getattr(entry, "title",   None) or "").strip()
                    summary = (getattr(entry, "summary", None) or "").strip()

                    # Keyword filter â€” split query into individual words and
                    # require at least ONE word (â‰¥4 chars) to appear in the
                    # article.  Avoids the broken exact-phrase match that killed
                    # all RSS results when the query was "world news politics
                    # security" (a phrase that never appears verbatim).
                    if query_lower:
                        keywords = [w for w in query_lower.split() if len(w) >= 4]
                        if keywords:
                            text_lower = (title + " " + summary).lower()
                            if not any(kw in text_lower for kw in keywords):
                                continue

                    # Attach feed metadata for normalize()
                    item: Dict = {
                        "_feed_label":  label,
                        "_feed_region": feed_meta.get("region", "global"),
                        "_feed_lang":   feed_meta.get("lang", "en"),
                        "_feed_url":    url,
                        "title":        title,
                        "summary":      summary,
                        "link":         getattr(entry, "link",        None),
                        "published":    getattr(entry, "published",   None),
                        "updated":      getattr(entry, "updated",     None),
                        "author":       getattr(entry, "author",      None),
                        "tags":         [t.get("term", "") for t in getattr(entry, "tags", []) if isinstance(t, dict)],
                        "id":           getattr(entry, "id",          None),
                    }
                    results.append(item)
                    count += 1

            except Exception as exc:
                self.logger.warning("RSS feed '%s' failed: %s", label, exc)
            else:
                if entries:
                    self.logger.debug("RSS '%s': %d entries, %d kept", label, len(entries), count)

        self.logger.info("RSS total raw items: %d (from %d feeds)", len(results), len(active_feeds))
        return results

    def normalize(self, item: Any) -> VisionEvent:
        label  = item.get("_feed_label", "rss")
        region = item.get("_feed_region", "global")
        lang   = item.get("_feed_lang", "en")

        link    = item.get("link") or ""
        title   = item.get("title") or "Untitled"
        summary = item.get("summary") or ""
        pub     = item.get("published") or item.get("updated")
        item_id = item.get("id") or link or title

        # Normalise label to a stable source name key
        source_key = f"rss_{label.lower().replace(' ', '_')}"
        tags = item.get("tags") or []
        tag_matches = market_entities_from_symbols(_ticker_symbols_from_tags(tags)) if _is_market_feed(label) else []
        text_matches = detect_market_entities(title, summary) if _is_market_feed(label) else []
        market_matches = merge_market_matches(text_matches, tag_matches)
        market_symbols = [m["symbol"].lower() for m in market_matches if m.get("symbol")]
        market_names = [m["name"].lower() for m in market_matches if m.get("name")]

        return VisionEvent(
            event_id   = stable_id(source_key, item_id),
            source     = source_key,
            source_id  = link or item_id,
            event_type = "news",
            title      = title,
            description= summary[:500],
            body       = summary,
            url        = link or None,
            language   = lang,
            author     = item.get("author"),
            timestamp  = to_iso(pub),
            ingest_time= utcnow_iso(),
            actors     = market_actor_payloads(market_matches),
            location   = None,
            sentiment  = None,
            tags       = [
                *tags,
                *(["market", "finance"] if market_matches else []),
                *market_symbols,
                *market_names,
            ],
            extras     = {
                "feed_label":  label,
                "feed_region": region,
                "feed_url":    item.get("_feed_url"),
                "market_entities": market_matches,
            },
            raw = item,
        )

    def health(self) -> Dict:
        """Quick check: parse the first feed only."""
        try:
            import feedparser
            first = self._feeds[0]
            parsed = feedparser.parse(first["url"], request_headers=self._HEADERS)
            count = len(parsed.entries or [])
            return {"source": self.source_name, "status": "ok" if count > 0 else "empty",
                    "sample_count": count, "feed": first["label"]}
        except Exception as exc:
            return {"source": self.source_name, "status": "error", "detail": str(exc)}

