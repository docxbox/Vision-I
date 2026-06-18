"""
extractors/treasury.py
───────────────────────
US Treasury Fiscal Data extractor — no authentication required.

Uses the public fiscaldata.treasury.gov API.
Default endpoint: Monthly Statement of the Public Debt (MSPD), Table 1.
"""

import logging
from typing import Any, Dict, List

logger = logging.getLogger("vision_i.extractors.treasury")


async def fetch(
    endpoint: str = "v1/debt/mspd/mspd_table_1",
    limit: int = 10,
    **kwargs,
) -> Dict[str, Any]:
    """
    Fetch records from the US Treasury Fiscal Data API.

    Parameters
    ----------
    endpoint : API path segment, e.g. "v1/debt/mspd/mspd_table_1".
    limit    : Number of records to retrieve (newest first).

    Returns
    -------
    dict with keys: source, total, events, error (None on success)
    """
    from core.http_source import fetch_source

    capped_limit = min(max(1, limit), 100)
    url = (
        f"https://api.fiscaldata.treasury.gov/services/api/fiscal_service/"
        f"{endpoint}?page[size]={capped_limit}&sort=-record_date"
    )

    result = await fetch_source(url, source_tag="treasury", timeout=20)

    if result["status"] != "ok":
        logger.warning("treasury fetch failed: %s", result["error"])
        return {
            "source": "treasury",
            "total":  0,
            "events": [],
            "error":  result["error"],
        }

    raw_data = result["data"]
    if not isinstance(raw_data, dict):
        return {
            "source": "treasury",
            "total":  0,
            "events": [],
            "error":  "unexpected response format",
        }

    rows: List[Dict] = raw_data.get("data", [])
    endpoint_label = endpoint.split("/")[-1].replace("_", " ").title()

    events: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        record_date = row.get("record_date", "unknown")
        # Build a stable, deterministic event_id
        safe_endpoint = endpoint.replace("/", "_")
        events.append({
            "event_id":   f"treasury_{record_date}_{safe_endpoint}",
            "title":      f"US Treasury {endpoint_label} — {record_date}",
            "source":     "treasury",
            "event_type": "fiscal",
            "timestamp":  _date_to_iso(record_date),
            "data":       row,
            "tags":       ["treasury", "fiscal", "us-government"],
        })

    logger.info("treasury: fetched %d records from endpoint=%s", len(events), endpoint)
    return {
        "source": "treasury",
        "total":  len(events),
        "events": events,
        "error":  None,
    }


def _date_to_iso(date_str: str) -> str:
    """Convert YYYY-MM-DD to ISO 8601 with Z suffix."""
    if not date_str or date_str == "unknown":
        from core.utils import utcnow_iso
        return utcnow_iso()
    date_str = date_str.strip()
    if len(date_str) == 10 and date_str[4] == "-":
        return date_str + "T00:00:00Z"
    return date_str
