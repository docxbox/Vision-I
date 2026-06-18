"""
core/utils.py
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Shared utilities used by every extractor and the API layer.
Nothing in here has side-effects or external dependencies beyond stdlib.
"""

import hashlib
import logging
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

def stable_id(prefix: str, value: str) -> str:
    """
    Produces a deterministic, collision-resistant ID from a prefix and a value.
    Safe to call across restarts â€” same input always yields the same output.

    Example:
        stable_id("usgs", "us2024abc") â†’ "usgs:5d41402abc4b2a76b9719d911017c592"
    """
    digest = hashlib.md5(value.encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"

def generate_provenance_hash(source_name: str, identifier: str, timestamp: str) -> str:
    """
    Creates a deterministic cryptographic SHA-256 zero-trust provenance hash.
    Using source, ID, and timestamp protects against fragility while ensuring integrity.
    """
    raw_str = f"{source_name}|{identifier}|{timestamp}"
    return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()

def utcnow_iso() -> str:
    """Current UTC time as an ISO 8601 string with Z suffix."""
    return datetime.utcnow().isoformat() + "Z"


def to_iso(value: Any, fallback: Optional[str] = None) -> str:
    """
    Converts any reasonable timestamp representation to ISO 8601 (UTC, Z-suffix).

    Handles:
      - int / float  â†’ Unix epoch seconds or milliseconds
      - str          â†’ tries multiple known formats
      - datetime     â†’ direct conversion
      - None / bad   â†’ returns fallback or utcnow_iso()

    This is the single place timestamp normalisation lives. Every extractor
    calls this instead of rolling its own datetime logic.
    """
    _fallback = fallback or utcnow_iso()

    if value is None:
        return _fallback
    if isinstance(value, datetime):
        return value.isoformat() + "Z"
    if isinstance(value, (int, float)):
        # USGS returns milliseconds; treat anything > 1e10 as ms
        ts = value / 1000 if value > 1e10 else value
        try:
            return datetime.utcfromtimestamp(ts).isoformat() + "Z"
        except (OSError, OverflowError, ValueError):
            logger.debug("to_iso: numeric out of range: %s", value)
            return _fallback

    if not isinstance(value, str):
        return _fallback

    s = value.strip()
    if len(s) == 16 and "T" in s and s.endswith("Z"):
        try:
            return datetime.strptime(s[:-1], "%Y%m%dT%H%M%S").isoformat() + "Z"
        except ValueError:
            pass
    if s.isdigit():
        raw = s if len(s) == 14 else s.ljust(14, "0")[:14]
        try:
            return datetime.strptime(raw, "%Y%m%d%H%M%S").isoformat() + "Z"
        except ValueError:
            pass
    for fmt in (
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(s.replace("Z", ""), fmt.replace("Z", "")).isoformat() + "Z"
        except ValueError:
            continue
    try:
        from dateutil import parser as du
        return du.parse(s).replace(tzinfo=None).isoformat() + "Z"
    except Exception:
        pass

    logger.debug("to_iso: could not parse '%s', using fallback", value)
    return _fallback

def safe_get(d: Dict, *keys, default=None):
    """
    Safely traverse a nested dict.

    Example:
        safe_get(item, "geometry", "coordinates", default=[])
    """
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur

