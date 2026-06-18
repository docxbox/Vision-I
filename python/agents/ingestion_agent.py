"""
agents/ingestion_agent.py
─────────────────────────
IngestionAgent — collects data from configured sources via the Orchestrator.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from agents.base import AgentBase, AgentStatus
from core.orchestrator import Orchestrator, IngestionResult

logger = logging.getLogger("vision_i.agents.ingestion")


class IngestionAgent(AgentBase):
    """
    Wraps the existing Orchestrator to fetch events from all configured sources.
    Decides which sources to query based on mission context.
    """

    def __init__(self, orchestrator: Orchestrator) -> None:
        super().__init__(
            agent_id="ingestion",
            name="Ingestion Agent",
            role="Data collection from 10+ global sources",
        )
        self._orchestrator = orchestrator

    async def think(self, context: Dict[str, Any]) -> Dict[str, Any]:
        query = context.get("query", "world news")
        sources = context.get("sources")
        limit = context.get("limit", 10)

        plan: Dict[str, Any] = {
            "action": "ingest",
            "query": query,
            "limit": limit,
            "sources": sources,
        }
        self.logger.info("Plan: ingest query=%r sources=%s limit=%d", query, sources, limit)
        return plan

    async def act(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        self.status = AgentStatus.WORKING
        self._current_task = f"Ingesting: {plan['query']}"

        try:
            loop = asyncio.get_running_loop()
            result: IngestionResult = await loop.run_in_executor(
                None,
                lambda: self._orchestrator.run(
                    query=plan["query"],
                    limit=plan.get("limit", 10),
                    sources=plan.get("sources"),
                ),
            )

            self.status = AgentStatus.IDLE
            self._current_task = None

            return {
                "events": result.events,
                "total": result.total,
                "source_counts": result.source_counts,
                "source_errors": result.source_errors,
            }

        except Exception as exc:
            self.status = AgentStatus.ERROR
            self._current_task = f"Error: {exc}"
            self.logger.error("Ingestion failed: %s", exc, exc_info=True)
            return {"events": [], "total": 0, "source_counts": {}, "source_errors": {"agent": str(exc)}}
