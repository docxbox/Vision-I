"""
agents/analysis_agent.py
────────────────────────
AnalysisAgent — enriches events with NLP (NER, sentiment, entity resolution)
and uses LLM for deeper intelligence insights.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from agents.base import AgentBase, AgentStatus
from core.enricher import Enricher
from nlp.pipeline import NLPPipeline

logger = logging.getLogger("vision_i.agents.analysis")


class AnalysisAgent(AgentBase):
    """
    Wraps the existing Enricher + NLPPipeline to enrich raw events with
    article bodies, named entities, sentiment scores, and canonical actor names.
    Optionally uses LLM for deeper analytical insights.
    """

    def __init__(
        self,
        nlp: NLPPipeline,
        enricher: Enricher,
        llm: Optional[Any] = None,
    ) -> None:
        super().__init__(
            agent_id="analysis",
            name="Analysis Agent",
            role="NLP enrichment + LLM analysis — NER, sentiment, entity resolution, insights",
        )
        self._nlp = nlp
        self._enricher = enricher
        self._llm = llm

    async def think(self, context: Dict[str, Any]) -> Dict[str, Any]:
        events = context.get("events", [])
        enrich_bodies = context.get("enrich", True)

        return {
            "action": "analyze",
            "event_count": len(events),
            "enrich_bodies": enrich_bodies,
            "events": events,
        }

    async def act(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        self.status = AgentStatus.WORKING
        events = plan.get("events", [])
        self._current_task = f"Analysing {len(events)} events"

        try:
            loop = asyncio.get_running_loop()

            # 1 — Enrich article bodies (I/O-bound)
            if plan.get("enrich_bodies", True) and events:
                events = await loop.run_in_executor(
                    None, lambda: self._enricher.enrich(events)
                )

            # 2 — NLP pipeline: NER + Sentiment + Entity Resolution (CPU-bound)
            if events:
                events = await loop.run_in_executor(
                    None, lambda: self._nlp.process(events)
                )

            # Count results
            actors_found = sum(len(e.get("actors", [])) for e in events)
            sentiments_scored = sum(1 for e in events if e.get("sentiment"))

            # 3 — LLM-powered insights (if available)
            llm_insights = None
            if self._llm and self._llm.available and events:
                try:
                    llm_insights = await self._generate_insights(events)
                except Exception as llm_exc:
                    logger.warning("LLM insights failed: %s", llm_exc)

            self.status = AgentStatus.IDLE
            self._current_task = None

            return {
                "events": events,
                "enriched_count": len(events),
                "actors_found": actors_found,
                "sentiments_scored": sentiments_scored,
                "llm_insights": llm_insights,
            }

        except Exception as exc:
            self.status = AgentStatus.ERROR
            self._current_task = f"Error: {exc}"
            self.logger.error("Analysis failed: %s", exc, exc_info=True)
            return {"events": events, "enriched_count": 0, "actors_found": 0, "sentiments_scored": 0}

    async def _generate_insights(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Use LLM to extract deeper insights from processed events."""
        # Build a concise event summary for the LLM
        summaries = []
        for e in events[:20]:  # Limit to 20 events for token efficiency
            title = e.get("title", "")[:120]
            source = e.get("source", "unknown")
            sentiment = e.get("sentiment", "unknown")
            actors = ", ".join(
                a.get("name", str(a)) if isinstance(a, dict) else str(a)
                for a in e.get("actors", [])[:5]
            )
            summaries.append(f"[{source}|{sentiment}] {title} (actors: {actors})")

        prompt = (
            f"Analyze these {len(events)} intelligence events and provide structured insights:\n\n"
            + "\n".join(summaries)
            + "\n\nRespond with JSON:\n"
            "{\n"
            '  "dominant_theme": "the main topic/theme across events",\n'
            '  "key_actors": ["top 5 most significant actors/entities"],\n'
            '  "sentiment_trend": "overall sentiment direction and significance",\n'
            '  "connections": ["notable connections between events or actors"],\n'
            '  "emerging_risks": ["potential risks or escalation indicators"],\n'
            '  "confidence": "low/medium/high confidence in analysis"\n'
            "}"
        )

        return await self._llm.complete_json(prompt, max_tokens=768)
