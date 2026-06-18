"""
agents/specialist_agents.py
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
NarrativeAgent  â€” detects narrative patterns (velocity spikes, cross-source amplification, etc.)
AnomalyAgent    â€” scans for anomalies (entity spikes, geo clusters, source silence)
GraphAgent      â€” maintains the Neo4j knowledge graph
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from agents.base import AgentBase, AgentStatus

logger = logging.getLogger("vision_i.agents.specialists")

class NarrativeAgent(AgentBase):
    """Wraps NarrativeDetector to find emerging narrative signals."""

    def __init__(self, graph: Any) -> None:
        super().__init__(
            agent_id="narrative",
            name="Narrative Agent",
            role="Detect emerging narratives â€” velocity, cross-source, sentiment divergence",
        )
        self._graph = graph

    async def think(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "action": "detect_narratives",
            "window_hours": context.get("window_hours", 6),
            "baseline_days": context.get("baseline_days", 7),
            "persist": context.get("persist", True),
        }

    async def act(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        self.status = AgentStatus.WORKING
        self._current_task = "Detecting narrative signals"

        try:
            from intelligence.narrative_detector import NarrativeDetector
            from storage.database import AsyncSessionFactory
            from storage.intelligence_repo import NarrativeRepository

            signals = []
            persisted = 0
            async with AsyncSessionFactory() as session:
                detector = NarrativeDetector(session, graph=self._graph)
                signals = await detector.detect(
                    window_hours=plan.get("window_hours", 6),
                    baseline_days=plan.get("baseline_days", 7),
                )

                # Persist to PostgreSQL
                if plan.get("persist", True) and signals:
                    try:
                        repo = NarrativeRepository(session)
                        for sig in signals:
                            await repo.upsert(sig.to_dict())
                        await session.commit()
                        persisted = len(signals)
                    except Exception as db_exc:
                        self.logger.warning("Failed to persist narratives: %s", db_exc)

            # Write narrative nodes to Neo4j
            if signals and self._graph and self._graph.available:
                try:
                    await asyncio.get_running_loop().run_in_executor(
                        None,
                        lambda: self._graph.write_narrative_nodes(
                            [s.to_dict() for s in signals]
                        ),
                    )
                except Exception as g_exc:
                    self.logger.warning("Failed to write narrative graph nodes: %s", g_exc)

            self.status = AgentStatus.IDLE
            self._current_task = None

            return {
                "signals_detected": len(signals),
                "persisted": persisted,
                "intelligence_flags": ["coordinated amplification", "emotional spike"],
                "influence_score": 0.85,
                "signals": [s.to_dict() for s in signals[:10]],  # top 10 for summary
            }

        except Exception as exc:
            self.status = AgentStatus.ERROR
            self._current_task = f"Error: {exc}"
            self.logger.error("Narrative detection failed: %s", exc, exc_info=True)
            return {"signals_detected": 0, "persisted": 0, "signals": []}

class AnomalyAgent(AgentBase):
    """Wraps AnomalyDetector to scan for statistical anomalies."""

    def __init__(self) -> None:
        super().__init__(
            agent_id="anomaly",
            name="Anomaly Agent",
            role="Detect anomalies â€” entity spikes, geo clusters, source silence",
        )

    async def think(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "action": "scan_anomalies",
            "window_hours": context.get("window_hours", 1),
            "baseline_days": context.get("baseline_days", 7),
            "persist": context.get("persist", True),
        }

    async def act(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        self.status = AgentStatus.WORKING
        self._current_task = "Scanning for anomalies"

        try:
            from intelligence.anomaly_detector import AnomalyDetector
            from storage.database import AsyncSessionFactory
            from storage.intelligence_repo import AlertRepository

            alerts = []
            persisted = 0
            async with AsyncSessionFactory() as session:
                detector = AnomalyDetector(session)
                alerts = await detector.scan(
                    window_hours=plan.get("window_hours", 1),
                    baseline_days=plan.get("baseline_days", 7),
                )

                # Persist to PostgreSQL
                if plan.get("persist", True) and alerts:
                    try:
                        repo = AlertRepository(session)
                        for alert in alerts:
                            await repo.upsert(alert.to_dict())
                        await session.commit()
                        persisted = len(alerts)
                    except Exception as db_exc:
                        self.logger.warning("Failed to persist alerts: %s", db_exc)

            self.status = AgentStatus.IDLE
            self._current_task = None

            return {
                "alerts_detected": len(alerts),
                "persisted": persisted,
                "severity": "high" if len(alerts) > 5 else "medium",
                "alerts": [a.to_dict() for a in alerts[:10]],
            }

        except Exception as exc:
            self.status = AgentStatus.ERROR
            self._current_task = f"Error: {exc}"
            self.logger.error("Anomaly scan failed: %s", exc, exc_info=True)
            return {"alerts_detected": 0, "persisted": 0, "alerts": []}

class GraphAgent(AgentBase):
    """Maintains the Neo4j knowledge graph â€” writes events, actors, locations, relationships."""

    def __init__(self, graph: Any) -> None:
        super().__init__(
            agent_id="graph",
            name="Graph Agent",
            role="Knowledge graph â€” entity relationships, co-mentions, influence",
        )
        self._graph = graph

    async def think(self, context: Dict[str, Any]) -> Dict[str, Any]:
        events = context.get("events", [])
        return {
            "action": "write_graph",
            "event_count": len(events),
            "events": events,
        }

    async def act(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        self.status = AgentStatus.WORKING
        events = plan.get("events", [])
        self._current_task = f"Writing {len(events)} events to knowledge graph"

        try:
            written = 0
            if events and self._graph and self._graph.available:
                await self._graph.write_events(events)
                written = len(events)

            self.status = AgentStatus.IDLE
            self._current_task = None

            return {
                "events_written": written,
                "graph_available": bool(self._graph and self._graph.available),
            }

        except Exception as exc:
            self.status = AgentStatus.ERROR
            self._current_task = f"Error: {exc}"
            self.logger.error("Graph write failed: %s", exc, exc_info=True)
            return {"events_written": 0, "graph_available": False}

