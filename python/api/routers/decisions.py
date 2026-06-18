"""
api/routers/decisions.py
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Analyst decision record endpoints.

  POST /decisions          â€” log an executed COA
  GET  /decisions          â€” list decisions (newest first)
  GET  /decisions/{id}     â€” get a single decision
"""

import logging
from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from typing import Any, Dict, List

logger = logging.getLogger("vision_i.api.decisions")
router = APIRouter(tags=["decisions"])

# ── Response schemas ───────────────────────────────────────────────────────

class DecisionSchema(BaseModel):
    model_config = ConfigDict(extra="allow")
    decision_id: Optional[str] = None
    event_id: Optional[str] = None
    coa_text: Optional[str] = None
    analyst: Optional[str] = None
    status: Optional[str] = None
    outcome: Optional[str] = None

class DecisionListResponse(BaseModel):
    total: int = 0
    limit: int = 50
    decisions: List[Any] = Field(default_factory=list)


class CreateDecisionRequest(BaseModel):
    event_id:  str
    coa_index: int
    coa_text:  str
    analyst:   str = "system"
    status:    str = "approved"
    rationale: Optional[str] = None


class RecordOutcomeRequest(BaseModel):
    outcome:       str  # effective | ineffective | inconclusive
    outcome_notes: Optional[str] = None


@router.post("", status_code=201, summary="Log an executed course of action", response_model=DecisionSchema)
async def create_decision(body: CreateDecisionRequest, request: Request):
    """Persist a decision record when an analyst executes a COA."""
    if not request.app.state.db_available:
        return JSONResponse({"error": "Database unavailable"}, status_code=503)

    try:
        from storage.database import get_session
        from storage.decision_repo import create_decision as repo_create

        async with get_session() as session:
            decision = await repo_create(
                session,
                event_id=body.event_id,
                coa_index=body.coa_index,
                coa_text=body.coa_text,
                analyst=body.analyst,
                status=body.status,
                rationale=body.rationale,
            )

        logger.info(
            "Decision created: event=%s coa=%d analyst=%s",
            body.event_id, body.coa_index, body.analyst,
        )
        return decision

    except Exception as exc:
        logger.error("Create decision failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("", summary="List decision records", response_model=DecisionListResponse)
async def list_decisions(
    request: Request,
    limit:   int = Query(50, ge=1, le=200),
):
    """Return the most recent decision records, newest first."""
    if not request.app.state.db_available:
        return {"total": 0, "decisions": []}

    try:
        from storage.database import get_session
        from storage.decision_repo import list_decisions as repo_list

        async with get_session() as session:
            decisions = await repo_list(session, limit=limit)

        return {"total": len(decisions), "limit": limit, "decisions": decisions}

    except Exception as exc:
        logger.error("List decisions failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/{decision_id}", summary="Get a single decision record", response_model=DecisionSchema)
async def get_decision(decision_id: str, request: Request):
    """Retrieve a decision record by its UUID."""
    if not request.app.state.db_available:
        return JSONResponse({"error": "Database unavailable"}, status_code=503)

    try:
        from storage.database import get_session
        from storage.decision_repo import get_decision as repo_get

        async with get_session() as session:
            decision = await repo_get(session, decision_id)

        if decision is None:
            return JSONResponse({"error": "Decision not found"}, status_code=404)

        return decision

    except Exception as exc:
        logger.error("Get decision %s failed: %s", decision_id, exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/{decision_id}/outcome", summary="Record outcome of an executed decision (feedback loop)", response_model=DecisionSchema)
async def record_outcome(decision_id: str, body: RecordOutcomeRequest, request: Request):
    """
    Capture the outcome of an executed course of action.

    Valid outcomes: effective | ineffective | inconclusive

    This feeds into the Decision OS learning loop â€” outcomes are used by the
    Copilot to rank recommendations for similar future events.
    """
    if not request.app.state.db_available:
        return JSONResponse({"error": "Database unavailable"}, status_code=503)

    valid = {"effective", "ineffective", "inconclusive"}
    if body.outcome not in valid:
        return JSONResponse({"error": f"outcome must be one of: {', '.join(valid)}"}, status_code=422)

    try:
        from storage.database import get_session
        from storage.decision_repo import update_decision_outcome

        async with get_session() as session:
            decision = await update_decision_outcome(
                session,
                decision_id=decision_id,
                outcome=body.outcome,
                outcome_notes=body.outcome_notes,
            )

        if decision is None:
            return JSONResponse({"error": "Decision not found"}, status_code=404)

        logger.info("Outcome recorded for decision %s: %s", decision_id, body.outcome)
        # Fire-and-forget: never let Neo4j failure propagate to the caller
        import asyncio

        async def _feedback_loop() -> None:
            try:
                graph = request.app.state.graph
                if not (graph and graph.available and decision.get("event_id")):
                    return
                from storage.database import get_session, EventModel
                from sqlalchemy import select

                async with get_session() as s:
                    ev_row = (await s.execute(
                        select(EventModel).where(EventModel.event_id == decision["event_id"])
                    )).scalar_one_or_none()

                if ev_row and ev_row.actors:
                    loop = asyncio.get_running_loop()
                    updates = []
                    for actor in (ev_row.actors or []):
                        actor_name = (actor.get("name") or "").strip()
                        if actor_name:
                            updates.append(loop.run_in_executor(
                                None,
                                graph.update_actor_risk_weight,
                                actor_name,
                                body.outcome,
                            ))
                    if updates:
                        await asyncio.wait_for(asyncio.gather(*updates, return_exceptions=True), timeout=8.0)
                    logger.info(
                        "Feedback loop: updated risk weights for %d actors (outcome=%s)",
                        len(ev_row.actors), body.outcome,
                    )
            except Exception as fb_exc:
                logger.warning("Feedback loop (graph weight update) failed: %s", fb_exc)

        asyncio.create_task(_feedback_loop())

        return decision

    except Exception as exc:
        logger.error("Record outcome failed for %s: %s", decision_id, exc)
        return JSONResponse({"error": str(exc)}, status_code=500)

