"""
agents/coordinator_agent.py
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
CoordinatorAgent â€” the brain of the swarm.

Orchestrates mission execution through a sequential pipeline:
  1. INGESTING   â†’  IngestionAgent collects data
  2. ANALYZING   â†’  AnalysisAgent enriches with NLP
  3. PERSISTING  â†’  GraphAgent writes to Neo4j + DB persist
  4. DETECTING   â†’  NarrativeAgent + AnomalyAgent run in parallel
  5. COMPLETE    â†’  LLM generates intelligence brief
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from agents.base import AgentBase, AgentStatus

if TYPE_CHECKING:
    from agents.swarm import SwarmManager

logger = logging.getLogger("vision_i.agents.coordinator")


class CoordinatorAgent(AgentBase):
    """
    Orchestrates a full intelligence mission across the swarm.

    Pipeline stages: INGESTING â†’ ANALYZING ï¿½ï¿½ï¿½ PERSISTING â†’ DETECTING â†’ COMPLETE
    Uses LLM (Claude/OpenAI/OpenRouter) for mission planning and intelligence briefs.
    """

    STAGES = ["ingesting", "analyzing", "persisting", "detecting", "complete"]

    def __init__(self, swarm: SwarmManager) -> None:
        super().__init__(
            agent_id="coordinator",
            name="Coordinator Agent",
            role="Mission orchestration â€” plans, delegates, aggregates, briefs via LLM",
        )
        self._swarm = swarm

    async def think(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Use LLM to generate a mission plan if available."""
        query = context.get("query", "")
        sources = context.get("sources")
        mission_id = context.get("mission_id", "")

        plan = {
            "action": "coordinate_mission",
            "query": query,
            "sources": sources,
            "mission_id": mission_id,
        }

        # LLM-enhanced planning
        llm = self._swarm.llm
        if llm and llm.available:
            try:
                plan_response = await llm.complete_json(
                    prompt=(
                        f"Plan an intelligence gathering mission for the query: \"{query}\"\n\n"
                        f"Available data sources: newsapi, reddit, youtube, usgs, stocks, opensky, rss, hackernews, aisstream\n"
                        f"User-selected sources: {sources or 'auto (all relevant)'}\n\n"
                        "Respond with JSON:\n"
                        "{\n"
                        '  "recommended_sources": ["list of most relevant sources"],\n'
                        '  "focus_entities": ["key entities/actors to track"],\n'
                        '  "risk_assessment": "brief risk context",\n'
                        '  "analysis_priority": "what to focus analysis on"\n'
                        "}"
                    ),
                    max_tokens=512,
                )
                plan["llm_plan"] = plan_response
                await self._swarm.log.add(
                    self.agent_id, "llm_plan",
                    f"LLM mission plan generated: {plan_response.get('risk_assessment', '')[:100]}",
                    mission_id,
                )
            except Exception as exc:
                logger.warning("LLM planning failed, continuing without: %s", exc)

        return plan

    async def act(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        return await self.run_mission(
            mission_id=plan["mission_id"],
            query=plan["query"],
            sources=plan.get("sources"),
            llm_plan=plan.get("llm_plan"),
        )

    async def generate_ceo_summary(self, alerts: List[Dict]) -> str:
        """Synthesize high priority anomalous alerts into a native plain-english 'CEO' / 'JARVIS' insight summary."""
        llm = self._swarm.llm
        if not llm or not llm.available or not alerts:
            return ""

        try:
            alert_texts = [f"[{a.get('severity', 'high')}] {a.get('title', '')}: {a.get('summary', '')}" for a in alerts[:5]]
            prompt = (
                "You are JARVIS, an executive intelligence coordinator. Given these recent high-priority alerts across markets, mobility, and narratives, "
                "generate a concise, 2-to-3 sentence plain-English situation summary for the CEO.\n"
                "Example format: 'High-priority signal: BTC dropped 3.8% while Narrative Agent detected coordinated negative posts. Mobility notes unusual ship activity. Risk level: Elevated.'\n\n"
                f"Alerts:\n" + "\n".join(alert_texts)
            )
            summary = await llm.complete(prompt, max_tokens=150, temperature=0.3)
            return summary.replace("JARVIS:", "").replace("Jarvis:", "").strip()
        except Exception as exc:
            logger.warning("CEO summary generation failed: %s", exc)
            return ""


    async def run_mission(
        self,
        mission_id: str,
        query: str,
        sources: Optional[List[str]] = None,
        llm_plan: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute the full mission pipeline."""
        self.status = AgentStatus.WORKING
        log = self._swarm.log
        mission = await self._swarm.get_mission(mission_id)
        results: Dict[str, Any] = {"stages": {}}

        if llm_plan:
            results["llm_plan"] = llm_plan

        try:
            self._set_stage(mission, "ingesting")
            self._current_task = f"Mission {mission_id}: ingesting"
            await log.add(self.agent_id, "stage_start", "Ingesting data from sources", mission_id)

            ingestion_agent = self._swarm._agents.get("ingestion")
            if not ingestion_agent:
                raise RuntimeError("IngestionAgent not registered")

            ingest_plan = await ingestion_agent.think({
                "query": query, "sources": sources, "limit": 10,
            })
            ingest_result = await ingestion_agent.act(ingest_plan)
            events = ingest_result.get("events", [])
            results["stages"]["ingestion"] = {
                "total": ingest_result.get("total", 0),
                "source_counts": ingest_result.get("source_counts", {}),
                "source_errors": ingest_result.get("source_errors", {}),
            }
            await log.add("ingestion", "stage_complete",
                          f"Collected {len(events)} events", mission_id)

            if not events:
                self._set_stage(mission, "complete")
                results["summary"] = "No events collected"
                self.status = AgentStatus.IDLE
                self._current_task = None
                return results
            self._set_stage(mission, "analyzing")
            self._current_task = f"Mission {mission_id}: analyzing"
            await log.add(
                self.agent_id,
                "stage_start",
                f"Analysing {len(events)} events",
                mission_id,
            )

            analysis_agent = self._swarm._agents.get("analysis")
            if not analysis_agent:
                raise RuntimeError("AnalysisAgent not registered")

            analysis_plan = await analysis_agent.think({"events": events, "enrich": True})
            analysis_result = await analysis_agent.act(analysis_plan)
            events = analysis_result.get("events", events)
            results["stages"]["analysis"] = {
                "enriched_count": analysis_result.get("enriched_count", 0),
                "actors_found": analysis_result.get("actors_found", 0),
                "sentiments_scored": analysis_result.get("sentiments_scored", 0),
                "llm_insights": analysis_result.get("llm_insights"),
            }
            await log.add(
                "analysis",
                "stage_complete",
                f"Enriched {analysis_result.get('enriched_count', 0)} events, "
                f"{analysis_result.get('actors_found', 0)} actors found",
                mission_id,
            )
            self._set_stage(mission, "persisting")
            self._current_task = f"Mission {mission_id}: persisting"
            await log.add(self.agent_id, "stage_start", "Persisting to DB + graph", mission_id)

            # Persist events to PostgreSQL
            db_persisted = 0
            try:
                from storage.database import AsyncSessionFactory
                from storage.event_repo import EventRepository
                async with AsyncSessionFactory() as session:
                    repo = EventRepository(session)
                    await repo.upsert_many(events)
                    await session.commit()
                    db_persisted = len(events)
            except Exception as db_exc:
                logger.warning("DB persist failed: %s", db_exc)

            # Write to Neo4j knowledge graph
            graph_agent = self._swarm._agents.get("graph")
            graph_result = {"events_written": 0}
            if graph_agent:
                graph_plan = await graph_agent.think({"events": events})
                graph_result = await graph_agent.act(graph_plan)

            results["stages"]["persistence"] = {
                "db_persisted": db_persisted,
                "graph_written": graph_result.get("events_written", 0),
            }
            await log.add(
                "graph",
                "stage_complete",
                f"DB: {db_persisted}, Graph: {graph_result.get('events_written', 0)}",
                mission_id,
            )
            self._set_stage(mission, "detecting")
            self._current_task = f"Mission {mission_id}: detecting"
            await log.add(
                self.agent_id,
                "stage_start",
                "Running narrative + anomaly detection",
                mission_id,
            )

            narrative_agent = self._swarm._agents.get("narrative")
            anomaly_agent = self._swarm._agents.get("anomaly")

            # Run in parallel
            tasks = []
            if narrative_agent:
                async def run_narrative():
                    p = await narrative_agent.think({"persist": True})
                    return await narrative_agent.act(p)
                tasks.append(run_narrative())

            if anomaly_agent:
                async def run_anomaly():
                    p = await anomaly_agent.think({"persist": True})
                    return await anomaly_agent.act(p)
                tasks.append(run_anomaly())

            detection_results = await asyncio.gather(*tasks, return_exceptions=True)

            narrative_result = {}
            anomaly_result = {}
            for i, res in enumerate(detection_results):
                if isinstance(res, Exception):
                    logger.warning("Detection task %d failed: %s", i, res)
                    continue
                if i == 0 and narrative_agent:
                    narrative_result = res
                elif anomaly_agent:
                    anomaly_result = res

            results["stages"]["detection"] = {
                "narratives": narrative_result.get("signals_detected", 0),
                "anomalies": anomaly_result.get("alerts_detected", 0),
            }
            await log.add(
                "narrative",
                "stage_complete",
                f"Detected {narrative_result.get('signals_detected', 0)} signals",
                mission_id,
            )
            await log.add(
                "anomaly",
                "stage_complete",
                f"Detected {anomaly_result.get('alerts_detected', 0)} alerts",
                mission_id,
            )
            self._set_stage(mission, "complete")
            self._current_task = f"Mission {mission_id}: generating brief"

            # Generate LLM intelligence brief
            llm = self._swarm.llm
            if llm and llm.available:
                try:
                    event_summaries = []
                    for e in events[:15]:  # Top 15 events for context
                        title = e.get("title", "")[:100]
                        src = e.get("source", "")
                        sent = e.get("sentiment", "")
                        actors = ", ".join(
                            a.get("name", str(a)) if isinstance(a, dict) else str(a)
                            for a in e.get("actors", [])[:3]
                        )
                        event_summaries.append(
                            f"- [{src}] {title} (sentiment: {sent}, actors: {actors})"
                        )

                    brief = await llm.complete(
                        prompt=(
                            f"Generate a concise intelligence brief for the query: \"{query}\"\n\n"
                            f"Data collected: {len(events)} events from "
                            f"{len(ingest_result.get('source_counts', {}))} sources\n"
                            f"Actors identified: {analysis_result.get('actors_found', 0)}\n"
                            f"Narratives detected: {narrative_result.get('signals_detected', 0)}\n"
                            f"Anomalies detected: {anomaly_result.get('alerts_detected', 0)}\n\n"
                            f"Key events:\n" + "\n".join(event_summaries) + "\n\n"
                            "Provide:\n"
                            "1. SITUATION SUMMARY (2-3 sentences)\n"
                            "2. KEY FINDINGS (bullet points)\n"
                            "3. RISK ASSESSMENT (low/medium/high with reasoning)\n"
                            "4. RECOMMENDED ACTIONS (for an intelligence analyst)\n"
                        ),
                        max_tokens=1024,
                        temperature=0.3,
                    )
                    results["intelligence_brief"] = brief
                    await log.add(
                        self.agent_id,
                        "llm_brief",
                        f"Intelligence brief generated ({len(brief)} chars)",
                        mission_id,
                    )
                except Exception as llm_exc:
                    logger.warning("LLM brief generation failed: %s", llm_exc)
                    results["intelligence_brief"] = None

            self._current_task = None
            self.status = AgentStatus.IDLE

            results["summary"] = (
                f"Collected {len(events)} events from "
                f"{len(ingest_result.get('source_counts', {}))} sources, "
                f"found {analysis_result.get('actors_found', 0)} actors, "
                f"{narrative_result.get('signals_detected', 0)} narratives, "
                f"{anomaly_result.get('alerts_detected', 0)} anomalies"
            )
            await log.add(self.agent_id, "mission_complete", results["summary"], mission_id)
            return results

        except Exception as exc:
            self.status = AgentStatus.ERROR
            self._current_task = f"Error: {exc}"
            await log.add(self.agent_id, "mission_error", str(exc), mission_id)
            if mission:
                mission["stage"] = "failed"
            raise

    @staticmethod
    def _set_stage(mission: Optional[Dict[str, Any]], stage: str) -> None:
        if mission:
            mission["stage"] = stage

