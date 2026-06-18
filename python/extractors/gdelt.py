"""
extractors/gdelt.py
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Fetches data from the GDELT Project v2 APIs.

Four endpoints, one unified client:
  - Doc API     â†’ news article metadata
  - Geo API     â†’ geolocated events
  - Context API â†’ sentence-level context
  - TV API      â†’ television news clips

Docs: https://blog.gdeltproject.org/gdelt-2-0-our-global-archive-of-the-past-quarter-century/
No API key required. Be polite: default delay=2s between requests.
"""

import time
from typing import Any, Dict, List, Optional

import requests

from core.base import BaseExtractor
from core.schema import VisionEvent
from core.utils import stable_id, safe_get, to_iso, utcnow_iso

_DOC_URL     = "https://api.gdeltproject.org/api/v2/doc/doc"
_GEO_URL     = "https://api.gdeltproject.org/api/v2/geo/geo"
_CONTEXT_URL = "https://api.gdeltproject.org/api/v2/context/context"
_TV_URL      = "https://api.gdeltproject.org/api/v2/tv/tvsearch"


class GDELTExtractor(BaseExtractor):
    """
    Queries the GDELT v2 APIs and returns unified VisionEvent records.

    fetch() params:
        query       str   GDELT query string
        limit       int   max records per sub-API (default 100)
        apis        list  which sub-APIs to call: ["doc","geo","context","tv"]
                          (default: ["doc"])
        delay       float seconds between requests (default 2.0)
    """

    source_name = "gdelt"

    def __init__(
        self,
        delay: float = 0.5,
        timeout: int = 20,
        cooldown_seconds: int = 300,
        doc_limit_cap: int = 50,
    ) -> None:
        super().__init__()
        self.delay   = delay
        self.timeout = timeout
        self.cooldown_seconds = max(60, cooldown_seconds)
        self.doc_limit_cap = max(1, doc_limit_cap)
        self._cooldown_until = 0.0
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "VisionI-GDELT/2.0"})

    def _get(self, url: str, params: dict, label: str, retries: int = 1) -> Optional[dict]:
        if self._is_cooling_down():
            remaining = int(max(1, self._cooldown_until - time.monotonic()))
            self.logger.warning("GDELT %s skipped during cooldown (%ds remaining)", label, remaining)
            return None

        for attempt in range(retries + 1):
            try:
                resp = self._session.get(url, params=params, timeout=self.timeout)
                time.sleep(self.delay)

                if resp.status_code == 429:
                    wait = max(self._retry_after_seconds(resp.headers.get("Retry-After")), 15)
                    if attempt < retries:
                        self.logger.warning(
                            "GDELT %s rate limited (%d/%d) â€” retrying in %ds",
                            label, attempt + 1, retries + 1, wait,
                        )
                        time.sleep(wait)
                        continue

                    self._start_cooldown(wait, label, "rate limited")
                    return None

                if resp.status_code in (404, 410):
                    self.logger.warning("GDELT %s unavailable (%d); skipping endpoint", label, resp.status_code)
                    return None

                resp.raise_for_status()
                data = resp.json()
                # GDELT sometimes returns a valid 200 with an empty/error body
                if not data or (isinstance(data, dict) and str(data.get("status", "")).lower() == "error"):
                    raise ValueError("Empty response body")
                return data
            except (requests.RequestException, ValueError) as exc:
                if attempt < retries:
                    wait = 2 * (attempt + 1)   # 2s, then 4s
                    self.logger.warning("GDELT %s attempt %d failed (%s) â€” retrying in %ds",
                                        label, attempt + 1, exc, wait)
                    time.sleep(wait)
                else:
                    self.logger.error("GDELT %s failed after %d attempts: %s",
                                      label, retries + 1, exc)
        return None

    def _is_cooling_down(self) -> bool:
        return time.monotonic() < self._cooldown_until

    def _retry_after_seconds(self, retry_after: Optional[str]) -> int:
        if retry_after:
            try:
                return max(int(float(retry_after)), self.cooldown_seconds)
            except ValueError:
                pass
        return self.cooldown_seconds

    def _start_cooldown(self, seconds: int, label: str, reason: str) -> None:
        wait = max(seconds, self.cooldown_seconds)
        self._cooldown_until = max(self._cooldown_until, time.monotonic() + wait)
        self.logger.warning("GDELT %s entering cooldown for %ds (%s)", label, wait, reason)

    def _fetch_doc(self, query: str, limit: int) -> List[Dict]:
        maxrecords = max(1, min(limit, self.doc_limit_cap))
        data = self._get(_DOC_URL, {
            "query": query,
            "mode": "artlist",
            "maxrecords": maxrecords,
            "sort": "datedesc",
            "format": "json"
        }, "DocAPI")
        if not data:
            return []
        return (
            data.get("articles")
            or data.get("artlist", {}).get("articles")
            or data.get("results")
            or []
        )

    def _fetch_geo(self, query: str) -> List[Dict]:
        data = self._get(_GEO_URL, {"query": query, "format": "json"}, "GeoAPI")
        if not data:
            return []
        return data.get("features") or data.get("data") or []

    def _fetch_context(self, query: str, limit: int) -> List[Dict]:
        data = self._get(_CONTEXT_URL, {
            "query": query, "mode": "context",
            "maxrecords": limit,
        }, "ContextAPI")
        if not data:
            return []
        # GDELT Context API uses several different response keys across versions
        raw = (
            data.get("sentences")
            or data.get("context")
            or data.get("snippets")
            or data.get("results")
            or []
        )
        # Some responses wrap items; ensure we always return a list of dicts
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
        return []

    def _fetch_tv(self, query: str, limit: int) -> List[Dict]:
        data = self._get(_TV_URL, {
            "query": query, "format": "json", "maxrecords": limit
        }, "TVAPI")
        if not data:
            return []
        return data.get("results") or []

    def fetch(
        self,
        query: str = "world",
        limit: int = 100,
        apis: Optional[List[str]] = None,
        delay: Optional[float] = None,
        **_,
    ) -> List[Dict]:
        """
        Returns a list of raw dicts, each tagged with "_gdelt_type" so
        normalize() knows which sub-schema to apply.
        """
        if delay is not None:
            self.delay = delay

        active = set(apis or ["doc"])
        raw: List[Dict] = []
        safe_query = (query or "world").strip() or "world"

        if "doc" in active:
            for item in self._fetch_doc(safe_query, limit):
                item["_gdelt_type"] = "doc"
                raw.append(item)
            self.logger.info("GDELT Doc: %d items", sum(1 for i in raw if i.get("_gdelt_type") == "doc"))

        if "geo" in active:
            for item in self._fetch_geo(safe_query):
                item["_gdelt_type"] = "geo"
                raw.append(item)

        if "context" in active:
            for item in self._fetch_context(safe_query, limit):
                item["_gdelt_type"] = "context"
                raw.append(item)

        if "tv" in active:
            for item in self._fetch_tv(safe_query, limit):
                item["_gdelt_type"] = "tv"
                raw.append(item)

        return raw

    def normalize(self, item: Any) -> VisionEvent:
        gdelt_type = item.get("_gdelt_type", "doc")
        dispatch   = {
            "doc":     self._normalize_doc,
            "geo":     self._normalize_geo,
            "context": self._normalize_context,
            "tv":      self._normalize_tv,
        }
        return dispatch.get(gdelt_type, self._normalize_doc)(item)

    def _normalize_doc(self, item: Dict) -> VisionEvent:
        sid = item.get("url") or item.get("id") or str(item.get("seendate", utcnow_iso()))
        return VisionEvent(
            event_id   = stable_id("gdelt_doc", sid),
            source     = "gdelt_doc",
            source_id  = sid,
            event_type = "news",
            title      = item.get("title") or item.get("seentitle") or sid[:80],
            description= "",
            body       = item.get("body") or "",
            url        = item.get("url"),
            language   = item.get("language") or "en",
            author     = item.get("source") or item.get("author"),
            timestamp  = to_iso(item.get("seendate")),
            ingest_time= utcnow_iso(),
            actors     = [],
            location   = None,
            sentiment  = None,
            tags       = self._parse_themes(item.get("themes")),
            extras     = {"domain": item.get("domain"), "sourcecountry": item.get("sourcecountry")},
            raw        = item,
        )

    def _normalize_geo(self, item: Dict) -> VisionEvent:
        coords = safe_get(item, "geometry", "coordinates")
        lat, lon = None, None
        if coords and isinstance(coords, list) and len(coords) >= 2:
            lon, lat = coords[0], coords[1]
        else:
            lat = item.get("latitude")
            lon = item.get("longitude")

        sid = item.get("name") or item.get("id") or utcnow_iso()
        return VisionEvent(
            event_id   = stable_id("gdelt_geo", sid),
            source     = "gdelt_geo",
            source_id  = sid,
            event_type = "news",
            title      = item.get("name") or "GDELT Geo Event",
            description= item.get("description") or "",
            body       = item.get("description") or "",
            url        = None,
            language   = "en",
            author     = "GDELT Geo",
            timestamp  = to_iso(item.get("date")),
            ingest_time= utcnow_iso(),
            actors     = [],
            location   = {"lat": lat, "lon": lon, "name": item.get("name")},
            sentiment  = None,
            tags       = item.get("themes") or [],
            extras     = {"count": item.get("count")},
            raw        = item,
        )

    def _normalize_context(self, item: Dict) -> VisionEvent:
        sentence = item.get("sentence") or ""
        sid      = item.get("url") or item.get("id") or str(item.get("date", utcnow_iso()))
        return VisionEvent(
            event_id   = stable_id("gdelt_ctx", sid),
            source     = "gdelt_context",
            source_id  = sid,
            event_type = "news",
            title      = sentence[:100] or "GDELT Context",
            description= sentence,
            body       = sentence,
            url        = item.get("url"),
            language   = item.get("language") or "en",
            author     = item.get("source"),
            timestamp  = to_iso(item.get("date")),
            ingest_time= utcnow_iso(),
            actors     = [],
            location   = None,
            sentiment  = None,
            tags       = item.get("themes") or [],
            extras     = {},
            raw        = item,
        )

    def _normalize_tv(self, item: Dict) -> VisionEvent:
        sid = item.get("url") or item.get("id") or utcnow_iso()
        return VisionEvent(
            event_id   = stable_id("gdelt_tv", sid),
            source     = "gdelt_tv",
            source_id  = sid,
            event_type = "news",
            title      = item.get("title") or item.get("programtitle") or "TV Segment",
            description= item.get("snippet") or item.get("description") or "",
            body       = item.get("snippet") or item.get("description") or "",
            url        = item.get("url"),
            language   = item.get("language") or "en",
            author     = item.get("station") or item.get("network"),
            timestamp  = to_iso(item.get("date")),
            ingest_time= utcnow_iso(),
            actors     = [],
            location   = None,
            sentiment  = None,
            tags       = item.get("themes") or [],
            extras     = {
                "station":  item.get("station"),
                "network":  item.get("network"),
                "show":     item.get("programtitle"),
            },
            raw = item,
        )

    @staticmethod
    def _parse_themes(themes: Any) -> List[str]:
        if not themes:
            return []
        if isinstance(themes, list):
            return themes
        if isinstance(themes, str):
            return [t.strip() for t in themes.split(";") if t.strip()]
        return []

    def health(self) -> Dict:
        """Cheaper health check â€” just hit the Doc API with a tiny query."""
        try:
            items = self._fetch_doc("world", limit=1)
            return {"source": self.source_name, "status": "ok", "sample_count": len(items)}
        except Exception as exc:
            return {"source": self.source_name, "status": "error", "detail": str(exc)}

