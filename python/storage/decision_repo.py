"""
storage/decision_repo.py
────────────────────────
CRUD helpers for analyst decision records.

All functions accept a SQLAlchemy AsyncSession and return plain dicts
to keep the router layer free of ORM imports.
"""

import logging
from datetime import datetime, timezone
from sqlalchemy import select, desc, update
from sqlalchemy.ext.asyncio import AsyncSession

from storage.database import DecisionModel, EventModel

logger = logging.getLogger("vision_i.storage.decision_repo")


async def create_decision(
    session: AsyncSession,
    event_id: str,
    coa_index: int,
    coa_text: str,
    analyst: str = "system",
    status: str = "approved",
    rationale: str | None = None,
) -> dict:
    """Insert a new decision record and return it as a dict."""
    decision = DecisionModel(
        event_id=event_id,
        coa_index=coa_index,
        coa_text=coa_text,
        analyst=analyst,
        status=status,
        rationale=rationale,
    )
    session.add(decision)
    await session.flush()
    await session.refresh(decision)
    return _to_dict(decision)


async def list_decisions(session: AsyncSession, limit: int = 50) -> list[dict]:
    """Return the most recent `limit` decision records, newest first."""
    result = await session.execute(
        select(DecisionModel)
        .order_by(desc(DecisionModel.created_at))
        .limit(limit)
    )
    return [_to_dict(row) for row in result.scalars().all()]


async def get_decision(session: AsyncSession, decision_id: str) -> dict | None:
    """Return a single decision by UUID, or None if not found."""
    result = await session.execute(
        select(DecisionModel).where(DecisionModel.id == decision_id)
    )
    row = result.scalar_one_or_none()
    return _to_dict(row) if row else None


async def update_decision_outcome(
    session: AsyncSession,
    decision_id: str,
    outcome: str,
    outcome_notes: str | None = None,
) -> dict | None:
    """Record the outcome of an executed decision (effective/ineffective/inconclusive)."""
    result = await session.execute(
        select(DecisionModel).where(DecisionModel.id == decision_id)
    )
    decision = result.scalar_one_or_none()
    if not decision:
        return None

    decision.outcome = outcome
    decision.outcome_notes = outcome_notes
    await session.flush()
    await session.refresh(decision)
    return _to_dict(decision)


async def find_similar_decisions(
    session: AsyncSession,
    event_type: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """
    Find past decisions for events of the same type.
    Used by the copilot to surface historical precedents.
    """
    # Join decisions with events to filter by event_type
    stmt = (
        select(DecisionModel, EventModel.event_type)
        .join(EventModel, DecisionModel.event_id == EventModel.event_id, isouter=True)
        .order_by(desc(DecisionModel.created_at))
    )
    if event_type:
        stmt = stmt.where(
            (EventModel.event_type == event_type) | (EventModel.event_type.is_(None))
        )
    stmt = stmt.limit(limit)

    result = await session.execute(stmt)
    rows = result.all()
    decisions = []
    for row in rows:
        d = _to_dict(row[0])
        d["matched_event_type"] = row[1]
        decisions.append(d)
    return decisions


def _to_dict(d: DecisionModel) -> dict:
    return {
        "id": str(d.id),
        "event_id": d.event_id,
        "coa_index": d.coa_index,
        "coa_text": d.coa_text,
        "analyst": d.analyst,
        "status": d.status,
        "rationale": d.rationale,
        "outcome": getattr(d, "outcome", None),
        "outcome_notes": getattr(d, "outcome_notes", None),
        "created_at": d.created_at.isoformat() if isinstance(d.created_at, datetime) else str(d.created_at),
    }
