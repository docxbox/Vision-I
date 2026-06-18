"""
core/orchestrator.py
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
The ingestion pipeline coordinator.

Responsibilities:
  - Run all extractors (query-based and live)
  - Merge results into a single sorted event list
  - Deduplicate by event_id
  - Report per-source counts and errors

This class contains zero FastAPI, zero file I/O, zero Flask.
It is a pure Python object that the FastAPI layer calls in a thread pool.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

from core.schema import VisionEvent
from core.source_registry import source_registry
from core.utils import utcnow_iso
from extractors import (
    AISExtractor,
    GDELTExtractor,
    HackerNewsExtractor,
    NewsExtractor,
    OpenSkyExtractor,
    RedditExtractor,
    RSSExtractor,
    SocialExtractor,
    StockExtractor,
    TelegramExtractor,
    USGSExtractor,
    YouTubeExtractor,
)
from extractors.crypto import CryptoExtractor
from extractors.firms import FIRMSExtractor
from extractors.weather import WeatherExtractor
from extractors.who import WHOExtractor

logger = logging.getLogger("vision_i.orchestrator")


class IngestionResult:
    """Return type of Orchestrator.run(). Carries events and per-source metadata."""

    def __init__(self) -> None:
        self.events:      List[VisionEvent] = []
        self.source_counts: Dict[str, int]  = {}
        self.source_errors: Dict[str, str]  = {}
        self.started_at:  str = utcnow_iso()
        self.finished_at: Optional[str] = None
        self.total:       int = 0

    def finalize(self) -> "IngestionResult":
        self.finished_at = utcnow_iso()
        self.total       = len(self.events)
        return self

    def to_dict(self) -> dict:
        return {
            "total":         self.total,
            "source_counts": self.source_counts,
            "source_errors": self.source_errors,
            "started_at":    self.started_at,
            "finished_at":   self.finished_at,
        }


class Orchestrator:
    """
    Coordinates all extractors and produces a merged, deduplicated event list.

    Usage (standalone):
        orc    = Orchestrator()
        result = orc.run(query="Ukraine", limit=10)
        events = result.events

    Usage (FastAPI â€” in a thread pool):
        result = await asyncio.get_running_loop().run_in_executor(
            None, lambda: orchestrator.run(query=query, limit=limit)
        )

    Constructor params:
        news_api_key   str   passed to NewsExtractor (falls back to env var)
        max_workers    int   thread pool size for parallel source fetching (default 6)
    """

    def __init__(
        self,
        news_api_key: Optional[str] = None,
        max_workers:  int   = 6,
    ) -> None:
        self.max_workers = max_workers
        self._health_cache: Dict[str, Dict] = {}
        self._health_cache_ts: float = 0.0

        # Query-based extractors (benefit from a search term)
        self._news       = NewsExtractor(api_key=news_api_key)
        self._gdelt      = GDELTExtractor()
        self._socials    = SocialExtractor()
        self._rss        = RSSExtractor()
        self._hackernews = HackerNewsExtractor()
        self._telegram   = TelegramExtractor()

        # Live/push extractors (no query needed)
        self._usgs    = USGSExtractor()
        self._stocks  = StockExtractor()
        self._crypto  = CryptoExtractor()
        self._opensky = OpenSkyExtractor()
        self._ais     = AISExtractor()
        self._firms   = FIRMSExtractor()
        self._weather = WeatherExtractor()
        self._who     = WHOExtractor()

    def run(
        self,
        query:   str = "world",
        limit:   int = 10,
        sources: Optional[List[str]] = None,
    ) -> IngestionResult:
        """
        Run the full ingestion pipeline.

        params:
            query    keyword query for text sources
            limit    max events per source
            sources  optional allowlist:
                       ["news","reddit","youtube","usgs","stocks","opensky",
                        "rss","hackernews","telegram","who"]
                     None = all sources
        """
        result  = IngestionResult()
        normalized = source_registry.normalize_many(sources)
        allowed = set(normalized) if normalized else None
        query_tasks = []
        if self._should_run("news",        allowed): query_tasks.append(("news",        self._run_news,        query, limit))
        if self._should_run("gdelt",       allowed): query_tasks.append(("gdelt",       self._run_gdelt,       query, limit))
        if self._should_run("socials",     allowed): query_tasks.append(("socials",     self._run_socials,     query, limit))
        if self._should_run("rss",         allowed): query_tasks.append(("rss",         self._run_rss,         query, limit))
        if self._should_run("hackernews",  allowed): query_tasks.append(("hackernews",  self._run_hackernews,  query, limit))
        if self._should_run("telegram",    allowed): query_tasks.append(("telegram",    self._run_telegram,    query, limit))
        if self._should_run("who",         allowed): query_tasks.append(("who",         self._run_who,         query, limit))

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {
                pool.submit(fn, q, lim): name
                for name, fn, q, lim in query_tasks
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    events = future.result()
                    self._merge(result, name, events)
                except Exception as exc:
                    logger.error("[%s] pipeline error: %s", name, exc)
                    result.source_errors[name] = str(exc)
        live_tasks = []
        if self._should_run("usgs",    allowed): live_tasks.append(("usgs",    self._usgs.run,    {"limit": min(limit, 20)}))
        if self._should_run("stocks",  allowed): live_tasks.append(("stocks",  self._stocks.run,  {"limit": limit}))
        if self._should_run("crypto",  allowed): live_tasks.append(("crypto",  self._crypto.run,  {"limit": limit}))
        if self._should_run("opensky", allowed): live_tasks.append(("opensky", self._opensky.run, {"limit": min(limit, 200)}))
        if self._should_run("ais",     allowed): live_tasks.append(("ais",     self._ais.run,     {"limit": min(limit, 300)}))
        if self._should_run("firms",   allowed): live_tasks.append(("firms",   self._firms.run,   {"limit": min(limit, 50)}))
        if self._should_run("nws",     allowed): live_tasks.append(("nws",     self._weather.run, {"limit": min(limit, 30)}))

        with ThreadPoolExecutor(max_workers=len(live_tasks) or 1) as live_pool:
            live_futures = {
                live_pool.submit(run_fn, **kwargs): name
                for name, run_fn, kwargs in live_tasks
            }
            for future in as_completed(live_futures):
                name = live_futures[future]
                try:
                    events = future.result(timeout=50)
                    self._merge(result, name, events)
                except Exception as exc:
                    logger.error("[%s] live source error: %s", name, exc)
                    result.source_errors[name] = str(exc)
        seen:   set = set()
        unique: List[VisionEvent] = []
        for e in result.events:
            eid = e.get("event_id", "")
            if eid not in seen:
                seen.add(eid)
                unique.append(e)

        unique.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
        result.events = unique

        return result.finalize()

    def run_text_only(self, query: str = "world", limit: int = 20) -> IngestionResult:
        """
        Scheduled text ingest: news intelligence sources only.

        Social media (Reddit/YouTube) is intentionally excluded here â€” they
        pollute the event feed with entertainment content when run against
        generic queries.  Use run_social_signals() for targeted social
        correlation on specific topics, or the /events/{id}/social endpoint
        for on-demand per-event enrichment.
        """
        return self.run(
            query=query, limit=limit,
            sources=["news", "gdelt", "rss", "hackernews", "who"],
        )

    def run_social_signals(self, query: str, limit: int = 20) -> IngestionResult:
        """
        Targeted social signal fetch for a specific topic.
        Uses OSINT-curated Reddit plus YouTube for workspace/event-specific
        correlation. This path is intentionally targeted, not generic ingest.
        Call this for per-topic social enrichment, not mass ingest.
        """
        previous_socials = self._socials
        self._socials = SocialExtractor(
            sources=[RedditExtractor(), YouTubeExtractor()],
            osint_only=True,
        )
        try:
            return self.run(
                query=query, limit=limit,
                sources=["socials"],
            )
        finally:
            self._socials = previous_socials

    def run_live_only(self, limit: int = 20) -> IngestionResult:
        """Convenience: only live sources (no query needed)."""
        return self.run(
            query="", limit=limit,
            sources=["usgs", "stocks", "crypto", "opensky", "ais", "firms", "nws"],
        )

    def health(
        self,
        timeout_s: float = 2.0,
        use_cache: bool = True,
        cache_ttl_s: int = 60,
    ) -> Dict:
        """Run health checks on all sources with timeout and caching."""
        now = time.time()
        if use_cache and self._health_cache and (now - self._health_cache_ts) < cache_ttl_s:
            return self._health_cache

        extractors = [
            self._news, self._gdelt, self._usgs,
            self._stocks, self._crypto, self._opensky, self._rss,
            self._ais, self._hackernews, self._telegram,
            self._firms, self._weather, self._who,
        ] + list(self._socials.sources)

        def _safe_health(ext):
            try:
                return ext.health()
            except Exception as exc:
                return {
                    "source": ext.source_name,
                    "status": "error",
                    "detail": str(exc),
                }

        checks: Dict[str, Dict] = {}
        with ThreadPoolExecutor(max_workers=min(8, len(extractors) or 1)) as pool:
            futures = {pool.submit(_safe_health, ext): ext for ext in extractors}
            # Wait for ALL futures concurrently â€” not sequentially â€” so total
            # wall-clock time â‰ˆ timeout_s, not N * timeout_s.
            import concurrent.futures as _cf
            done, not_done = _cf.wait(futures.keys(), timeout=timeout_s)
            for future in not_done:
                future.cancel()
                ext = futures[future]
                checks[ext.source_name] = {
                    "source": ext.source_name,
                    "status": "timeout",
                    "detail": "health check timeout",
                }
            for future in done:
                ext = futures[future]
                try:
                    h = future.result()
                except Exception as exc:
                    h = {
                        "source": ext.source_name,
                        "status": "error",
                        "detail": str(exc),
                    }
                checks[h["source"]] = h

        self._health_cache = checks
        self._health_cache_ts = now
        return checks

    def source_catalog(self) -> dict:
        return source_registry.catalog(self.health())

    @staticmethod
    def _should_run(name: str, allowed: Optional[set]) -> bool:
        return allowed is None or name in allowed

    @staticmethod
    def _merge(result: IngestionResult, name: str, events: List[VisionEvent]) -> None:
        result.events.extend(events)
        result.source_counts[name] = len(events)
        logger.info("[%s] â†’ %d events", name, len(events))

    def _run_news(self, query: str, limit: int) -> List[VisionEvent]:
        return self._news.run(query=query, limit=limit)

    def _run_gdelt(self, query: str, limit: int) -> List[VisionEvent]:
        return self._gdelt.run(query=query, limit=min(limit, 25), apis=["doc", "geo"])

    def _run_socials(self, query: str, limit: int) -> List[VisionEvent]:
        return self._socials.collect(query=query, limit=limit)

    def _run_rss(self, query: str, limit: int) -> List[VisionEvent]:
        return self._rss.run(query=query, limit=limit)

    def _run_hackernews(self, query: str, limit: int) -> List[VisionEvent]:
        return self._hackernews.run(query=query, limit=min(limit, 25))

    def _run_telegram(self, query: str, limit: int) -> List[VisionEvent]:
        return self._telegram.run(query=query, limit=limit)

    def _run_who(self, query: str, limit: int) -> List[VisionEvent]:
        return self._who.run(limit=limit)

