"""
Shared market entity detection for finance telemetry and business/news feeds.

The goal is to keep ticker/company identity consistent across sources such as
Yahoo Finance and Bloomberg RSS so graph, entity, and workspace views can join
them through the same actor names.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List


MARKET_ENTITIES: Dict[str, Dict[str, Any]] = {
    "AAPL": {
        "name": "Apple",
        "aliases": ["Apple", "Apple Inc", "AAPL", "$AAPL", "iPhone maker"],
        "sector": "Technology",
    },
    "AMZN": {
        "name": "Amazon",
        "aliases": ["Amazon", "Amazon.com", "AMZN", "$AMZN", "AWS"],
        "sector": "Technology",
    },
    "GOOGL": {
        "name": "Alphabet",
        "aliases": ["Alphabet", "Google", "GOOGL", "GOOG", "$GOOGL", "$GOOG"],
        "sector": "Technology",
    },
    "MSFT": {
        "name": "Microsoft",
        "aliases": ["Microsoft", "Microsoft Corp", "MSFT", "$MSFT"],
        "sector": "Technology",
    },
    "NVDA": {
        "name": "Nvidia",
        "aliases": ["Nvidia", "NVIDIA", "NVDA", "$NVDA"],
        "sector": "Semiconductors",
    },
    "TSLA": {
        "name": "Tesla",
        "aliases": ["Tesla", "Tesla Inc", "TSLA", "$TSLA", "Elon Musk"],
        "sector": "Automotive",
    },
    "AMD": {"name": "AMD", "aliases": ["AMD", "$AMD", "Advanced Micro Devices"], "sector": "Semiconductors"},
    "ASML": {"name": "ASML", "aliases": ["ASML", "$ASML", "ASML Holding"], "sector": "Semiconductors"},
    "AZO": {"name": "AutoZone", "aliases": ["AutoZone", "AZO", "$AZO"], "sector": "Consumer"},
    "BA": {"name": "Boeing", "aliases": ["Boeing", "BA", "$BA"], "sector": "Aerospace"},
    "BABA": {"name": "Alibaba", "aliases": ["Alibaba", "BABA", "$BABA"], "sector": "Technology"},
    "CVX": {"name": "Chevron", "aliases": ["Chevron", "CVX", "$CVX"], "sector": "Energy"},
    "GS": {"name": "Goldman Sachs", "aliases": ["Goldman Sachs", "GS", "$GS"], "sector": "Finance"},
    "HD": {"name": "Home Depot", "aliases": ["Home Depot", "HD", "$HD"], "sector": "Consumer"},
    "INTC": {"name": "Intel", "aliases": ["Intel", "Intel Corp", "INTC", "$INTC"], "sector": "Semiconductors"},
    "JPM": {"name": "JPMorgan Chase", "aliases": ["JPMorgan", "JPMorgan Chase", "JPM", "$JPM"], "sector": "Finance"},
    "LMT": {"name": "Lockheed Martin", "aliases": ["Lockheed Martin", "LMT", "$LMT"], "sector": "Defense"},
    "META": {"name": "Meta", "aliases": ["Meta", "Meta Platforms", "Facebook", "META", "$META"], "sector": "Technology"},
    "NFLX": {"name": "Netflix", "aliases": ["Netflix", "NFLX", "$NFLX"], "sector": "Technology"},
    "RTX": {"name": "RTX", "aliases": ["RTX", "Raytheon", "RTX Corp", "$RTX"], "sector": "Defense"},
    "TSM": {"name": "TSMC", "aliases": ["TSMC", "Taiwan Semiconductor", "TSM", "$TSM"], "sector": "Semiconductors"},
    "WMT": {"name": "Walmart", "aliases": ["Walmart", "Wal-Mart", "WMT", "$WMT"], "sector": "Consumer"},
    "XOM": {"name": "Exxon Mobil", "aliases": ["Exxon", "Exxon Mobil", "XOM", "$XOM"], "sector": "Energy"},
}


def default_ticker_names() -> Dict[str, str]:
    return {symbol: meta["name"] for symbol, meta in MARKET_ENTITIES.items()}


def _contains_alias(text: str, alias: str) -> bool:
    if not alias:
        return False
    if alias.startswith("$"):
        return alias.lower() in text.lower()
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", text, re.IGNORECASE) is not None


def detect_market_entities(*texts: str) -> List[Dict[str, Any]]:
    haystack = " ".join(t for t in texts if t)
    matches: List[Dict[str, Any]] = []

    for symbol, meta in MARKET_ENTITIES.items():
        aliases = [str(a) for a in meta.get("aliases") or []]
        matched = next((alias for alias in aliases if _contains_alias(haystack, alias)), None)
        if not matched:
            continue
        matches.append({
            "symbol": symbol,
            "name": meta["name"],
            "aliases": aliases,
            "sector": meta.get("sector"),
            "matched_alias": matched,
        })

    return matches


def market_entities_from_symbols(symbols: List[str]) -> List[Dict[str, Any]]:
    matches: List[Dict[str, Any]] = []
    seen = set()
    for raw in symbols:
        symbol = (raw or "").strip().upper().lstrip("$")
        if not symbol or symbol in seen:
            continue
        meta = MARKET_ENTITIES.get(symbol)
        if not meta:
            continue
        matches.append({
            "symbol": symbol,
            "name": meta["name"],
            "aliases": [str(a) for a in meta.get("aliases") or []],
            "sector": meta.get("sector"),
            "matched_alias": symbol,
        })
        seen.add(symbol)
    return matches


def merge_market_matches(*groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen = set()
    for group in groups:
        for match in group:
            symbol = str(match.get("symbol") or "").upper()
            name = str(match.get("name") or "").lower()
            key = symbol or name
            if not key or key in seen:
                continue
            merged.append(match)
            seen.add(key)
    return merged


def market_actor_payloads(matches: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    actors: List[Dict[str, str]] = []
    for match in matches:
        name = str(match.get("name") or "").strip()
        symbol = str(match.get("symbol") or "").strip().upper()
        if name:
            actors.append({"name": name, "type": "ORG", "canonical": name})
        if symbol:
            actors.append({"name": symbol, "type": "ORG", "canonical": name or symbol})
    return actors
