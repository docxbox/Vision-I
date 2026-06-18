"""
core/circuit_breaker.py
────────────────────────
Per-extractor circuit breaker to prevent cascade failures.

States:
  CLOSED    — Normal operation. Failures increment counter.
  OPEN      — Source is failing. All calls return empty immediately.
              After cooldown_seconds, transitions to HALF_OPEN.
  HALF_OPEN — Allow one test request. Success → CLOSED. Failure → OPEN.

Default thresholds:
  - 3 consecutive failures → OPEN
  - 300s cooldown → HALF_OPEN
  - 1 success in HALF_OPEN → CLOSED
"""

import logging
import time
from enum import Enum
from typing import Dict, Optional

from config.settings import settings

logger = logging.getLogger("vision_i.circuit_breaker")


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Per-source circuit breaker."""

    def __init__(
        self,
        source_name: str,
        threshold: Optional[int] = None,
        cooldown_seconds: Optional[int] = None,
    ) -> None:
        self.source_name = source_name
        self._threshold = threshold or settings.circuit_breaker_threshold
        self._cooldown = cooldown_seconds or settings.circuit_breaker_cooldown
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0
        self._total_trips = 0

    @property
    def state(self) -> CircuitState:
        """Current state, accounting for cooldown transitions."""
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self._cooldown:
                self._state = CircuitState.HALF_OPEN
                logger.info(
                    "Circuit %s: OPEN → HALF_OPEN after %ds cooldown",
                    self.source_name, int(elapsed),
                )
        return self._state

    def can_execute(self) -> bool:
        """Return True if the circuit allows execution."""
        s = self.state  # triggers cooldown transition
        if s == CircuitState.CLOSED:
            return True
        if s == CircuitState.HALF_OPEN:
            return True  # allow one test request
        return False  # OPEN

    def record_success(self) -> None:
        """Record a successful call — resets failure count, closes circuit."""
        if self._state != CircuitState.CLOSED:
            logger.info("Circuit %s: %s → CLOSED (success)", self.source_name, self._state.value)
        self._state = CircuitState.CLOSED
        self._failure_count = 0

    def record_failure(self) -> None:
        """Record a failed call — may trip the circuit to OPEN."""
        self._failure_count += 1
        self._last_failure_time = time.monotonic()

        if self._state == CircuitState.HALF_OPEN:
            # Test request failed — back to OPEN
            self._state = CircuitState.OPEN
            self._total_trips += 1
            logger.warning(
                "Circuit %s: HALF_OPEN → OPEN (test failed, trip #%d)",
                self.source_name, self._total_trips,
            )
        elif self._failure_count >= self._threshold:
            self._state = CircuitState.OPEN
            self._total_trips += 1
            logger.warning(
                "Circuit %s: CLOSED → OPEN after %d failures (trip #%d)",
                self.source_name, self._failure_count, self._total_trips,
            )

    def to_dict(self) -> Dict:
        """Serialize state for health check / admin API."""
        return {
            "source": self.source_name,
            "state": self.state.value,
            "failure_count": self._failure_count,
            "total_trips": self._total_trips,
            "threshold": self._threshold,
            "cooldown_seconds": self._cooldown,
        }
