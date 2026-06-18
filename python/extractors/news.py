"""
extractors/news.py
──────────────────
Fetches articles from NewsAPI.org.

Docs: https://newsapi.org/docs/endpoints/everything
Free tier: 100 req/day, 1 month of history.
"""

import os
import time
from datetime import datetime, timedelta
from typing import Any, List, Optional

import requests

from core.base import BaseExtractor
from core.schema import VisionEvent
from core.utils import stable_id, to_iso, utcnow_iso

_ENDPOINT = "https://newsapi.org/v2/everything"


class NewsExtractor(BaseExtractor):
    """
    Fetches news articles for a keyword query.

    fetch() params:
        query       str   search keywords
        days_back   int   how many days of history to fetch (default 1)
        limit       int   max articles (default 50, max 100 on free tier)
        language    str   ISO 639-1 language code (default "en")
        sort_by     str   "publishedAt" | "relevancy" | "popularity"
    """

    source_name = "newsapi"

    def __init__(self, api_key: Optional[str] = None) -> None:
        super().__init__()
        self.api_key = api_key or os.getenv("NEWSAPI_KEY", "")
        if not self.api_key:
            self.logger.warning(
                "NEWSAPI_KEY not set. Set the environment variable before fetching."
            )

    def fetch(
        self,
        query: str = "world news",
        days_back: int = 1,
        limit: int = 50,
        language: str = "en",
        sort_by: str = "publishedAt",
        **_,
    ) -> List[Any]:
        if not self.api_key:
            self.logger.error("Cannot fetch: NEWSAPI_KEY is not set.")
            return []

        from_date = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        to_date   = datetime.utcnow().strftime("%Y-%m-%d")

        # NewsAPI rejects empty query — use a broad default
        effective_query = query.strip() if query and query.strip() else "world news politics security conflict"

        params = {
            "q":        effective_query,
            "from":     from_date,
            "to":       to_date,
            "pageSize": min(limit, 100),
            "sortBy":   sort_by,
            "language": language,
            "apiKey":   self.api_key,
        }

        try:
            from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

            @retry(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=1, min=2, max=10),
                retry=retry_if_exception_type(requests.RequestException),
                reraise=True
            )
            def _do_request():
                return requests.get(_ENDPOINT, params=params, timeout=10)

            resp = _do_request()

            if resp.status_code == 426:
                self.logger.warning("NewsAPI: upgrade required (free tier limit hit)")
                return []
            if resp.status_code == 429:
                self.logger.warning("NewsAPI: rate limited, sleeping 60s")
                time.sleep(60)
                return []

            resp.raise_for_status()
            data = resp.json()

            if data.get("status") != "ok":
                self.logger.error("NewsAPI error: %s", data.get("message"))
                return []

            return data.get("articles", [])

        except requests.RequestException as exc:
            self.logger.error("NewsAPI fetch failed: %s", exc)
            return []

    def normalize(self, item: Any) -> VisionEvent:
        url   = item.get("url") or ""
        title = item.get("title") or "Untitled"

        return VisionEvent(
            event_id   = stable_id(self.source_name, url or title),
            source     = self.source_name,
            source_id  = url,
            event_type = "news",
            title      = title,
            description= item.get("description") or "",
            body       = (item.get("content") or "")[:500],  # NewsAPI truncates at 200 chars on free
            url        = url,
            language   = "en",
            author     = item.get("author") or (item.get("source") or {}).get("name"),
            timestamp  = to_iso(item.get("publishedAt")),
            ingest_time= utcnow_iso(),
            actors     = [],
            location   = None,
            sentiment  = None,  # filled by NLP pipeline
            tags       = [],
            extras     = {
                "source_name": (item.get("source") or {}).get("name"),
                "source_id":   (item.get("source") or {}).get("id"),
            },
            raw = item,
        )
