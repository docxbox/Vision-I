"""
api/routers/copilot.py
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Analyst Copilot â€” LLM-powered reasoning over ontology objects.

Endpoints:
  POST /copilot/ask                   â€” free-form question with ontology context
  POST /copilot/explain/{event_id}    â€” explain why an event is high risk
  GET  /copilot/similar/{event_id}    â€” find similar past events + their decisions
  GET  /copilot/recommend/{event_id}  â€” AI-recommended next action for an event
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("vision_i.api.copilot")
router = APIRouter(tags=["copilot"])

# ── Response schemas ───────────────────────────────────────────────────────

class CopilotAskResponse(BaseModel):
    question: str = ""
    answer: str = ""
    context_summary: Dict[str, Any] = Field(default_factory=dict)
    llm_used: bool = False
    model: str = ""

class CopilotExplainResponse(BaseModel):
    event_id: str = ""
    event_title: str = ""
    risk_score: float = 0.0
    briefing: str = ""
    evidence: Dict[str, Any] = Field(default_factory=dict)
    llm_used: bool = False
    model: str = ""

class CopilotSimilarResponse(BaseModel):
    event_id: str = ""
    event_type: str = ""
    similar_decisions: List[Any] = Field(default_factory=list)
    total: int = 0
    insight: str = ""

class CopilotRecommendResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    event_id: str = ""
    risk_score: float = 0.0
    primary_recommendation: str = ""
    historical_precedent: str = ""
    confidence: str = ""
    reasoning: str = ""
    evidence: Dict[str, Any] = Field(default_factory=dict)
    llm_used: bool = False

class CopilotSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

class RuntimeLlmOverride(BaseModel):
    provider: str = ""
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key: str = ""
    enabled: bool = True

class AskRequest(BaseModel):
    question: str
    event_id: Optional[str] = None
    actor_id: Optional[str] = None
    narrative_id: Optional[str] = None
    context: Optional[str] = None
    history: List[Dict[str, Any]] = Field(default_factory=list)
    analyst: str = "analyst"
    llm_runtime: Optional[RuntimeLlmOverride] = None


def _context_summary(body: AskRequest | None, context: Dict[str, Any], similar_count: int = 0) -> Dict[str, Any]:
    event = context.get("event") or {}
    return {
        "event_id": body.event_id if body else event.get("event_id"),
        "has_event": bool(event),
        "past_decisions_count": len(context.get("past_decisions") or []),
        "alert_count": len(event.get("alerts") or []),
        "narrative_count": len(event.get("narratives") or []),
        "actor_count": len(event.get("actors") or []),
        "similar_event_count": similar_count,
    }

_QUESTION_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "about",
    "all", "any", "tell", "me", "show", "give", "what", "whats", "who", "whom",
    "is", "are", "was", "were", "do", "does", "did", "related", "event", "events",
    "recent", "there", "this", "that", "please", "find", "list", "get", "info",
    "information", "regarding", "concerning", "near", "around", "latest", "current",
    "happening", "going", "anything", "everything", "you", "your", "can", "could",
    "would", "should", "have", "has", "had", "them", "they", "their", "more", "some",
}


def _question_terms(question: str) -> List[str]:
    """Pull salient subject terms from a free-form question for corpus retrieval."""
    import re
    words = re.findall(r"[A-Za-z][A-Za-z0-9'_-]{2,}", question or "")
    terms, seen = [], set()
    for w in words:
        lw = w.lower()
        if lw in _QUESTION_STOPWORDS or lw in seen:
            continue
        seen.add(lw)
        terms.append(w)
    return terms[:8]


async def _retrieve_corpus_context(session, graph, question: str) -> Dict[str, Any]:
    """RAG grounding for free-form copilot questions. Pulls the most relevant events from
    the WHOLE corpus (trigram search over all sources) plus actor relationships and
    connected events from the Neo4j graph — so JARVIS answers about any subject (e.g.
    'Trump') instead of only the handful of events cached in the current page circuit."""
    out: Dict[str, Any] = {"events": [], "correlated": [], "graph_events": [], "terms": []}
    terms = _question_terms(question)
    out["terms"] = terms
    if not terms:
        return out

    combined = " OR ".join(terms)
    try:
        from storage.event_repo import EventRepository
        repo = EventRepository(session)
        _total, events = await repo.list_events(
            query=combined, limit=14, sort_by="timestamp", with_total=False,
        )
        out["events"] = events or []
    except Exception as exc:
        logger.warning("Copilot corpus event retrieval failed: %s", exc)

    # Build candidate actor ids from the question terms + actors on the retrieved events.
    # correlated_actors / actor_events filter by `a.id IN $ids`, so unknown ids are harmless
    # (no per-id round-trips needed). Cap to keep the two graph queries cheap.
    actor_ids: List[str] = []
    seen_ids = set()

    def _add_actor(name: str) -> None:
        slug = (name or "").strip().lower().replace(" ", "_")
        aid = f"actor:{slug}"
        if slug and aid not in seen_ids:
            seen_ids.add(aid)
            actor_ids.append(aid)

    for t in terms:
        _add_actor(t)
    for ev in out["events"][:8]:
        for a in (ev.get("actors") or []):
            if isinstance(a, dict):
                nm = a.get("name") or a.get("canonical") or ""
                if nm:
                    _add_actor(nm)

    if graph and getattr(graph, "available", False) and actor_ids:
        ids = actor_ids[:10]
        try:
            out["correlated"] = graph.correlated_actors(ids, limit=18)
        except Exception as exc:
            logger.warning("Copilot correlated_actors failed: %s", exc)
        try:
            out["graph_events"] = graph.actor_events(ids, limit=12)
        except Exception as exc:
            logger.warning("Copilot actor_events failed: %s", exc)
    return out


def _format_corpus_for_prompt(corpus: Dict[str, Any]) -> str:
    """Serialize retrieved corpus + graph evidence into a compact prompt block."""
    parts: List[str] = []
    events = corpus.get("events") or []
    if events:
        parts.append("## Retrieved Corpus Events (most relevant to the question, across all sources)")
        for ev in events[:14]:
            title = (ev.get("title") or "").strip()
            src = ev.get("source") or "?"
            ts = ev.get("timestamp") or ""
            risk = ev.get("risk_score") or 0
            eid = ev.get("event_id") or ""
            parts.append(f"- {title[:160]} | source={src} | risk={risk:.2f} | time={ts} | id={eid}")
    correlated = corpus.get("correlated") or []
    if correlated:
        parts.append("\n## Graph Actor Relationships (co-mentioned across the corpus)")
        for c in correlated[:18]:
            parts.append(f"- {c.get('source')} {c.get('rel', 'CO_MENTIONED')} {c.get('target')}")
    gevents = corpus.get("graph_events") or []
    if gevents:
        parts.append("\n## Graph-Connected Events (linked to the subject actors)")
        for ge in gevents[:12]:
            parts.append(
                f"- {(ge.get('title') or '')[:140]} | source={ge.get('source')} "
                f"| actor={ge.get('actor')} | id={ge.get('id')}"
            )
    return "\n".join(parts)


async def _build_event_context(session, event_id: str) -> Dict[str, Any]:
    """Pull full event context from ontology + related decisions."""
    from ontology.views import get_event_detail
    from storage.decision_repo import find_similar_decisions

    context: Dict[str, Any] = {}

    try:
        event_detail = await get_event_detail(session, event_id)
        if event_detail:
            context["event"] = event_detail
    except Exception as exc:
        logger.warning("Event detail fetch failed for %s: %s", event_id, exc)

    try:
        event_type = (context.get("event") or {}).get("event_type", "")
        past_decisions = await find_similar_decisions(session, event_type=event_type, limit=5)
        context["past_decisions"] = past_decisions
    except Exception as exc:
        logger.warning("Past decisions fetch failed: %s", exc)

    return context


def _format_context_for_prompt(context: Dict[str, Any]) -> str:
    """Serialize ontology context into a compact prompt string."""
    parts: List[str] = []

    event = context.get("event")
    if event:
        parts.append("## Event Under Analysis")
        parts.append(f"- Title: {event.get('title', 'Unknown')}")
        parts.append(f"- Type: {event.get('event_type', 'unknown')}")
        parts.append(f"- Risk Score: {event.get('risk_score', 0):.2f}")
        parts.append(f"- Confidence: {event.get('confidence_score', 0):.2f}")
        sentiment = event.get("sentiment") or {}
        parts.append(f"- Sentiment: {sentiment.get('label', 'N/A')} ({sentiment.get('score', 0):.2f})")
        loc = event.get("location") or {}
        if loc.get("name"):
            parts.append(f"- Location: {loc['name']}")
        actors = event.get("actors") or []
        if actors:
            actor_str = ", ".join(a.get("canonical") or a.get("name", "") for a in actors[:5])
            parts.append(f"- Key Actors: {actor_str}")
        if event.get("reasoning"):
            parts.append(f"- System Reasoning: {event['reasoning']}")
        narratives = event.get("narratives") or []
        if narratives:
            topics = [n.get("topic", "") for n in narratives[:3]]
            parts.append(f"- Active Narratives: {', '.join(topics)}")
        alerts = event.get("alerts") or []
        if alerts:
            alert_str = "; ".join(
                f"{a.get('title', '')} ({a.get('severity', '')})" for a in alerts[:3]
            )
            parts.append(f"- Active Alerts: {alert_str}")
        coas = event.get("courses_of_action") or []
        if coas:
            coa_titles = [c.get("title", "") for c in coas]
            parts.append(f"- Proposed COAs: {', '.join(coa_titles)}")

    past_decisions = context.get("past_decisions") or []
    if past_decisions:
        parts.append("\n## Historical Decisions for Similar Events")
        for d in past_decisions[:4]:
            parts.append(
                f"- [{d.get('status', 'approved')}] {d.get('coa_text', '')} "
                f"(analyst: {d.get('analyst', '?')}, outcome: {d.get('outcome') or 'not recorded'})"
            )

    return "\n".join(parts)

@router.post("/ask", summary="Ask the Analyst Copilot a question with ontology context", response_model=CopilotAskResponse)
async def copilot_ask(body: AskRequest, request: Request):
    """
    Free-form Q&A with ontology grounding.

    If an event_id is provided the copilot pulls the full event context
    (actors, narratives, alerts, past decisions) before answering.
    Works in degraded mode when no LLM is configured.
    """
    if not request.app.state.db_available:
        return JSONResponse({"error": "Database unavailable"}, status_code=503)

    context: Dict[str, Any] = {}
    corpus_text = ""
    try:
        from storage.database import get_session
        graph = getattr(request.app.state, "graph", None)
        async with get_session() as session:
            if body.event_id:
                context = await _build_event_context(session, body.event_id)
            else:
                # No specific event → ground the free-form question in the whole corpus +
                # knowledge graph so JARVIS isn't limited to the page's cached events.
                corpus = await _retrieve_corpus_context(session, graph, body.question)
                corpus_text = _format_corpus_for_prompt(corpus)
    except Exception as exc:
        logger.warning("Context build failed: %s", exc)

    context_text = _format_context_for_prompt(context) if context else ""

    prompt = (
        "You are an expert intelligence analyst working inside the Vision-I Decision OS. "
        "Answer the analyst's question using ONLY the structured intelligence context below. "
        "Be concise, actionable, and specific. Use intelligence-grade language.\n\n"
    )
    if body.context:
        prompt += f"## Native Platform Context\n{body.context[:12000]}\n\n"
    if context_text:
        prompt += f"{context_text}\n\n"
    if corpus_text:
        prompt += f"{corpus_text}\n\n"
    if body.history:
        prompt += "## Recent JARVIS Conversation\n"
        for msg in body.history[-6:]:
            role = str(msg.get("role", "user"))[:20]
            content = str(msg.get("content", ""))[:800]
            prompt += f"- {role}: {content}\n"
        prompt += "\n"
    prompt += f"## Analyst Question\n{body.question}\n\n## Your Answer"

    llm = getattr(request.app.state, "llm", None)
    if llm and body.llm_runtime and body.llm_runtime.enabled:
        try:
            llm.apply_runtime_config(
                provider=body.llm_runtime.provider,
                api_key=body.llm_runtime.api_key,
                model=body.llm_runtime.model,
                base_url=body.llm_runtime.base_url,
            )
        except Exception as exc:
            logger.warning("Copilot runtime override failed: %s", exc)

    if llm and llm.available:
        try:
            answer = await llm.complete(
                prompt=prompt,
                system=(
                    "You are an expert intelligence analyst for the Vision-I Decision OS platform. "
                    "Provide concise, structured, actionable intelligence assessments. "
                    "Reference specific data from the context. Never hallucinate. "
                    "If context is insufficient, say so clearly."
                ),
                max_tokens=1024,
                temperature=0.2,
            )
            llm_used = True
        except Exception as exc:
            logger.error("LLM call failed: %s", exc)
            answer = _rule_based_answer(body.question, context)
            llm_used = False
    else:
        answer = _rule_based_answer(body.question, context)
        llm_used = False

    return {
        "question": body.question,
        "answer": answer,
        "context_summary": _context_summary(body, context),
        "llm_used": llm_used,
        "model": (getattr(llm, "last_model_used", None) or llm.model if llm and llm_used else "rule-based"),
    }


@router.post("/explain/{event_id}", summary="Explain why an event is flagged as high risk", response_model=CopilotExplainResponse)
async def copilot_explain(event_id: str, request: Request):
    """
    Generate a structured risk explanation for an ontology event.
    Pulls the full event context, then asks the LLM to produce a
    structured briefing covering: risk factors, actors, narratives, and recommended actions.
    """
    if not request.app.state.db_available:
        return JSONResponse({"error": "Database unavailable"}, status_code=503)

    context: Dict[str, Any] = {}
    try:
        from storage.database import get_session
        async with get_session() as session:
            context = await _build_event_context(session, event_id)
    except Exception as exc:
        logger.warning("Context build failed for %s: %s", event_id, exc)

    if not context.get("event"):
        return JSONResponse({"error": f"Event '{event_id}' not found"}, status_code=404)

    context_text = _format_context_for_prompt(context)

    prompt = (
        f"{context_text}\n\n"
        "## Task\n"
        "Directly answer 'Why are you showing me this?' about this event.\n"
        "Provide a direct CAUSAL intelligence explanation. Do not provide a generic summary.\n"
        "Structure exactly as follows:\n"
        "1. **Causal Trigger** â€” Exactly what anomaly or action triggered this alert?\n"
        "2. **Actor Intent** â€” What is the inferred intent of the actors involved?\n"
        "3. **Narrative Propagation** â€” How is this narrative spreading and who is amplifying it?\n"
        "4. **Tactical Recommendation** â€” What is the exact next step the analyst must take?\n"
        "\nBe direct, factual, and hyper-concise. Reference specific node connections from the context."
    )

    llm = getattr(request.app.state, "llm", None)
    if llm and llm.available:
        try:
            briefing = await llm.complete(
                prompt=prompt,
                system=(
                    "You are a senior intelligence analyst. Produce structured, actionable briefings. "
                    "Use intelligence-grade language. Be direct and specific."
                ),
                max_tokens=1500,
                temperature=0.2,
            )
            llm_used = True
        except Exception as exc:
            logger.error("LLM explain failed: %s", exc)
            briefing = _rule_based_explain(context)
            llm_used = False
    else:
        briefing = _rule_based_explain(context)
        llm_used = False

    event = context.get("event") or {}
    return {
        "event_id": event_id,
        "event_title": event.get("title", "Unknown"),
        "risk_score": event.get("risk_score", 0),
        "briefing": briefing,
        "evidence": _context_summary(AskRequest(question="", event_id=event_id), context),
        "llm_used": llm_used,
        "model": (getattr(llm, "last_model_used", None) or llm.model if llm and llm_used else "rule-based"),
    }


@router.get("/similar/{event_id}", summary="Find similar past events and their analyst decisions", response_model=CopilotSimilarResponse)
async def copilot_similar(
    event_id: str,
    request: Request,
    limit: int = Query(5, ge=1, le=20),
):
    """
    Return past events of the same type along with any decisions analysts
    made on them. This powers the 'what did analysts do in similar situations'
    recommendation feature.
    """
    if not request.app.state.db_available:
        return {"similar": [], "message": "Database unavailable"}

    try:
        from storage.database import get_session, EventModel
        from storage.decision_repo import find_similar_decisions
        from sqlalchemy import select
        from ontology.views import get_event_detail

        async with get_session() as session:
            # Get the reference event type
            result = await session.execute(
                select(EventModel.event_type).where(EventModel.event_id == event_id)
            )
            row = result.scalar_one_or_none()
            if not row:
                return JSONResponse({"error": "Event not found"}, status_code=404)

            event_type = row
            past_decisions = await find_similar_decisions(session, event_type=event_type, limit=limit)

        similar_count = len(past_decisions)
        return {
            "event_id": event_id,
            "event_type": event_type,
            "similar_decisions": past_decisions,
            "total": similar_count,
            "insight": _summarize_decisions(past_decisions),
        }
    except Exception as exc:
        logger.error("Similar events failed for %s: %s", event_id, exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/recommend/{event_id}", summary="AI-recommended next action for an event", response_model=CopilotRecommendResponse)
async def copilot_recommend(event_id: str, request: Request):
    """
    Returns a short, actionable recommendation for an event,
    backed by similar past decisions and ontology context.
    """
    if not request.app.state.db_available:
        return JSONResponse({"error": "Database unavailable"}, status_code=503)

    try:
        from storage.database import get_session
        async with get_session() as session:
            context = await _build_event_context(session, event_id)

        if not context.get("event"):
            return JSONResponse({"error": "Event not found"}, status_code=404)

        event = context["event"]
        past = context.get("past_decisions") or []

        # Determine recommendation from risk + past decisions
        risk = float(event.get("risk_score") or 0)
        most_common_coa = _most_common_coa(past)

        if risk >= 0.72:
            primary = "Escalate to analyst â€” risk score exceeds escalation threshold."
        elif risk >= 0.5:
            primary = "Issue stakeholder briefing â€” situation requires awareness update."
        else:
            primary = "Continue monitoring â€” situation is within normal parameters."

        recommendation = {
            "event_id": event_id,
            "risk_score": risk,
            "primary_recommendation": primary,
            "historical_precedent": (
                f"In {len(past)} similar past events, analysts most commonly chose: '{most_common_coa}'"
                if most_common_coa else "No historical precedent available."
            ),
            "confidence": "high" if len(past) >= 3 else "low",
            "reasoning": (
                f"Risk score {risk:.2f}, {len(event.get('alerts') or [])} active alerts, "
                f"{len(event.get('narratives') or [])} linked narratives."
            ),
            "evidence": _context_summary(AskRequest(question="", event_id=event_id), context, similar_count=len(past)),
            "llm_used": False,
        }

        llm = getattr(request.app.state, "llm", None)
        if llm and llm.available:
            context_text = _format_context_for_prompt(context)
            prompt = (
                f"{context_text}\n\n"
                "## Task\n"
                "In 2-3 sentences, recommend the single most important next action an analyst should take "
                "for this event. Be specific, reference the data, and explain why."
            )
            try:
                ai_rec = await llm.complete(
                    prompt=prompt,
                    system="You are an intelligence analyst. Give concise, actionable recommendations.",
                    max_tokens=256,
                    temperature=0.2,
                )
                recommendation["ai_recommendation"] = ai_rec.strip()
                recommendation["llm_used"] = True
            except Exception as exc:
                logger.warning("LLM recommend failed: %s", exc)

        return recommendation

    except Exception as exc:
        logger.error("Recommend failed for %s: %s", event_id, exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/summary", summary="Rule-based tactical summary (JARVIS)", response_model=CopilotSummaryResponse)
async def copilot_summary(
    request: Request,
    window_hours: int = Query(6, ge=1, le=72),
):
    if not request.app.state.db_available:
        return JSONResponse({"error": "Database unavailable"}, status_code=503)
    try:
        from intelligence.jarvis import build_tactical_summary
        return await build_tactical_summary(window_hours=window_hours)
    except Exception as exc:
        logger.error("Copilot summary failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)

def _rule_based_answer(question: str, context: Dict[str, Any]) -> str:
    event = context.get("event") or {}
    if not event:
        return (
            "No event context was loaded. To get a fuller answer, "
            "select an event in the Operations panel before asking. "
            "Configure an LLM API key (ANTHROPIC_API_KEY, OPENAI_API_KEY, or OPENROUTER_API_KEY) "
            "to enable AI-powered responses."
        )

    risk = float(event.get("risk_score") or 0)
    title = event.get("title", "this event")
    event_type = event.get("event_type", "unknown")
    actors = [a.get("canonical") or a.get("name", "") for a in (event.get("actors") or [])[:3]]
    past = context.get("past_decisions") or []

    parts = [
        f"**Event**: {title} ({event_type})",
        f"**Risk score**: {risk:.2f} ({'elevated' if risk >= 0.5 else 'moderate'})",
    ]
    if actors:
        parts.append(f"**Key actors**: {', '.join(actors)}")
    if past:
        parts.append(f"**Historical context**: {len(past)} similar past decisions recorded.")
        coa = _most_common_coa(past)
        if coa:
            parts.append(f'Analysts most frequently chose: "{coa}".')
    parts.append(
        "\n*Configure ANTHROPIC_API_KEY, OPENAI_API_KEY, or OPENROUTER_API_KEY "
        "for full AI-powered analysis.*"
    )
    return "\n".join(parts)


def _rule_based_explain(context: Dict[str, Any]) -> str:
    event = context.get("event") or {}
    risk = float(event.get("risk_score") or 0)
    alerts = event.get("alerts") or []
    narratives = event.get("narratives") or []
    actors = [a.get("canonical") or a.get("name", "") for a in (event.get("actors") or [])[:3]]
    coas = [c.get("title", "") for c in (event.get("courses_of_action") or [])]

    level = "CRITICAL" if risk >= 0.82 else "HIGH" if risk >= 0.64 else "MEDIUM" if risk >= 0.5 else "LOW"

    lines = [
        f"## Causal Trigger\nRisk anomaly detected: **{level}** (score {risk:.2f}). "
        f"Triggered by {len(alerts)} alerts interacting with {len(narratives)} active narratives.",
    ]
    if actors:
        lines.append(f"\n## Actor Intent\nInferred coordination among: {', '.join(actors)}")
    if narratives:
        topics = [n.get("topic", "") for n in narratives[:3]]
        lines.append(f"\n## Narrative Propagation\nTopics escalating: {', '.join(topics)}")
    if coas:
        lines.append(f"\n## Tactical Recommendation\n" + "\n".join(f"- {c}" for c in coas))
    lines.append(
        "\n*Configure an LLM API key for AI-powered briefings.*"
    )
    return "\n".join(lines)


def _most_common_coa(past_decisions: List[Dict[str, Any]]) -> Optional[str]:
    if not past_decisions:
        return None
    from collections import Counter
    coa_counter: Counter = Counter()
    for d in past_decisions:
        text = d.get("coa_text", "").strip()
        if text:
            coa_counter[text] += 1
    most_common = coa_counter.most_common(1)
    return most_common[0][0] if most_common else None


def _summarize_decisions(past_decisions: List[Dict[str, Any]]) -> str:
    if not past_decisions:
        return "No similar past decisions found."
    approved = sum(1 for d in past_decisions if d.get("status") == "approved")
    coa = _most_common_coa(past_decisions)
    summary = f"{len(past_decisions)} similar past decision(s) found. {approved} approved."
    if coa:
        summary += f' Most frequent action: \u201c{coa}\u201d.'
    return summary
