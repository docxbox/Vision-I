"""
core/schema.py
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
The canonical Vision-I event schema.

Every extractor's normalize() method returns an VisionEvent dict that matches
this shape. Downstream consumers (Orchestrator, FastAPI, NLP pipeline, Neo4j
writer) all operate on this contract â€” they never touch raw source payloads.

Using TypedDict gives us IDE autocompletion and makes the shape explicit
without pulling in Pydantic as a dependency in the extraction layer.
"""

from typing import Any, Dict, List, Optional
from typing_extensions import TypedDict, NotRequired


class Location(TypedDict):
    lat:  Optional[float]
    lon:  Optional[float]
    name: Optional[str]


class Actor(TypedDict):
    name: str
    type: str   # PERSON | ORG | LOC | VEHICLE | UNKNOWN


class Sentiment(TypedDict):
    label: str   # POSITIVE | NEUTRAL | NEGATIVE
    score: float # 0.0 â€“ 1.0


class VisionEvent(TypedDict):
    event_id:   str            # stable_id("source", unique_value)
    source:     str            # e.g. "usgs", "newsapi", "gdelt_doc"
    source_id:  NotRequired[Optional[str]]  # raw ID from the source
    event_type: str            # disaster | news | social | video | market | transport
    title:       str
    description: NotRequired[Optional[str]]
    body:        NotRequired[Optional[str]]  # full text; may be empty pre-enrichment
    url:         NotRequired[Optional[str]]
    language:    NotRequired[str]            # ISO 639-1, default "en"
    author:      NotRequired[Optional[str]]
    timestamp:   str           # ISO 8601 UTC with Z â€” guaranteed string, never None
    ingest_time: str           # when Vision-I processed this record
    actors:    List[Actor]
    location:  Optional[Location]
    sentiment: Optional[Sentiment]
    tags:      NotRequired[List[str]]
    # Each extractor may attach a typed sub-dict for data that doesn't fit the
    # common schema (e.g. flight telemetry, stock prices, social metrics).
    extras: NotRequired[Dict[str, Any]]
    raw: NotRequired[Any]      # kept for debugging; stripped before DB write
    provenance_id: NotRequired[str]   # SHA-256 hash of core payload guaranteeing zero-trust provenance

