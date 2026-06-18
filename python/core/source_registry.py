"""
core/source_registry.py
-----------------------
Central registry for all ingestible OSINT sources.

This lets Vision-I reason about sources as platform capabilities rather than
hardcoded route/controller lists. The registry powers:
  - ingest request validation
  - source catalog APIs
  - operator UX for source interrogation
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional


@dataclass(frozen=True)
class SourceParameter:
    name: str
    kind: str
    required: bool = False
    description: str = ""
    default: str | None = None


@dataclass(frozen=True)
class SourceDefinition:
    key: str
    label: str
    category: str
    extractor: str
    modes: List[str]
    supports_query: bool
    nlp_mode: str
    description: str
    route: str
    aliases: List[str] = field(default_factory=list)
    requires_credentials: bool = False
    parameters: List[SourceParameter] = field(default_factory=list)

    def to_catalog_item(self, health: Optional[dict] = None) -> dict:
        item = asdict(self)
        item["health"] = health or {"status": "unknown"}
        return item


class SourceRegistry:
    def __init__(self, definitions: Iterable[SourceDefinition]) -> None:
        self._definitions: Dict[str, SourceDefinition] = {
            definition.key: definition for definition in definitions
        }
        self._aliases: Dict[str, str] = {}
        for definition in definitions:
            self._aliases[definition.key] = definition.key
            for alias in definition.aliases:
                self._aliases[alias] = definition.key

    def all(self) -> List[SourceDefinition]:
        return list(self._definitions.values())

    def get(self, key: str) -> Optional[SourceDefinition]:
        canonical = self.canonicalize(key)
        if canonical is None:
            return None
        return self._definitions.get(canonical)

    def canonicalize(self, key: str) -> Optional[str]:
        return self._aliases.get((key or "").strip().lower())

    def normalize_many(self, keys: Optional[Iterable[str]]) -> List[str]:
        if not keys:
            return []

        normalized: List[str] = []
        for key in keys:
            canonical = self.canonicalize(key)
            if canonical and canonical not in normalized:
                normalized.append(canonical)
        return normalized

    def unknown(self, keys: Optional[Iterable[str]]) -> List[str]:
        if not keys:
            return []
        return [key for key in keys if self.canonicalize(key) is None]

    def catalog(self, health_map: Optional[dict] = None) -> dict:
        health_map = health_map or {}
        items = [
            definition.to_catalog_item(health_map.get(definition.key))
            for definition in self.all()
        ]
        return {
            "total": len(items),
            "sources": items,
        }


def build_source_registry() -> SourceRegistry:
    return SourceRegistry([
        SourceDefinition(
            key="news",
            label="NewsAPI",
            category="news",
            extractor="NewsExtractor",
            modes=["query"],
            supports_query=True,
            nlp_mode="full",
            description="Keyword-driven article ingestion from NewsAPI.",
            route="/sources/news",
            aliases=["newsapi"],
            requires_credentials=True,
            parameters=[
                SourceParameter("query", "string", True, "Search keywords"),
                SourceParameter("limit", "int", False, "Max results", "10"),
                SourceParameter("language", "string", False, "ISO language code", "en"),
                SourceParameter("days_back", "int", False, "History window", "1"),
                SourceParameter("sort_by", "string", False, "publishedAt | relevancy | popularity", "publishedAt"),
            ],
        ),
        SourceDefinition(
            key="gdelt",
            label="GDELT",
            category="news",
            extractor="GDELTExtractor",
            modes=["query"],
            supports_query=True,
            nlp_mode="full",
            description="Global event/news archive via the GDELT v2 APIs.",
            route="/sources/gdelt",
            aliases=["gdelt_doc", "gdelt_geo", "gdelt_context", "gdelt_tv"],
            parameters=[
                SourceParameter("query", "string", True, "GDELT query string"),
                SourceParameter("limit", "int", False, "Max records per API", "25"),
                SourceParameter("apis", "string", False, "doc | geo | context | tv", "doc"),
                SourceParameter("delay", "float", False, "Seconds between requests", "0.5"),
            ],
        ),
        SourceDefinition(
            key="socials",
            label="Reddit",
            category="social",
            extractor="RedditExtractor",
            modes=["query"],
            supports_query=True,
            nlp_mode="full",
            description="Community discourse and emerging social signals from Reddit.",
            route="/sources/reddit",
            aliases=["reddit"],
            parameters=[
                SourceParameter("query", "string", True, "Search keywords"),
                SourceParameter("limit", "int", False, "Max results", "25"),
                SourceParameter("sort", "string", False, "new | hot | relevance | top", "new"),
                SourceParameter("subreddit", "string", False, "Restrict to one subreddit"),
            ],
        ),
        SourceDefinition(
            key="youtube",
            label="YouTube",
            category="social",
            extractor="YouTubeExtractor",
            modes=["query"],
            supports_query=True,
            nlp_mode="full",
            description="Video discovery via yt-dlp search.",
            route="/sources/youtube",
            parameters=[
                SourceParameter("query", "string", True, "Search keywords"),
                SourceParameter("limit", "int", False, "Max results", "10"),
            ],
        ),
        SourceDefinition(
            key="rss",
            label="RSS Feeds",
            category="news",
            extractor="RSSExtractor",
            modes=["query"],
            supports_query=True,
            nlp_mode="full",
            description="Open RSS feed aggregation for flexible OSINT extension.",
            route="/sources/rss",
            parameters=[
                SourceParameter("query", "string", True, "Search keywords"),
                SourceParameter("limit", "int", False, "Max results", "20"),
            ],
        ),
        SourceDefinition(
            key="hackernews",
            label="Hacker News",
            category="community",
            extractor="HackerNewsExtractor",
            modes=["query"],
            supports_query=True,
            nlp_mode="full",
            description="Technology and cyber-adjacent signals from Hacker News.",
            route="/sources/hackernews",
            parameters=[
                SourceParameter("query", "string", True, "Search keywords"),
                SourceParameter("limit", "int", False, "Max results", "20"),
            ],
        ),
        SourceDefinition(
            key="twitter",
            label="Twitter / X",
            category="social",
            extractor="TwitterExtractor",
            modes=["query"],
            supports_query=True,
            nlp_mode="full",
            description="Recent search via the Twitter v2 API. Surfaces social amplification, verified actors, and geotagged posts.",
            route="/sources/twitter",
            aliases=["x"],
            requires_credentials=True,
            parameters=[
                SourceParameter("query", "string", True, "Twitter v2 search query"),
                SourceParameter("limit", "int", False, "Max results (10-100)", "25"),
                SourceParameter("lang", "string", False, "ISO language code", "en"),
            ],
        ),
        SourceDefinition(
            key="telegram",
            label="Telegram",
            category="social",
            extractor="TelegramExtractor",
            modes=["query"],
            supports_query=True,
            nlp_mode="full",
            description="Channel-based social signal monitoring from Telegram.",
            route="/sources/telegram",
            requires_credentials=True,
            parameters=[
                SourceParameter("query", "string", True, "Search keywords"),
                SourceParameter("limit", "int", False, "Max results", "20"),
            ],
        ),
        SourceDefinition(
            key="usgs",
            label="USGS Earthquakes",
            category="geospatial",
            extractor="USGSExtractor",
            modes=["live", "direct"],
            supports_query=False,
            nlp_mode="none",
            description="Structured seismic events from the USGS feed.",
            route="/sources/usgs",
            parameters=[
                SourceParameter("limit", "int", False, "Max results", "10"),
                SourceParameter("min_mag", "float", False, "Minimum magnitude", "4.0"),
                SourceParameter("hours_back", "int", False, "Lookback window", "24"),
            ],
        ),
        SourceDefinition(
            key="stocks",
            label="Yahoo Finance",
            category="market",
            extractor="StockExtractor",
            modes=["live", "direct"],
            supports_query=False,
            nlp_mode="none",
            description="Market-moving asset signals from tracked tickers.",
            route="/sources/stocks",
            aliases=["yahoo_finance"],
            parameters=[
                SourceParameter("tickers", "string", False, "Comma-separated ticker list"),
                SourceParameter("limit", "int", False, "Max results", "20"),
            ],
        ),
        SourceDefinition(
            key="opensky",
            label="OpenSky",
            category="transport",
            extractor="OpenSkyExtractor",
            modes=["live", "direct"],
            supports_query=False,
            nlp_mode="none",
            description="Live aircraft telemetry and transport anomalies.",
            route="/sources/opensky",
            parameters=[
                SourceParameter("limit", "int", False, "Max results", "50"),
                SourceParameter("callsign", "string", False, "Filter by callsign"),
                SourceParameter("icao24", "string", False, "Filter by ICAO 24-bit hex"),
                SourceParameter("airborne_only", "bool", False, "Only airborne aircraft"),
                SourceParameter("on_ground_only", "bool", False, "Only on-ground aircraft"),
            ],
        ),
        SourceDefinition(
            key="firms",
            label="NASA FIRMS",
            category="geospatial",
            extractor="FIRMSExtractor",
            modes=["live"],
            supports_query=False,
            nlp_mode="none",
            description="Wildfire and thermal anomaly detections.",
            route="",
            requires_credentials=True,
        ),
        SourceDefinition(
            key="ais",
            label="AIS Vessel Tracking",
            category="transport",
            extractor="AISExtractor",
            modes=["live", "direct"],
            supports_query=False,
            nlp_mode="none",
            description="Live vessel telemetry via aisstream.io WebSocket (free API key) or legacy HTTP AIS endpoint.",
            route="/sources/ais",
            requires_credentials=True,
            parameters=[
                SourceParameter("limit", "int", False, "Max results", "50"),
            ],
        ),
        SourceDefinition(
            key="nws",
            label="Weather",
            category="weather",
            extractor="WeatherExtractor",
            modes=["live"],
            supports_query=False,
            nlp_mode="none",
            description="Operational weather alerts and geospatial hazard context.",
            route="",
            aliases=["weather"],
        ),
        SourceDefinition(
            key="who",
            label="WHO",
            category="health",
            extractor="WHOExtractor",
            modes=["query"],
            supports_query=False,
            nlp_mode="light",
            description="Public health and epidemiological intelligence feeds.",
            route="",
        ),
        SourceDefinition(
            key="bluesky",
            label="Bluesky",
            category="social",
            extractor="bluesky.fetch",
            modes=["query", "direct"],
            supports_query=True,
            nlp_mode="full",
            description="Bluesky decentralised social network posts via public AT Protocol API. No credentials required.",
            route="/sources/bluesky",
            aliases=["bsky"],
            requires_credentials=False,
            parameters=[
                SourceParameter("query", "string", False, "Search keywords (empty = trending)", ""),
                SourceParameter("limit", "int", False, "Max results (1-100)", "25"),
            ],
        ),
        SourceDefinition(
            key="cisa_kev",
            label="CISA KEV",
            category="vulnerability",
            extractor="cisa_kev.fetch",
            modes=["live", "direct"],
            supports_query=False,
            nlp_mode="none",
            description="CISA Known Exploited Vulnerabilities catalogue. Updated daily. No credentials required.",
            route="/sources/cisa_kev",
            aliases=["cisa", "kev"],
            requires_credentials=False,
            parameters=[
                SourceParameter("limit", "int", False, "Max vulnerabilities to return", "50"),
            ],
        ),
        SourceDefinition(
            key="treasury",
            label="US Treasury Fiscal Data",
            category="fiscal",
            extractor="treasury.fetch",
            modes=["live", "direct"],
            supports_query=False,
            nlp_mode="none",
            description="US Treasury public fiscal data API (debt, spending, revenue). No credentials required.",
            route="/sources/treasury",
            aliases=["fiscal", "us_treasury"],
            requires_credentials=False,
            parameters=[
                SourceParameter("endpoint", "string", False, "Fiscal Data API endpoint path", "v1/debt/mspd/mspd_table_1"),
                SourceParameter("limit", "int", False, "Max records", "10"),
            ],
        ),
    ])


source_registry = build_source_registry()
