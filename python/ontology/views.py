"""
ontology/views.py
-----------------
Precomputed ontology-facing serving views.

These functions expose the "world model" the UI should consume:
  - situations: promoted, explainable events with priority
  - actor detail: influence + recent related events
  - graph snapshot: actor -> event -> asset style topology

They intentionally avoid heavy NLP or correlation work at request time.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.database import DecisionModel, EventModel, NarrativeModel, OntologyActorModel, SignalModel
from storage.event_repo import _row_to_event


def _priority_score(event: Dict[str, Any]) -> float:
    confidence = float(event.get("confidence_score") or 0.25)
    signal_count = int(event.get("signal_count") or 0)
    source_count = len(((event.get("extras") or {}).get("sources") or []))
    sentiment_score = abs(float((event.get("sentiment") or {}).get("score") or 0.0))
    recency_bonus = 0.2 if event.get("source") == "composite" else 0.0
    return round(
        confidence * 0.55
        + min(signal_count / 8.0, 1.0) * 0.20
        + min(source_count / 4.0, 1.0) * 0.15
        + min(sentiment_score, 1.0) * 0.10
        + recency_bonus,
        3,
    )


def _event_to_situation(event: Dict[str, Any]) -> Dict[str, Any]:
    actors = event.get("actors") or []
    supporting_signals = event.get("supporting_signals") or []
    extras = event.get("extras") or {}
    return {
        "id": event.get("event_id"),
        "title": event.get("title"),
        "summary": event.get("description") or event.get("reasoning") or "",
        "event_type": event.get("event_type"),
        "timestamp": event.get("timestamp"),
        "confidence_score": event.get("confidence_score") or 0.0,
        "priority_score": _priority_score(event),
        "signal_count": event.get("signal_count") or len(supporting_signals),
        "supporting_signals": supporting_signals,
        "reasoning": event.get("reasoning"),
        "actors": actors[:5],
        "primary_actor": actors[0]["name"] if actors else None,
        "location": event.get("location"),
        "sentiment": event.get("sentiment"),
        "narrative_tags": event.get("tags") or [],
        "source_mix": extras.get("sources") or [event.get("source")],
    }


def _actor_id(name: str) -> str:
    return f"actor:{name.lower().replace(' ', '_')}"


def _derived_influence(mention_count: int, event_count: int, source_count: int, narrative_count: int) -> float:
    import math
    mention_signal = min(math.log10(max(mention_count, 0) + 1) / 3.2, 1.0)
    event_signal = min(max(event_count, 0) / 25.0, 1.0)
    source_signal = min(max(source_count, 0) / 8.0, 1.0)
    narrative_signal = min(max(narrative_count, 0) / 8.0, 1.0)
    return round(
        mention_signal * 0.45
        + event_signal * 0.25
        + source_signal * 0.15
        + narrative_signal * 0.15,
        3,
    )


def _avg_event_sentiment(events: List[Dict[str, Any]]) -> float:
    scores = []
    for event in events:
        score = ((event.get("sentiment") or {}).get("score"))
        if score is not None:
            try:
                scores.append(float(score))
            except Exception:
                pass
    return round(sum(scores) / len(scores), 3) if scores else 0.0


def _actor_names_match(actor_list: List[Dict[str, Any]], actor_id: str, canonical_name: str | None = None) -> bool:
    for actor in actor_list or []:
        raw_name = (actor.get("name") or "").strip()
        canonical = (actor.get("canonical") or raw_name).strip()
        if not canonical:
            continue
        if _actor_id(canonical) == actor_id or _actor_id(raw_name) == actor_id:
            return True
        if canonical_name and canonical.lower() == canonical_name.lower():
            return True
    return False


async def _build_actor_evidence(
    session: AsyncSession,
    actor_id: str,
    canonical_name: str,
    recent_events: List[Dict[str, Any]],
) -> Dict[str, Any]:
    signal_rows = (
        await session.execute(
            select(SignalModel)
            .order_by(desc(SignalModel.timestamp))
            .limit(250)
        )
    ).scalars().all()

    matched_signals = []
    cluster_ids: set[str] = set()
    for row in signal_rows:
        names = [str(a).strip().lower() for a in (row.actors or []) if str(a).strip()]
        if canonical_name.lower() in names:
            matched_signals.append(row)
            if row.cluster_id:
                cluster_ids.add(row.cluster_id)

    narrative_rows = (
        await session.execute(
            select(NarrativeModel)
            .order_by(desc(NarrativeModel.detected_at))
            .limit(200)
        )
    ).scalars().all()

    matched_narratives = [
        row for row in narrative_rows
        if any((str(actor).strip().lower() == canonical_name.lower()) for actor in (row.actors or []))
    ]

    recent_event_ids = [event.get("id") for event in recent_events if event.get("id")]
    decision_history = []
    if recent_event_ids:
        decision_rows = (
            await session.execute(
                select(DecisionModel)
                .where(DecisionModel.event_id.in_(recent_event_ids))
                .order_by(desc(DecisionModel.created_at))
                .limit(12)
            )
        ).scalars().all()
        decision_history = [
            {
                "decision_id": str(row.id),
                "event_id": row.event_id,
                "coa_text": row.coa_text,
                "status": row.status,
                "outcome": row.outcome,
                "analyst": row.analyst,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in decision_rows
        ]

    return {
        "signal_count": len(matched_signals),
        "cluster_count": len(cluster_ids),
        "narrative_count": len(matched_narratives),
        "decision_count": len(decision_history),
        "signals": [
            {
                "signal_id": row.signal_id,
                "title": row.title,
                "source": row.source,
                "signal_type": row.signal_type,
                "confidence": row.confidence,
                "cluster_id": row.cluster_id,
                "timestamp": row.timestamp.isoformat() if row.timestamp else None,
            }
            for row in matched_signals[:8]
        ],
        "narratives": [
            {
                "narrative_id": row.narrative_id,
                "topic": row.topic,
                "signal_type": row.signal_type,
                "severity": row.severity,
                "strength": row.strength,
                "confidence": row.confidence,
                "detected_at": row.detected_at.isoformat() + "Z" if row.detected_at else None,
            }
            for row in matched_narratives[:6]
        ],
        "decision_history": decision_history,
    }


async def build_situation_overview(session: AsyncSession, limit: int = 12) -> Dict[str, Any]:
    result = await session.execute(
        select(EventModel)
        .order_by(desc(EventModel.timestamp))
        .limit(max(limit * 4, 40))
    )
    rows = result.scalars().all()
    events = [_row_to_event(row) for row in rows]
    situations = [_event_to_situation(event) for event in events]
    situations.sort(key=lambda item: item["priority_score"], reverse=True)

    top = situations[:limit]
    return {
        "generated_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "situations": top,
        "total": len(top),
    }


async def get_event_detail(session: AsyncSession, event_id: str) -> Optional[Dict[str, Any]]:
    row = (
        await session.execute(
            select(EventModel).where(EventModel.event_id == event_id)
        )
    ).scalar_one_or_none()
    if row is None:
        return None

    event = _row_to_event(row)
    return {
        "event": event,
        "situation": _event_to_situation(event),
        "ontology": {
            "object_type": "Event",
            "relationships": [
                {"type": "SUPPORTS", "count": len(event.get("supporting_signals") or [])},
                {"type": "PART_OF", "count": len(event.get("tags") or [])},
                {"type": "AFFECTS", "count": 1 if event.get("event_type") in {"market", "transport", "disaster"} else 0},
            ],
        },
    }


async def _derive_actor_detail_from_events(
    session: AsyncSession,
    actor_id: str,
    actor_seed: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Fallback when the ontology actor cache has not been populated yet.
    We derive a minimal actor view from recent events so the endpoint remains usable.
    """
    event_rows = (
        await session.execute(
            select(EventModel)
            .order_by(desc(EventModel.timestamp))
            .limit(400)
        )
    ).scalars().all()

    canonical_name: Optional[str] = (actor_seed or {}).get("name")
    entity_type = (actor_seed or {}).get("type") or "UNKNOWN"
    aliases: set[str] = set()
    source_set: set[str] = set()
    mention_count = int((actor_seed or {}).get("mention_count") or 0)
    influence_score = (actor_seed or {}).get("influence_score")
    related_events: List[Dict[str, Any]] = []

    for row in event_rows:
        event = _row_to_event(row)
        matched = False

        for actor in (event.get("actors") or []):
            raw_name = (actor.get("name") or "").strip()
            canonical = (actor.get("canonical") or raw_name).strip()
            if not canonical:
                continue
            if _actor_id(canonical) != actor_id and _actor_id(raw_name) != actor_id:
                continue

            if canonical_name is None:
                canonical_name = canonical
            aliases.update(name for name in {raw_name, canonical} if name)
            entity_type = actor.get("type") or entity_type
            if not actor_seed:
                mention_count += 1
            if event.get("source"):
                source_set.add(event["source"])
            matched = True

        if matched and len(related_events) < 12:
            related_events.append(_event_to_situation(event))

    if canonical_name is None:
        return None

    evidence = await _build_actor_evidence(session, actor_id, canonical_name, related_events)

    if influence_score is None:
        influence_score = _derived_influence(mention_count, len(related_events), len(source_set), evidence.get("narrative_count", 0))

    return {
        "id": actor_id,
        "name": canonical_name,
        "type": entity_type,
        "mention_count": mention_count,
        "event_count": len(related_events),
        "source_count": max(len(source_set), 1 if related_events else 0),
        "influence_score": influence_score,
        "sentiment_score": _avg_event_sentiment(related_events),
        "aliases": sorted(a for a in aliases if a != canonical_name),
        "recent_events": related_events,
        **evidence,
    }


async def get_actor_detail(
    session: AsyncSession,
    actor_id: str,
    graph: Any | None = None,
) -> Optional[Dict[str, Any]]:
    actor_row = (
        await session.execute(
            select(OntologyActorModel).where(OntologyActorModel.actor_id == actor_id)
        )
    ).scalar_one_or_none()
    if actor_row is None:
        actor_seed = None
        if graph is not None and getattr(graph, "available", False):
            try:
                actor_seed = graph.get_actor(actor_id)
            except Exception:
                actor_seed = None
        return await _derive_actor_detail_from_events(session, actor_id, actor_seed=actor_seed)

    event_rows = (
        await session.execute(
            select(EventModel)
            .order_by(desc(EventModel.timestamp))
            .limit(150)
        )
    ).scalars().all()

    related_events = []
    for row in event_rows:
        event = _row_to_event(row)
        if _actor_names_match(event.get("actors") or [], actor_id, actor_row.canonical_name):
            related_events.append(_event_to_situation(event))

    evidence = await _build_actor_evidence(session, actor_id, actor_row.canonical_name, related_events[:12])

    source_count = actor_row.source_count or len({event.get("source") for event in related_events if event.get("source")})
    narrative_count = evidence.get("narrative_count", 0)
    influence_score = actor_row.influence_score
    if influence_score is None or influence_score <= 0:
        influence_score = _derived_influence(actor_row.mention_count or 0, len(related_events), source_count, narrative_count)

    return {
        "id": actor_row.actor_id,
        "name": actor_row.canonical_name,
        "type": actor_row.entity_type,
        "mention_count": actor_row.mention_count,
        "event_count": len(related_events),
        "source_count": source_count,
        "influence_score": influence_score,
        "sentiment_score": _avg_event_sentiment(related_events),
        "aliases": actor_row.aliases or [],
        "recent_events": related_events[:12],
        **evidence,
    }


async def build_graph_snapshot(session: AsyncSession, limit: int = 10) -> Dict[str, Any]:
    result = await session.execute(
        select(EventModel)
        .order_by(desc(EventModel.timestamp))
        .limit(max(limit * 3, 30))
    )
    rows = result.scalars().all()

    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    node_ids = set()
    actor_counts: Dict[str, int] = defaultdict(int)

    def add_node(node_id: str, **props: Any) -> None:
        if node_id in node_ids:
            return
        node_ids.add(node_id)
        nodes.append({"id": node_id, **props})

    for row in rows[:limit]:
        event = _row_to_event(row)
        event_id = event.get("event_id")
        sentiment = event.get("sentiment") or {}
        add_node(
            event_id,
            label=event.get("title"),
            group="event",
            event_type=event.get("event_type"),
            confidence_score=event.get("confidence_score"),
            sentiment_score=sentiment.get("score"),
            sentiment_label=sentiment.get("label"),
            timestamp=event.get("timestamp"),
        )

        for actor in (event.get("actors") or [])[:5]:
            name = (actor.get("name") or "").strip()
            if not name:
                continue
            actor_node_id = f"actor:{name.lower().replace(' ', '_')}"
            actor_counts[actor_node_id] += 1
            add_node(
                actor_node_id,
                label=name,
                group="actor",
                entity_type=actor.get("type", "UNKNOWN"),
                mention_count=actor_counts[actor_node_id],
            )
            edges.append({
                "source": actor_node_id,
                "target": event_id,
                "relation": "INFLUENCES" if actor.get("type") in {"ORG", "PERSON"} else "ASSOCIATED_WITH",
                "weight": 1,
            })

        location = event.get("location") or {}
        if location.get("name"):
            location_id = f"loc:{location['name'].lower().replace(' ', '_')}"
            add_node(location_id, label=location["name"], group="location")
            edges.append({
                "source": event_id,
                "target": location_id,
                "relation": "OCCURRED_IN",
                "weight": 1,
            })

    for node in nodes:
        if node.get("group") == "actor":
            mentions = actor_counts.get(node["id"], 1)
            node["value"] = mentions
            node["mention_count"] = mentions
            node["influence_score"] = _derived_influence(mentions, mentions, 1, 0)

    return {
        "generated_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "nodes": nodes,
        "edges": edges,
        "node_count": len(nodes),
        "edge_count": len(edges),
    }


async def refresh_precomputed_views(event_bus: Any | None) -> None:
    if event_bus is None:
        return

    from storage.database import get_session
    from ontology.operations import build_operations_overview

    async with get_session() as session:
        overview = await build_situation_overview(session, limit=12)
        graph = await build_graph_snapshot(session, limit=12)
        operations = await build_operations_overview(session, limit=10)

    await event_bus.cache_set("precomputed:ontology:overview", overview)
    await event_bus.cache_set("precomputed:ontology:graph", graph)
    await event_bus.cache_set("precomputed:operations:overview", operations)
