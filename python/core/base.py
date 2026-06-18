"""
core/base.py
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Abstract base class for every Vision-I extractor.

Concrete extractors only need to implement:
  - fetch()     â†’ returns raw items from the source
  - normalize() â†’ converts one raw item to a VisionEvent dict

The base class provides:
  - run()           convenience method: fetch + normalize + return list
  - source_name     identifier used in event_id and logging
  - logger          pre-configured per-extractor logger
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List

from core.entity_normalizer import sanitize_event_text
from core.schema import VisionEvent
from core.utils import utcnow_iso


class BaseExtractor(ABC):
    """
    All extractors inherit from this class.

    Usage (standalone):
        extractor = MyExtractor()
        events = extractor.run(limit=10)

    Usage (FastAPI route):
        extractor = MyExtractor()
        raw  = await run_in_executor(extractor.fetch, limit=10)
        data = [extractor.normalize(item) for item in raw]
    """

    source_name: str = "base"

    def __init__(self) -> None:
        self.logger = logging.getLogger(f"vision_i.extractors.{self.source_name}")
        from core.circuit_breaker import CircuitBreaker
        self._circuit_breaker = CircuitBreaker(self.source_name)

    @abstractmethod
    def fetch(self, **kwargs) -> List[Any]:
        """
        Pull raw data from the source.
        Must be side-effect free beyond network calls.
        Returns a list of raw items (dicts, lists, objects â€” source-dependent).
        """

    @abstractmethod
    def normalize(self, item: Any) -> VisionEvent:
        """
        Convert one raw item to the canonical VisionEvent schema.
        Must never raise â€” return a minimal valid event on error.
        """

    def run(self, **kwargs) -> List[VisionEvent]:
        """
        Fetch + normalize in one call. Errors per item are caught and logged
        so one bad record never kills the whole batch.

        Respects the circuit breaker: if the source is failing, returns
        empty immediately to prevent cascade failures.
        """
        if not self._circuit_breaker.can_execute():
            self.logger.warning(
                "Circuit OPEN for %s â€” skipping fetch", self.source_name
            )
            return []

        self.logger.info("Starting fetch (kwargs=%s)", kwargs)
        try:
            raw_items = self.fetch(**kwargs)
        except Exception as exc:
            self._circuit_breaker.record_failure()
            self.logger.error("fetch() failed for %s: %s", self.source_name, exc)
            return []

        self._circuit_breaker.record_success()
        self.logger.info("Fetched %d raw items", len(raw_items))

        events: List[VisionEvent] = []
        for item in raw_items:
            try:
                norm_event = self.normalize(item)
                # Auto-inject Defense-Grade Provenance Hash
                if "provenance_id" not in norm_event:
                    import core.utils as util
                    phash = util.generate_provenance_hash(
                        norm_event.get("source", self.source_name),
                        norm_event.get("event_id", ""),
                        norm_event.get("timestamp", util.utcnow_iso())
                    )
                    norm_event["provenance_id"] = phash
                sanitize_event_text(norm_event)
                events.append(norm_event)
            except Exception as exc:
                self.logger.warning("normalize() failed for item: %s â€” %s", str(item)[:120], exc)

        self.logger.info("Normalized %d events", len(events))
        return events

    def health(self) -> Dict[str, Any]:
        """
        Lightweight connectivity check used by GET /health.
        Default: try fetch(limit=1) and report ok/error.
        Override for a cheaper check if fetch is expensive.
        """
        cb_state = self._circuit_breaker.to_dict()
        if not self._circuit_breaker.can_execute():
            return {
                "source": self.source_name,
                "status": "circuit_open",
                "circuit_breaker": cb_state,
            }
        try:
            result = self.fetch(limit=1)
            return {
                "source": self.source_name,
                "status": "ok",
                "sample_count": len(result),
                "circuit_breaker": cb_state,
            }
        except Exception as exc:
            return {
                "source": self.source_name,
                "status": "error",
                "detail": str(exc),
                "circuit_breaker": cb_state,
            }

