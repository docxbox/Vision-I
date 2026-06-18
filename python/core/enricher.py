"""
core/enricher.py
─────────────────
Article body enrichment.

Downloads full article text for events where the body is missing or too short.
Skips sources that don't have fetchable article URLs (live data streams).

Usage (standalone):
    enricher = Enricher()
    enriched_events = enricher.enrich(events)

Usage (FastAPI — in a thread pool):
    enriched = await run_in_executor(None, enricher.enrich, events)
"""

import logging
import time
from typing import List, Optional

from newspaper import Article, Config

from core.schema import VisionEvent

logger = logging.getLogger("vision_i.enricher")

# Sources where scraping the URL makes no sense (live telemetry, market data)
_SKIP_SOURCES = {"opensky", "yahoo_finance"}

# Minimum body length — events shorter than this get enrichment attempted
_MIN_BODY_LEN = 150

_newspaper_config = Config()
_newspaper_config.browser_user_agent = "VisionI-Enricher/1.0"
_newspaper_config.request_timeout    = 8
_newspaper_config.fetch_images       = False


class Enricher:
    """
    Fills in missing body text by scraping article URLs.

    params:
        min_body_len   int   body shorter than this triggers a scrape attempt
        delay          float seconds between scrape requests (be polite)
        skip_sources   set   source names to never scrape
    """

    def __init__(
        self,
        min_body_len:  int  = _MIN_BODY_LEN,
        delay:         float = 0.5,
        skip_sources: Optional[set] = None,
    ) -> None:
        self.min_body_len  = min_body_len
        self.delay         = delay
        self.skip_sources  = skip_sources or _SKIP_SOURCES

    def enrich(self, events: List[VisionEvent]) -> List[VisionEvent]:
        """
        Mutates events in-place, returning the same list for chaining.
        """
        enriched_count = 0

        for i, event in enumerate(events):
            source = event.get("source", "")
            body   = event.get("body") or ""
            url    = event.get("url")

            if source in self.skip_sources:
                continue

            if len(body) >= self.min_body_len:
                continue

            if not url:
                continue

            time.sleep(self.delay)
            full_text = self._scrape(url)

            if full_text:
                event["body"] = full_text
                enriched_count += 1
                logger.debug("Enriched [%d/%d]: %s", i + 1, len(events), event.get("title", "")[:60])

        logger.info("Enrichment complete: %d/%d events enriched", enriched_count, len(events))
        return events

    def enrich_one(self, event: VisionEvent) -> VisionEvent:
        """Enrich a single event. Useful for on-demand enrichment."""
        return self.enrich([event])[0]

    def _scrape(self, url: str) -> Optional[str]:
        """Download and parse a single URL. Returns body text or None."""
        try:
            article = Article(url, config=_newspaper_config)
            article.download()
            article.parse()
            text = (article.text or "").strip()
            return text if len(text) > 50 else None
        except Exception as exc:
            logger.debug("Scrape failed for %s: %s", url, exc)
            return None
