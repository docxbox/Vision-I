"""
agents/swarm.py
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
MessageBus    â€” async pub/sub between agents
MissionLog    â€” append-only log of agent actions
SwarmManager  â€” lifecycle, mission orchestration, API surface
"""

from __future__ import annotations

import uuid
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from agents.base import AgentBase, AgentMessage, AgentStatus

if TYPE_CHECKING:
    from core.orchestrator import Orchestrator
    from core.enricher import Enricher
    from nlp.pipeline import NLPPipeline
    from storage.graph import GraphDB
    from agents.llm_provider import LLMProvider

logger = logging.getLogger("vision_i.swarm")

class MessageBus:
    """Simple async message router between registered agents."""

    def __init__(self) -> None:
        self._agents: Dict[str, AgentBase] = {}

    def register(self, agent: AgentBase) -> None:
        self._agents[agent.agent_id] = agent

    def unregister(self, agent_id: str) -> None:
        self._agents.pop(agent_id, None)

    async def dispatch(self, msg: AgentMessage) -> None:
        if msg.recipient == "*":
            for aid, agent in self._agents.items():
                if aid != msg.sender:
                    await agent.receive(msg)
        else:
            target = self._agents.get(msg.recipient)
            if target:
                await target.receive(msg)
            else:
                logger.warning("Bus: no agent '%s' â€” message dropped", msg.recipient)

class MissionLog:
    """Append-only log of agent actions scoped to missions."""

    def __init__(self, max_entries: int = 5000) -> None:
        self._entries: List[Dict[str, Any]] = []
        self._max = max_entries
        self._lock = asyncio.Lock()

    async def add(
        self,
        agent_id: str,
        action: str,
        detail: str = "",
        mission_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent_id": agent_id,
            "action": action,
            "detail": detail,
            "mission_id": mission_id,
        }
        async with self._lock:
            self._entries.append(entry)
            if len(self._entries) > self._max:
                self._entries = self._entries[-self._max:]
        return entry

    async def get(
        self,
        mission_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        async with self._lock:
            if mission_id:
                filtered = [e for e in self._entries if e.get("mission_id") == mission_id]
            else:
                filtered = list(self._entries)
        return list(reversed(filtered[-limit:]))

class SwarmManager:
    """
    Singleton that owns the agent registry, message bus, mission state,
    and shared references to the existing Vision-I singletons.
    """

    def __init__(
        self,
        orchestrator: Orchestrator,
        enricher: Enricher,
        nlp: NLPPipeline,
        graph: GraphDB,
        llm: Optional[LLMProvider] = None,
        max_missions: int = 500,
    ) -> None:
        self.orchestrator = orchestrator
        self.enricher = enricher
        self.nlp = nlp
        self.graph = graph
        self.llm = llm
        self._max_missions = max_missions

        self._bus = MessageBus()
        self._agents: Dict[str, AgentBase] = {}
        self._missions: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._log = MissionLog()

    def register(self, agent: AgentBase) -> None:
        agent._bus = self._bus
        self._bus.register(agent)
        self._agents[agent.agent_id] = agent
        logger.info("Registered agent: %s (%s)", agent.name, agent.agent_id)

    def unregister(self, agent_id: str) -> None:
        self._bus.unregister(agent_id)
        self._agents.pop(agent_id, None)

    def list_agents(self) -> List[Dict[str, Any]]:
        return [a.to_dict() for a in self._agents.values()]

    def get_agent(self, agent_id: str) -> Optional[Dict[str, Any]]:
        agent = self._agents.get(agent_id)
        return agent.to_dict() if agent else None

    async def start_mission(
        self,
        query: str,
        sources: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        mission_id = f"m-{uuid.uuid4().hex[:10]}"

        mission: Dict[str, Any] = {
            "mission_id": mission_id,
            "query": query,
            "sources": sources,
            "status": "running",
            "stage": "starting",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "results": None,
            "error": None,
        }

        async with self._lock:
            # Evict oldest completed missions if at capacity
            if len(self._missions) >= self._max_missions:
                completed = sorted(
                    (
                        (mid, m)
                        for mid, m in self._missions.items()
                        if m.get("status") in ("completed", "failed")
                    ),
                    key=lambda x: x[1].get("started_at", ""),
                )
                for mid, _ in completed:
                    del self._missions[mid]
                    if len(self._missions) < self._max_missions:
                        break

            self._missions[mission_id] = mission

        await self._log.add("swarm", "mission_created", f"Query: {query}", mission_id)

        # Dispatch to coordinator as a background task
        coordinator = self._agents.get("coordinator")
        if coordinator is None:
            async with self._lock:
                mission["status"] = "failed"
                mission["error"] = "No coordinator agent registered"
            return mission

        asyncio.create_task(self._run_mission(mission_id, coordinator, query, sources))
        return {"mission_id": mission_id, "status": "running", "query": query}

    async def _run_mission(
        self,
        mission_id: str,
        coordinator: AgentBase,
        query: str,
        sources: Optional[List[str]],
    ) -> None:
        try:
            from agents.coordinator_agent import CoordinatorAgent
            assert isinstance(coordinator, CoordinatorAgent)
            results = await coordinator.run_mission(mission_id, query, sources)
            async with self._lock:
                mission = self._missions[mission_id]
                mission["status"] = "completed"
                mission["results"] = results
                mission["finished_at"] = datetime.now(timezone.utc).isoformat()
            await self._log.add("swarm", "mission_completed", f"Results: {results.get('summary', '')}", mission_id)
        except Exception as exc:
            logger.error("Mission %s failed: %s", mission_id, exc, exc_info=True)
            async with self._lock:
                mission = self._missions[mission_id]
                mission["status"] = "failed"
                mission["error"] = str(exc)
                mission["finished_at"] = datetime.now(timezone.utc).isoformat()
            await self._log.add("swarm", "mission_failed", str(exc), mission_id)

    async def get_mission(self, mission_id: str) -> Optional[Dict[str, Any]]:
        async with self._lock:
            return self._missions.get(mission_id)

    async def list_missions(self, limit: int = 20) -> List[Dict[str, Any]]:
        async with self._lock:
            missions = sorted(
                self._missions.values(),
                key=lambda m: m.get("started_at", ""),
                reverse=True,
            )
            return missions[:limit]

    @property
    def log(self) -> MissionLog:
        return self._log

    async def get_log(
        self,
        mission_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        return await self._log.get(mission_id=mission_id, limit=limit)

