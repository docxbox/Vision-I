"""
core/validator.py
──────────────────
Runtime schema validation for VisionEvent dicts.

The core/schema.py TypedDict provides IDE support but no runtime validation.
This module adds Pydantic validation at the pipeline boundary to catch
malformed events before they enter the NLP/storage layers.

Usage:
    from core.validator import validate_batch
    valid, rejected = validate_batch(raw_events)
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, field_validator

logger = logging.getLogger("vision_i.validator")

KNOWN_SOURCES = {
    "gdelt_doc", "gdelt_geo", "gdelt_context", "gdelt_tv",
    "newsapi", "reddit", "youtube", "usgs", "opensky",
    "yahoo_finance", "rss", "hackernews", "telegram",
    "firms", "nws", "who", "ais", "crypto", "bluesky",
    "cisa_kev", "treasury", "composite", "acled", "darkweb",
}

KNOWN_EVENT_TYPES = {
    "disaster", "news", "social", "video", "market",
    "transport", "transport_anomaly", "maritime", "maritime_anomaly",
    "geopolitical", "weather", "technology", "conflict",
    "health", "vulnerability", "fiscal", "composite",
}


class VisionEventValidator(BaseModel):
    """Validates the minimum required fields of a VisionEvent dict."""

    event_id: str
    source: str
    event_type: str
    title: str
    timestamp: str

    @field_validator("event_id")
    @classmethod
    def event_id_not_empty(cls, v: str) -> str:
        if not v or len(v) < 3:
            raise ValueError("event_id too short")
        return v

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("title is empty")
        if len(v) > 2000:
            raise ValueError("title exceeds 2000 chars")
        return v

    @field_validator("source")
    @classmethod
    def source_known(cls, v: str) -> str:
        if not v:
            raise ValueError("source is empty")
        # Warn but don't reject unknown sources to allow extensibility
        if v not in KNOWN_SOURCES:
            logger.debug("Unknown source: %s (allowed but not in registry)", v)
        return v

    @field_validator("event_type")
    @classmethod
    def event_type_known(cls, v: str) -> str:
        if not v:
            raise ValueError("event_type is empty")
        if v not in KNOWN_EVENT_TYPES:
            logger.debug("Unknown event_type: %s (allowed but not in registry)", v)
        return v

    @field_validator("timestamp")
    @classmethod
    def timestamp_parseable(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("timestamp is empty")
        return v


def validate_batch(
    events: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Validate a list of raw event dicts.

    Returns:
        (valid_events, rejected_events)

    Rejected events have an "_rejection_reason" key added.
    """
    valid = []
    rejected = []

    for event in events:
        try:
            VisionEventValidator(
                event_id=event.get("event_id", ""),
                source=event.get("source", ""),
                event_type=event.get("event_type", ""),
                title=event.get("title", ""),
                timestamp=event.get("timestamp", ""),
            )
            valid.append(event)
        except Exception as exc:
            event["_rejection_reason"] = str(exc)
            rejected.append(event)

    if rejected:
        logger.warning(
            "Validation: %d valid, %d rejected",
            len(valid), len(rejected),
        )

    return valid, rejected
