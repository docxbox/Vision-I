"""
storage/situation_repo.py
──────────────────────────
CRUD helpers for SituationModel.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from storage.database import SituationModel

logger = logging.getLogger("vision_i.storage.situation_repo")


async def upsert_situation(
    session: AsyncSession,
    situation: Dict[str, Any],
) -> SituationModel:
    """Insert or update a situation (upsert on situation_id). Returns the model row."""
    sid = situation["situation_id"]
    existing = (await session.execute(
        select(SituationModel).where(SituationModel.situation_id == sid)
    )).scalar_one_or_none()

    if existing:
        existing.title       = situation.get("title", existing.title)
        existing.description = situation.get("description")
        existing.event_ids   = situation.get("event_ids", [])
        existing.actor_ids   = situation.get("actor_ids", [])
        existing.risk_score  = situation.get("risk_score", existing.risk_score)
        existing.severity    = situation.get("severity", existing.severity)
        existing.region      = situation.get("region", existing.region)
        existing.event_count = situation.get("event_count", existing.event_count)
        existing.status      = situation.get("status", existing.status)
        existing.updated_at  = datetime.now(timezone.utc)
        existing.meta        = situation.get("meta", {})
        return existing

    row = SituationModel(
        situation_id = sid,
        title        = situation.get("title", ""),
        description  = situation.get("description"),
        event_ids    = situation.get("event_ids", []),
        actor_ids    = situation.get("actor_ids", []),
        risk_score   = situation.get("risk_score", 0.0),
        severity     = situation.get("severity", "low"),
        region       = situation.get("region", "GLOBAL"),
        event_count  = situation.get("event_count", 0),
        status       = situation.get("status", "active"),
        meta         = situation.get("meta", {}),
    )
    session.add(row)
    return row


async def sync_active_situations(
    session: AsyncSession,
    active_ids: List[str],
) -> int:
    """
    Mark previously active situations as stale when they are no longer present
    in the latest detection pass.
    """
    wanted = {sid for sid in active_ids if sid}
    rows = (
        await session.execute(
            select(SituationModel).where(SituationModel.status == "active")
        )
    ).scalars().all()

    updated = 0
    now = datetime.now(timezone.utc)
    for row in rows:
        if row.situation_id in wanted:
            continue
        row.status = "stale"
        row.updated_at = now
        updated += 1
    return updated


async def list_situations(
    session: AsyncSession,
    limit: int = 50,
    severity: Optional[str] = None,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return situations ordered by detected_at desc."""
    q = select(SituationModel).order_by(desc(SituationModel.detected_at)).limit(limit)
    if severity:
        q = q.where(SituationModel.severity == severity)
    if status:
        q = q.where(SituationModel.status == status)
    rows = (await session.execute(q)).scalars().all()
    return [_to_dict(r) for r in rows]


async def list_feed_situations(
    session: AsyncSession,
    limit: int = 12,
) -> List[Dict[str, Any]]:
    """
    Return the strongest active situations for case-based feed grouping.

    We bias toward higher risk first, then newer detections, and let the router
    apply additional quality gates using the situation metadata.
    """
    rows = (
        await session.execute(
            select(SituationModel)
            .where(SituationModel.status == "active")
            .order_by(desc(SituationModel.risk_score), desc(SituationModel.detected_at))
            .limit(limit)
        )
    ).scalars().all()
    return [_to_dict(r) for r in rows]


async def get_situation(
    session: AsyncSession,
    situation_id: str,
) -> Optional[Dict[str, Any]]:
    row = (await session.execute(
        select(SituationModel).where(SituationModel.situation_id == situation_id)
    )).scalar_one_or_none()
    return _to_dict(row) if row else None


async def get_situation_membership_map(
    session: AsyncSession,
    event_ids: List[str],
    limit: int = 200,
) -> Dict[str, Dict[str, Any]]:
    """
    Return the highest-risk active situation for each requested event_id.

    Situation event membership currently lives in a JSONB array. For feed-sized
    windows it is cheaper and simpler to pull a bounded set of active situations
    and fan them into a lookup map than to do N JSON containment probes.
    """
    wanted = {e for e in event_ids if e}
    if not wanted:
        return {}

    rows = (
        await session.execute(
            select(SituationModel)
            .where(SituationModel.status == "active")
            .order_by(desc(SituationModel.risk_score), desc(SituationModel.detected_at))
            .limit(limit)
        )
    ).scalars().all()

    by_event: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        payload = _to_dict(row)
        for event_id in payload.get("event_ids") or []:
            if event_id not in wanted or event_id in by_event:
                continue
            by_event[event_id] = {
                "situation_id": payload.get("situation_id"),
                "title": payload.get("title"),
                "severity": payload.get("severity"),
                "risk_score": payload.get("risk_score"),
                "status": payload.get("status"),
                "region": payload.get("region"),
                "event_count": payload.get("event_count"),
                "subcase_id": (payload.get("meta") or {}).get("subcase_id"),
                "parent_situation_id": (payload.get("meta") or {}).get("parent_situation_id"),
            }
    return by_event


async def get_situations_by_ids(
    session: AsyncSession,
    situation_ids: List[str],
) -> List[Dict[str, Any]]:
    ids = [sid for sid in situation_ids if sid]
    if not ids:
        return []
    rows = (
        await session.execute(
            select(SituationModel).where(SituationModel.situation_id.in_(ids))
        )
    ).scalars().all()
    return [_to_dict(r) for r in rows]


def _to_dict(row: SituationModel) -> Dict[str, Any]:
    return {
        "situation_id": row.situation_id,
        "title":        row.title,
        "description":  row.description,
        "event_ids":    row.event_ids or [],
        "actor_ids":    row.actor_ids or [],
        "risk_score":   row.risk_score,
        "severity":     row.severity,
        "region":       row.region,
        "event_count":  row.event_count,
        "status":       row.status,
        "detected_at":  row.detected_at.isoformat() if row.detected_at else None,
        "updated_at":   row.updated_at.isoformat() if row.updated_at else None,
        "meta":         row.meta or {},
    }
