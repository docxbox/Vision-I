"""
extractors/cisa_kev.py
───────────────────────
CISA Known Exploited Vulnerabilities (KEV) extractor — no authentication required.

Feed URL: https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json
"""

import logging
from typing import Any, Dict, List

logger = logging.getLogger("vision_i.extractors.cisa_kev")


async def fetch(limit: int = 50, **kwargs) -> Dict[str, Any]:
    """
    Fetch the CISA KEV catalogue.

    Parameters
    ----------
    limit : Maximum number of vulnerabilities to return (newest first).

    Returns
    -------
    dict with keys: source, total, events, error (None on success)
    """
    from core.http_source import fetch_source

    url = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
    result = await fetch_source(url, source_tag="cisa_kev", timeout=20)

    if result["status"] != "ok":
        logger.warning("cisa_kev fetch failed: %s", result["error"])
        return {
            "source": "cisa_kev",
            "total":  0,
            "events": [],
            "error":  result["error"],
        }

    raw_data = result["data"]
    if not isinstance(raw_data, dict):
        return {
            "source": "cisa_kev",
            "total":  0,
            "events": [],
            "error":  "unexpected response format",
        }

    vulns: List[Dict] = raw_data.get("vulnerabilities", [])
    # Most-recently-added first (the feed is already ordered, but be explicit)
    # Slice to limit
    vulns = vulns[:limit]

    events: List[Dict[str, Any]] = []
    for v in vulns:
        if not isinstance(v, dict):
            continue
        cve_id = v.get("cveID", "")
        events.append({
            "event_id":    cve_id,
            "title":       f"{cve_id} - {v.get('vulnerabilityName', '')}",
            "source":      "cisa_kev",
            "event_type":  "vulnerability",
            "timestamp":   _date_to_iso(v.get("dateAdded", "")),
            "description": v.get("shortDescription", ""),
            "vendor":      v.get("vendorProject", ""),
            "product":     v.get("product", ""),
            "due_date":    v.get("dueDate", ""),
            "risk_score":  0.85,
            "tags":        ["cisa", "kev", "vulnerability"],
        })

    logger.info("cisa_kev: fetched %d vulnerabilities (limit=%d)", len(events), limit)
    return {
        "source": "cisa_kev",
        "total":  len(events),
        "events": events,
        "error":  None,
    }


def _date_to_iso(date_str: str) -> str:
    """Convert YYYY-MM-DD to ISO 8601 with Z suffix."""
    if not date_str:
        from core.utils import utcnow_iso
        return utcnow_iso()
    date_str = date_str.strip()
    if len(date_str) == 10 and date_str[4] == "-":
        return date_str + "T00:00:00Z"
    return date_str
