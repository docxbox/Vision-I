"""
core/event_bus.py
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Redis Pub/Sub event bus for inter-layer pipeline coordination.

Channels:
  pipeline:ingest_complete       â€” published after ingestion finishes
  pipeline:nlp_complete          â€” published after NLP enrichment finishes
  pipeline:ontology_mapped       â€” published after ontology mapping finishes
  pipeline:intelligence_complete â€” published after intelligence cycle finishes

Usage:
    from core.event_bus import EventBus
    bus = EventBus("redis://redis:6379/0")
    await bus.connect()
    await bus.publish("ingest_complete", {"batch_id": "...", "event_count": 42})
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Dict, Optional

import redis.asyncio as aioredis

logger = logging.getLogger("vision_i.event_bus")

CHANNELS = {
    "ingest_complete":       "pipeline:ingest_complete",
    "nlp_complete":          "pipeline:nlp_complete",
    "ontology_mapped":       "pipeline:ontology_mapped",
    "intelligence_complete": "pipeline:intelligence_complete",
    "correlation_complete":  "pipeline:correlation_complete",
    "composite_events":      "pipeline:composite_events",
    "risk_score_updated":    "pipeline:risk_score_updated",
    "situation_updated":     "pipeline:situation_updated",
    "intelligence_update":   "pipeline:intelligence_update",
    # Cache warming requests (fast HTTP endpoints can return 202 and let workers fill Redis)
    "sources_warm":          "cache:sources_warm",
}


class EventBus:
    """Thin wrapper around Redis Pub/Sub for pipeline event coordination."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._redis: Optional[aioredis.Redis] = None

    async def connect(self) -> None:
        """Connect to Redis. Safe to call multiple times."""
        if self._redis is None:
            self._redis = aioredis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
            )
            # Verify connection
            await self._redis.ping()
            logger.info("EventBus connected to Redis")

    @property
    def redis(self) -> aioredis.Redis:
        """Direct access to the Redis client for cache reads/writes."""
        if self._redis is None:
            raise RuntimeError("EventBus not connected â€” call connect() first")
        return self._redis

    async def publish(self, event_name: str, payload: Dict[str, Any]) -> int:
        """
        Publish a pipeline event.

        Args:
            event_name: One of the CHANNELS keys (e.g. "ingest_complete")
            payload:    JSON-serialisable dict

        Returns:
            Number of subscribers that received the message.
        """
        channel = CHANNELS.get(event_name)
        if channel is None:
            raise ValueError(
                f"Unknown event: {event_name!r}. Must be one of {list(CHANNELS)}"
            )
        message = self._with_contract(event_name, payload)
        data = json.dumps(message, default=str)
        count = await self.redis.publish(channel, data)
        logger.info("Published %s â†’ %d subscribers  payload_size=%d",
                     channel, count, len(data))
        return count

    @staticmethod
    def _with_contract(event_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Versioned redis event contract (v1), backward compatible.
        Keeps legacy top-level fields while adding structured metadata under `_meta`.
        """
        meta = {
            "schema_version": "1.0",
            "event_name": event_name,
            "event_id": str(uuid.uuid4()),
            "emitted_at": datetime.now(timezone.utc).isoformat(),
            "publisher": "python",
        }
        if isinstance(payload, dict) and "_meta" in payload and isinstance(payload["_meta"], dict):
            # preserve caller-provided meta extensions but enforce canonical fields
            meta = {**payload["_meta"], **meta}

        enriched = dict(payload or {})
        enriched["_meta"] = meta
        return enriched

    async def subscribe(self, *event_names: str) -> aioredis.client.PubSub:
        """
        Subscribe to one or more pipeline event channels.

        Returns an async PubSub object. Iterate with:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    payload = json.loads(message["data"])
        """
        channels = []
        for name in event_names:
            ch = CHANNELS.get(name)
            if ch is None:
                raise ValueError(f"Unknown event: {name!r}")
            channels.append(ch)

        pubsub = self.redis.pubsub()
        await pubsub.subscribe(*channels)
        logger.info("Subscribed to %s", channels)
        return pubsub

    async def cache_set(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        """Write a JSON value to Redis. If ttl_seconds is None, key lives forever."""
        data = json.dumps(value, default=str)
        if ttl_seconds:
            await self.redis.setex(key, ttl_seconds, data)
        else:
            await self.redis.set(key, data)

    async def cache_get(self, key: str) -> Optional[Any]:
        """Read a JSON value from Redis. Returns None on miss."""
        data = await self.redis.get(key)
        if data is None:
            return None
        return json.loads(data)

    _DLQ_KEY = "dlq:failed_events"
    _DLQ_MAX = 1000

    async def send_to_dlq(self, event: Dict[str, Any], error: str, stage: str) -> None:
        """Push a failed event to the dead-letter queue."""
        entry = json.dumps({
            "event": event,
            "error": str(error),
            "stage": stage,
            "timestamp": __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat(),
        }, default=str)
        await self.redis.lpush(self._DLQ_KEY, entry)
        await self.redis.ltrim(self._DLQ_KEY, 0, self._DLQ_MAX - 1)

    async def get_dlq_events(self, limit: int = 50) -> list:
        """Read recent DLQ entries."""
        raw = await self.redis.lrange(self._DLQ_KEY, 0, limit - 1)
        return [json.loads(r) for r in raw]

    async def dlq_size(self) -> int:
        """Return current DLQ length."""
        return await self.redis.llen(self._DLQ_KEY)

    async def retry_dlq_event(self, index: int = 0) -> Optional[Dict[str, Any]]:
        """Pop an event from the DLQ and re-publish to ingest_complete."""
        raw = await self.redis.lindex(self._DLQ_KEY, index)
        if raw is None:
            return None
        entry = json.loads(raw)
        event = entry.get("event", {})
        await self.redis.lrem(self._DLQ_KEY, 1, raw)
        await self.publish("ingest_complete", {
            "batch_id": "dlq_retry",
            "event_count": 1,
            "job_type": "dlq_retry",
        })
        return entry

    async def close(self) -> None:
        """Close the Redis connection."""
        if self._redis:
            await self._redis.aclose()
            self._redis = None
            logger.info("EventBus disconnected")

