"""
ontology/operations.py
----------------------
Decision-layer serving views over ontology objects.

This module turns situations, alerts, and narratives into operator-facing
decision objects:
  - operations queue items
  - recommended playbooks
  - courses of action

It is intentionally read-optimized and precomputable.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.database import AlertModel, NarrativeModel
from storage.intelligence_repo import _alert_to_dict, _narrative_to_dict
from ontology.views import build_situation_overview


def _severity_rank(value: str | None) -> int:
    return {
        "critical": 4,
        "high": 3,
        "medium": 2,
        "low": 1,
    }.get((value or "").lower(), 0)


def _match_alerts(situation: Dict[str, Any], alerts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    title = (situation.get("title") or "").lower()
    actor_names = {(actor.get("name") or "").lower() for actor in (situation.get("actors") or [])}
    location_name = ((situation.get("location") or {}).get("name") or "").lower()

    matches: List[Dict[str, Any]] = []
    for alert in alerts:
        entity = (alert.get("entity") or "").lower()
        location = (alert.get("location") or "").lower()
        if entity and entity in actor_names:
            matches.append(alert)
            continue
        if location and location_name and location in location_name:
            matches.append(alert)
            continue
        if entity and entity in title:
            matches.append(alert)
    return matches


def _match_narratives(situation: Dict[str, Any], narratives: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    title = (situation.get("title") or "").lower()
    tags = {(tag or "").lower() for tag in (situation.get("narrative_tags") or [])}
    actor_names = {(actor.get("name") or "").lower() for actor in (situation.get("actors") or [])}

    matches: List[Dict[str, Any]] = []
    for narrative in narratives:
        topic = (narrative.get("topic") or "").lower()
        narrative_actors = {(actor or "").lower() for actor in (narrative.get("actors") or [])}
        if topic and topic in title:
            matches.append(narrative)
            continue
        if topic and any(tag in topic or topic in tag for tag in tags):
            matches.append(narrative)
            continue
        if actor_names & narrative_actors:
            matches.append(narrative)
    return matches


def _recommend_playbook(
    situation: Dict[str, Any],
    matched_alerts: List[Dict[str, Any]],
    matched_narratives: List[Dict[str, Any]],
) -> Dict[str, Any]:
    event_type = (situation.get("event_type") or "event").lower()
    location = ((situation.get("location") or {}).get("name") or "region")
    top_alert = max(matched_alerts, key=lambda a: _severity_rank(a.get("severity")), default=None)
    highest_narrative = max(matched_narratives, key=lambda n: _severity_rank(n.get("severity")), default=None)

    if top_alert and _severity_rank(top_alert.get("severity")) >= 3:
        name = "Escalation Review"
        objective = f"Escalate analyst review for {situation.get('title')} and confirm mitigation steps."
        approvals_required = True
    elif event_type in {"disaster", "health", "weather"}:
        name = "Crisis Monitoring"
        objective = f"Track humanitarian and operational impact in {location}."
        approvals_required = False
    elif event_type in {"market", "transport"}:
        name = "Market and Logistics Response"
        objective = f"Assess downstream disruption risk tied to {situation.get('title')}."
        approvals_required = False
    else:
        name = "Narrative Surveillance"
        objective = f"Monitor narrative propagation and actor coordination around {situation.get('title')}."
        approvals_required = False

    trigger = (
        top_alert.get("title")
        if top_alert
        else highest_narrative.get("topic")
        if highest_narrative
        else situation.get("reasoning") or "Priority-ranked ontology event"
    )

    steps = [
        {"name": "Generate briefing", "kind": "analysis", "automated": True},
        {"name": "Review linked actors", "kind": "human_review", "automated": False},
        {"name": "Notify stakeholders", "kind": "notification", "automated": approvals_required is False},
    ]
    if matched_alerts:
        steps.append({"name": "Acknowledge related alerts", "kind": "alert_workflow", "automated": False})

    return {
        "name": name,
        "status": "recommended",
        "objective": objective,
        "trigger_reason": trigger,
        "requires_approval": approvals_required,
        "steps": steps,
    }


def _courses_of_action(
    situation: Dict[str, Any],
    matched_alerts: List[Dict[str, Any]],
    matched_narratives: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    actor_names = [actor.get("name") for actor in (situation.get("actors") or []) if actor.get("name")]
    location = ((situation.get("location") or {}).get("name") or "affected region")
    title = situation.get("title") or "event"

    coas = [
        {
            "title": "Escalate to analyst",
            "expected_impact": "Faster human validation and higher-confidence operational understanding.",
            "risk": "Consumes analyst attention if the event decays quickly.",
            "dependencies": ["Analyst availability", "Context packet"],
        },
        {
            "title": "Issue stakeholder briefing",
            "expected_impact": f"Improves shared awareness for teams monitoring {location}.",
            "risk": "May over-communicate if corroboration weakens.",
            "dependencies": ["Narrative summary", "Source corroboration"],
        },
    ]

    if actor_names:
        coas.append(
            {
                "title": "Monitor actor network",
                "expected_impact": f"Tracks whether {', '.join(actor_names[:2])} are increasing influence or coordination.",
                "risk": "Can amplify noise if actors are only co-mentioned.",
                "dependencies": ["Ontology graph", "Influence refresh"],
            }
        )

    if matched_alerts:
        coas.append(
            {
                "title": "Open risk review",
                "expected_impact": f"Aligns {title} with existing alert posture and mitigation workflows.",
                "risk": "Requires operator acknowledgement and ownership.",
                "dependencies": ["Alert triage queue"],
            }
        )

    if matched_narratives:
        coas.append(
            {
                "title": "Track narrative evolution",
                "expected_impact": "Detects whether the surrounding storyline is accelerating toward campaign-level behavior.",
                "risk": "Narrative clustering may lag fast-moving ground truth.",
                "dependencies": ["Narrative detector output", "Timeline view"],
            }
        )

    return coas[:4]


def _queue_item(
    situation: Dict[str, Any],
    matched_alerts: List[Dict[str, Any]],
    matched_narratives: List[Dict[str, Any]],
) -> Dict[str, Any]:
    priority = float(situation.get("priority_score") or 0.0)
    confidence = float(situation.get("confidence_score") or 0.0)
    alert_boost = 0.08 * max((_severity_rank(alert.get("severity")) for alert in matched_alerts), default=0)
    narrative_boost = 0.04 * max((_severity_rank(narrative.get("severity")) for narrative in matched_narratives), default=0)
    risk_score = round(min(priority * 0.65 + confidence * 0.25 + alert_boost + narrative_boost, 1.0), 3)

    return {
        "id": situation.get("id"),
        "title": situation.get("title"),
        "summary": situation.get("summary"),
        "event_type": situation.get("event_type"),
        "timestamp": situation.get("timestamp"),
        "priority_score": priority,
        "risk_score": risk_score,
        "confidence_score": confidence,
        "signal_count": int(situation.get("signal_count") or 0),
        "supporting_signals": list(situation.get("supporting_signals") or []),
        "source_family_count": len(situation.get("source_mix") or []),
        "actors": situation.get("actors") or [],
        "location": situation.get("location"),
        "sentiment": situation.get("sentiment"),
        "alerts": matched_alerts[:3],
        "narratives": matched_narratives[:3],
        "playbook": _recommend_playbook(situation, matched_alerts, matched_narratives),
        "courses_of_action": _courses_of_action(situation, matched_alerts, matched_narratives),
        "recommended_next_action": (
            "Escalate to analyst" if risk_score >= 0.72 else "Generate briefing" if risk_score >= 0.5 else "Continue monitoring"
        ),
    }


async def build_operations_overview(session: AsyncSession, limit: int = 8) -> Dict[str, Any]:
    situation_overview = await build_situation_overview(session, limit=max(limit, 12))

    alert_rows = (
        await session.execute(
            select(AlertModel)
            .where(AlertModel.resolved_at.is_(None))
            .order_by(desc(AlertModel.detected_at))
            .limit(60)
        )
    ).scalars().all()
    narrative_rows = (
        await session.execute(
            select(NarrativeModel)
            .where(NarrativeModel.status == "active")
            .order_by(desc(NarrativeModel.detected_at))
            .limit(60)
        )
    ).scalars().all()

    alerts = [_alert_to_dict(row) for row in alert_rows]
    narratives = [_narrative_to_dict(row) for row in narrative_rows]

    items: List[Dict[str, Any]] = []
    for situation in situation_overview.get("situations", [])[:limit]:
        matched_alerts = _match_alerts(situation, alerts)
        matched_narratives = _match_narratives(situation, narratives)
        items.append(_queue_item(situation, matched_alerts, matched_narratives))

    items.sort(key=lambda item: (item["risk_score"], item["priority_score"]), reverse=True)

    threat_posture = "steady"
    if any(item["risk_score"] >= 0.82 for item in items):
        threat_posture = "critical"
    elif any(item["risk_score"] >= 0.64 for item in items):
        threat_posture = "elevated"

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "threat_posture": threat_posture,
        "total": len(items),
        "items": items,
    }
