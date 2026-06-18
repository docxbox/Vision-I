"""
core/entity_normalizer.py
-------------------------
Shared actor/entity normalization for Vision-I.

This module provides a light canonicalization layer that can run:
  - after extractor output
  - after NLP/entity resolution
  - before DB persistence
  - before graph writes

It intentionally does not try to solve full entity resolution. It focuses on
stable display names, duplicate suppression, basic mojibake repair, and
consistent canonical keys across the pipeline.
"""

from __future__ import annotations

import html
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional

_KNOWN_BAD_ACTOR_TOKENS = {"kraine", "armen", "lice", "news", "live"}
_LOWERCASE_WORDS = {"of", "the", "and", "for", "in", "on", "at", "to", "de", "du", "la", "le"}
_UPPERCASE_WORDS = {"un", "uk", "us", "uae", "eu", "nato", "ocha", "fbi", "cia", "ssa"}
_GENERIC_ORGS = {
    "security council",
    "un news",
    "the guardian",
    "theguardian",
    "bloomberg",
    "bloomberg television",
    "france24",
    "france 24",
    "al jazeera",
    "rt",
    "rt com",
    "dw",
    "dw news",
    "cnbc",
    "cnbc world",
    "war on the rocks",
    "south china morning post",
    "444 hu",
}


def canonical_actor_key(name: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def repair_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = html.unescape(str(value))
    attempts = 0
    suspicious_markers = ("Ã", "Â", "â", "Ð", "Ñ", "Ă", "đ")
    while attempts < 2 and any(marker in text for marker in suspicious_markers):
        repaired = None
        for source_encoding in ("latin1", "cp1252"):
            try:
                repaired = text.encode(source_encoding).decode("utf-8")
                break
            except Exception:
                continue
        if repaired is None:
            break
        if repaired == text:
            break
        text = repaired
        attempts += 1
    text = unicodedata.normalize("NFKC", text)
    text = (
        text.replace("\u00a0", " ")
        .replace("\u200b", "")
        .replace("\u2060", " ")
        .replace("\u2014", "-")
        .replace("\u2013", "-")
    )
    return text


def normalize_actor_name(name: Optional[str], actor_type: Optional[str] = None) -> Optional[str]:
    if not name:
        return None

    cleaned = repair_text(name) or ""
    cleaned = cleaned.replace("\u2019", "'").replace("\u2018", "'")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_,.;:/\\[](){}\"'")
    if not cleaned:
        return None

    parts: List[str] = []
    upper_type = str(actor_type or "").upper()
    for part in cleaned.split(" "):
        if not part or not part.isalpha():
            parts.append(part)
            continue
        lower_part = part.lower()
        if lower_part in _LOWERCASE_WORDS and parts:
            parts.append(lower_part)
        elif lower_part in _UPPERCASE_WORDS:
            parts.append(lower_part.upper())
        elif part.islower() and len(part) <= 4:
            parts.append(
                part.upper()
                if upper_type == "ORG" and lower_part not in _LOWERCASE_WORDS
                else (part.upper() if len(part) <= 3 else part.capitalize())
            )
        elif part.islower():
            parts.append(part.capitalize())
        else:
            parts.append(part)
    cleaned = " ".join(parts).strip()

    if cleaned.islower():
        if len(cleaned) <= 5 and upper_type == "ORG":
            cleaned = cleaned.upper()
        else:
            cleaned = cleaned.capitalize()

    lowered = cleaned.lower()
    if lowered in _KNOWN_BAD_ACTOR_TOKENS:
        return None
    return cleaned or None


def is_significant_actor(name: str, actor_type: Optional[str] = None) -> bool:
    key = canonical_actor_key(name)
    if not key or key in _KNOWN_BAD_ACTOR_TOKENS or key in _GENERIC_ORGS:
        return False

    parts = [p for p in key.split(" ") if p]
    if not parts:
        return False

    if len(parts) == 1:
        token = parts[0]
        if len(token) <= 3:
            return False
        if token.isalpha() and token == token.lower() and len(token) <= 5 and str(actor_type or "").upper() != "ORG":
            return False

    return True


def normalize_actor_payloads(
    actors: Optional[Iterable[Dict[str, Any]]],
    *,
    filter_noise: bool = False,
    drop_types: Optional[set[str]] = None,
) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    seen: set[str] = set()
    drop_types = {t.upper() for t in (drop_types or set())}

    for actor in actors or []:
        actor_type = str(actor.get("type") or "").upper()
        if actor_type in drop_types:
            continue

        raw_name = actor.get("canonical") or actor.get("name")
        display_name = normalize_actor_name(raw_name, actor_type)
        if not display_name:
            continue

        if filter_noise and not is_significant_actor(display_name, actor_type):
            continue

        key = canonical_actor_key(display_name)
        if not key or key in seen:
            continue
        seen.add(key)

        payload = dict(actor)
        payload["name"] = display_name
        payload["canonical"] = display_name
        payload["type"] = actor_type or actor.get("type")
        normalized.append(payload)

    return normalized


def normalize_event_entities(event: Dict[str, Any], *, filter_noise: bool = False) -> Dict[str, Any]:
    event["actors"] = normalize_actor_payloads(
        event.get("actors") or [],
        filter_noise=filter_noise,
    )
    return event


def normalize_events(events: List[Dict[str, Any]], *, filter_noise: bool = False) -> List[Dict[str, Any]]:
    for event in events:
        normalize_event_entities(event, filter_noise=filter_noise)
    return events


def sanitize_event_text(event: Dict[str, Any]) -> Dict[str, Any]:
    for key in ("title", "description", "body", "author", "source_id"):
        value = event.get(key)
        if isinstance(value, str):
            event[key] = repair_text(value)

    location = event.get("location")
    if isinstance(location, dict):
        for key in ("name", "country"):
            value = location.get(key)
            if isinstance(value, str):
                location[key] = repair_text(value)

    tags = event.get("tags")
    if isinstance(tags, list):
        event["tags"] = [repair_text(tag) if isinstance(tag, str) else tag for tag in tags]

    extras = event.get("extras")
    if isinstance(extras, dict):
        for key, value in list(extras.items()):
            if isinstance(value, str):
                extras[key] = repair_text(value)

    normalize_event_entities(event)
    return event
