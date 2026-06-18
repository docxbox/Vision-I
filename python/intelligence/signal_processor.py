"""
intelligence/signal_processor.py
──────────────────────────────────
Converts NLP-enriched VisionEvents into Signals with semantic embeddings.

Each VisionEvent produces one Signal containing:
  - A 384-dim embedding vector for semantic similarity search
  - A content hash for deduplication
  - An initial confidence score based on source reliability
  - Normalised actor/location/sentiment metadata

This is the bridge between the Data Layer (raw events) and the
Intelligence Layer (correlated signals → composite events).
"""

import hashlib
import logging
from typing import Any, Dict, List, TYPE_CHECKING

from core.utils import utcnow_iso

if TYPE_CHECKING:
    from intelligence.embedder import EmbeddingService

logger = logging.getLogger("vision_i.intelligence.signal_processor")

# Source reliability weights — used for initial confidence scoring.
# Higher weight = more trusted source.  Updated from empirical observation.
SOURCE_WEIGHTS: Dict[str, float] = {
    "usgs":          0.95,
    "nws":           0.95,
    "firms":         0.90,
    "opensky":       0.90,
    "who":           0.90,
    "rss":           0.85,
    "yahoo_finance": 0.80,
    "newsapi":       0.75,
    "gdelt":         0.70,
    "acled":         0.80,
    "hackernews":    0.50,
    "reddit":        0.40,
    "telegram":      0.35,
    "twitter":       0.35,
    "darkweb":       0.30,
}


def _content_hash(title: str, body: str) -> str:
    """SHA-256 of title+body for content deduplication."""
    raw = f"{(title or '').strip()}|{(body or '').strip()[:500]}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _extract_actor_names(actors: Any) -> List[str]:
    """Pull actor names from the event's actors field (list of dicts)."""
    if not actors or not isinstance(actors, list):
        return []
    names = []
    for a in actors:
        if isinstance(a, dict):
            name = a.get("name") or a.get("canonical_name", "")
            if name and name.strip():
                names.append(name.strip())
        elif isinstance(a, str) and a.strip():
            names.append(a.strip())
    return names


class SignalProcessor:
    """Converts VisionEvents into Signals with embeddings and confidence."""

    def __init__(self, embedder: "EmbeddingService") -> None:
        self._embedder = embedder

    def create_signals_sync(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Synchronous signal creation — call from ``run_in_executor``.

        Parameters
        ----------
        events : list of VisionEvent dicts (already NLP-enriched)

        Returns
        -------
        list of signal dicts ready for ``SignalRepository.upsert_signals``
        """
        if not events:
            return []

        # Build embedding texts
        texts = []
        for ev in events:
            title = ev.get("title") or ""
            body  = ev.get("body") or ev.get("description") or ""
            texts.append(f"{title}. {body[:500]}")

        # Batch embed
        embeddings = self._embedder.embed_texts(texts)

        signals: List[Dict[str, Any]] = []
        for ev, emb in zip(events, embeddings):
            event_id = ev.get("event_id", "")
            source   = ev.get("source", "unknown")
            title    = ev.get("title") or ""
            body     = ev.get("body") or ev.get("description") or ""

            sig = {
                "signal_id":       f"sig:{event_id}",
                "source_event_id": event_id,
                "source":          source,
                "signal_type":     "raw",
                "title":           title,
                "body":            body[:2000],
                "content_hash":    _content_hash(title, body),
                "embedding":       emb,
                "timestamp":       ev.get("timestamp") or utcnow_iso(),
                "actors":          _extract_actor_names(ev.get("actors")),
                "location_name":   (ev.get("location") or {}).get("name") if isinstance(ev.get("location"), dict) else ev.get("location_name"),
                "location_lat":    (ev.get("location") or {}).get("lat") if isinstance(ev.get("location"), dict) else ev.get("location_lat"),
                "location_lon":    (ev.get("location") or {}).get("lon") if isinstance(ev.get("location"), dict) else ev.get("location_lon"),
                "sentiment_score": ev.get("sentiment", {}).get("score") if isinstance(ev.get("sentiment"), dict) else ev.get("sentiment_score"),
                "confidence":      SOURCE_WEIGHTS.get(source, 0.5),
                "cluster_id":      None,
                "meta": {
                    "event_type": ev.get("event_type"),
                    "tags":       ev.get("tags", []),
                    "url":        ev.get("url"),
                },
            }
            signals.append(sig)

        logger.info("Created %d signals from %d events", len(signals), len(events))
        return signals
