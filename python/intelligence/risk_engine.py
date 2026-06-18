"""
intelligence/risk_engine.py
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Unified risk scoring for Vision-I events.

risk_score = Î±Â·military + Î²Â·sentiment + Î³Â·narrative + Î´Â·anomaly + ÎµÂ·influence

Scores are 0.0â€“1.0 and stored on EventModel.risk_score so every downstream
consumer (escalation, situations, copilot) reads one canonical value.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vision_i.intelligence.risk_engine")

_W_MILITARY   = 0.30
_W_SENTIMENT  = 0.25
_W_NARRATIVE  = 0.20
_W_ANOMALY    = 0.15
_W_INFLUENCE  = 0.10

# Military / conflict keyword sets
_MIL_HIGH = {
    "strike", "attack", "missile", "bomb", "explosion", "naval", "warship",
    "troops", "invasion", "blockade", "airstrike", "drone", "ammunition",
    "casualties", "killed", "wounded", "escalation", "hostilities",
}
_MIL_MED = {
    "military", "defense", "conflict", "tension", "sanction", "embargo",
    "seized", "intercept", "provocation", "deploy", "mobilize", "exercise",
    "vessel", "submarine", "aircraft carrier", "ais", "transponder",
}
_DISASTER = {
    "earthquake", "tsunami", "flood", "hurricane", "typhoon", "wildfire",
    "eruption", "tornado", "collapse", "contamination", "radiation",
}
_INFOSEC = {
    "cyber", "hack", "breach", "ransomware", "malware", "outage", "blackout",
    "data leak", "vulnerability", "exploit",
}


def _keyword_military_score(text: str) -> float:
    """0â€“1 based on military/conflict keyword density."""
    t = text.lower()
    high = sum(1 for kw in _MIL_HIGH if kw in t)
    med  = sum(1 for kw in _MIL_MED  if kw in t)
    dis  = sum(1 for kw in _DISASTER if kw in t)
    sec  = sum(1 for kw in _INFOSEC  if kw in t)
    raw  = high * 0.25 + med * 0.12 + dis * 0.15 + sec * 0.10
    return min(raw, 1.0)


def _sentiment_risk(event: Dict[str, Any]) -> float:
    """Convert sentiment score â†’ risk contribution (negative = higher risk)."""
    sent = (event.get("sentiment") or {}).get("score")
    if sent is None:
        return 0.3  # unknown = moderate risk
    # score of 0.0 (very negative) â†’ risk 1.0; 0.5 (neutral) â†’ 0.3; 1.0 (positive) â†’ 0.0
    if sent <= 0.3:
        return 1.0
    if sent <= 0.5:
        return 0.5
    if sent <= 0.7:
        return 0.2
    return 0.05


def _anomaly_flag(event: Dict[str, Any]) -> float:
    """1.0 if event is flagged as an anomaly, else 0.0."""
    tags      = {str(t).lower() for t in (event.get("tags") or [])}
    evt_type  = (event.get("event_type") or "").lower()
    reasoning = (event.get("reasoning") or "").lower()
    if "anomaly" in tags or "anomaly" in evt_type or "anomaly" in reasoning:
        return 1.0
    if "alert" in tags or "critical" in tags:
        return 0.7
    return 0.0


def _narrative_signal(event: Dict[str, Any]) -> float:
    """Narrative strength signal from event extras or supporting_signals count."""
    extras     = event.get("extras") or {}
    trigger    = extras.get("trigger_type", "")
    n_signals  = event.get("signal_count") or len(event.get("supporting_signals") or [])
    base       = 0.0
    if trigger == "auto_social":
        base = 0.5
    if n_signals >= 5:
        base = max(base, 0.7)
    elif n_signals >= 2:
        base = max(base, 0.4)
    return base


def _actor_importance(event: Dict[str, Any]) -> float:
    """Higher risk if many named actors (suggests wider geopolitical scope)."""
    actors = event.get("actors") or []
    if len(actors) >= 5:
        return 1.0
    if len(actors) >= 3:
        return 0.6
    if len(actors) >= 1:
        return 0.3
    return 0.0


def compute_risk_score(event: Dict[str, Any]) -> float:
    """
    Canonical risk scorer. Returns float 0.0â€“1.0.

    Factors:
      military   â€” keyword presence (attack/strike/naval/etc.)
      sentiment  â€” negativity of sentiment score
      narrative  â€” social amplification / signal count
      anomaly    â€” explicit anomaly tag
      influence  â€” event's pre-scored influence_score
    """
    text = " ".join(filter(None, [
        event.get("title", ""),
        event.get("description", ""),
        event.get("body", ""),
    ]))

    military  = _keyword_military_score(text)
    sentiment = _sentiment_risk(event)
    narrative = _narrative_signal(event)
    anomaly   = _anomaly_flag(event)
    influence = float(event.get("influence_score") or 0.0)

    score = (
        _W_MILITARY  * military  +
        _W_SENTIMENT * sentiment +
        _W_NARRATIVE * narrative +
        _W_ANOMALY   * anomaly   +
        _W_INFLUENCE * influence
    )
    return round(min(max(score, 0.0), 1.0), 4)


def severity_from_score(score: float) -> str:
    """Map numeric risk score to severity label."""
    if score >= 0.75:
        return "critical"
    if score >= 0.50:
        return "high"
    if score >= 0.25:
        return "medium"
    return "low"


def score_events_batch(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Add `risk_score` and `risk_severity` keys to each event dict in-place.
    Returns the same list for chaining.
    """
    for ev in events:
        score = compute_risk_score(ev)
        ev["risk_score"]    = score
        ev["risk_severity"] = severity_from_score(score)
    return events


async def persist_risk_scores(event_ids_scores: List[tuple]) -> int:
    """
    Persist (event_id, risk_score) pairs to PostgreSQL.
    Called from pipeline_worker after batch scoring.
    Returns number of rows updated.
    """
    if not event_ids_scores:
        return 0
    try:
        from sqlalchemy import update
        from storage.database import get_session, EventModel

        async with get_session() as session:
            updated = 0
            for event_id, score in event_ids_scores:
                result = await session.execute(
                    update(EventModel)
                    .where(EventModel.event_id == event_id)
                    .values(risk_score=score)
                )
                updated += result.rowcount
            return updated
    except Exception as exc:
        logger.error("persist_risk_scores failed: %s", exc)
        return 0

