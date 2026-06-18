"""
api/routers/playbooks.py
────────────────────────
HTTP surface for the Vision-I playbook engine.

Endpoints:
    GET  /api/playbooks                   list available playbooks
    GET  /api/playbooks/{id}              fetch a single playbook
    POST /api/playbooks/match             return playbooks whose trigger matches a context dict
    POST /api/playbooks/{id}/execute      execute a playbook against a context dict
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from playbook import PlaybookEngine

router = APIRouter(prefix="/api/playbooks", tags=["playbooks"])

# ── Response schemas ───────────────────────────────────────────────────────

class PlaybookListResponse(BaseModel):
    total: int = 0
    playbooks: List[Any] = Field(default_factory=list)

class PlaybookItemSchema(BaseModel):
    model_config = ConfigDict(extra="allow")

class PlaybookMatchResponse(BaseModel):
    total: int = 0
    playbooks: List[Any] = Field(default_factory=list)

class PlaybookExecuteResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

_engine = PlaybookEngine()


@router.get("", response_model=PlaybookListResponse)
def list_playbooks() -> Dict[str, Any]:
    items = _engine.list()
    return {"total": len(items), "playbooks": items}


@router.get("/{playbook_id}", response_model=PlaybookItemSchema)
def get_playbook(playbook_id: str) -> Dict[str, Any]:
    pb = _engine.get(playbook_id)
    if pb is None:
        raise HTTPException(status_code=404, detail="playbook not found")
    return pb.to_dict()


@router.post("/match", response_model=PlaybookMatchResponse)
def match(context: Dict[str, Any]) -> Dict[str, Any]:
    matches = _engine.matches(context or {})
    return {"total": len(matches), "playbooks": [p.to_dict() for p in matches]}


@router.post("/{playbook_id}/execute", response_model=PlaybookExecuteResponse)
async def execute(playbook_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
    pb = _engine.get(playbook_id)
    if pb is None:
        raise HTTPException(status_code=404, detail="playbook not found")
    return await _engine.execute(playbook_id, context or {})
