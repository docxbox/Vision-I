"""
api/routers/agents.py
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Agent Swarm endpoints â€” list agents, launch missions, view logs.

All endpoints are internal (behind X-Internal-Key middleware from main.py).

IMPORTANT: Fixed routes must appear before parameterised routes to avoid
FastAPI matching e.g. "missions" as an {agent_id}.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from typing import Any, List, Optional

logger = logging.getLogger("vision_i.api.agents")
router = APIRouter(tags=["agents"])

# ── Response schemas ───────────────────────────────────────────────────────

class AgentListResponse(BaseModel):
    agents: List[Any] = Field(default_factory=list)
    total: int = 0

class MissionStartResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

class MissionListResponse(BaseModel):
    missions: List[Any] = Field(default_factory=list)
    total: int = 0

class MissionDetailSchema(BaseModel):
    model_config = ConfigDict(extra="allow")

class AgentLogResponse(BaseModel):
    entries: List[Any] = Field(default_factory=list)
    total: int = 0

class LlmStatusResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    provider: Optional[str] = None
    available: bool = False

class AgentDetailSchema(BaseModel):
    model_config = ConfigDict(extra="allow")


class MissionRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500, description="Intelligence query")
    sources: Optional[list[str]] = Field(None, description="Optional source filter")

def _swarm(request: Request):
    swarm = getattr(request.app.state, "swarm", None)
    if swarm is None:
        raise HTTPException(503, "Agent swarm not initialised")
    return swarm

@router.get("", response_model=AgentListResponse)
async def list_agents(request: Request):
    """List all agents with their current status."""
    swarm = _swarm(request)
    agents = swarm.list_agents()
    return {"agents": agents, "total": len(agents)}


@router.post("/mission", status_code=202, response_model=MissionStartResponse)
async def start_mission(body: MissionRequest, request: Request):
    """Start a new intelligence mission. Returns immediately with a mission_id."""
    swarm = _swarm(request)
    result = await swarm.start_mission(query=body.query, sources=body.sources)
    return result


@router.get("/missions", response_model=MissionListResponse)
async def list_missions(request: Request, limit: int = 20):
    """List recent missions."""
    swarm = _swarm(request)
    missions = await swarm.list_missions(limit=limit)
    return {"missions": missions, "total": len(missions)}


@router.get("/mission/{mission_id}", response_model=MissionDetailSchema)
async def get_mission(mission_id: str, request: Request):
    """Get mission status and results."""
    swarm = _swarm(request)
    mission = await swarm.get_mission(mission_id)
    if mission is None:
        raise HTTPException(404, f"Mission '{mission_id}' not found")
    return mission


@router.get("/log", response_model=AgentLogResponse)
async def get_log(
    request: Request,
    mission_id: Optional[str] = None,
    limit: int = 100,
):
    """Get mission log entries. Optionally filter by mission_id."""
    swarm = _swarm(request)
    entries = await swarm.get_log(mission_id=mission_id, limit=limit)
    return {"entries": entries, "total": len(entries)}


@router.get("/llm-status", response_model=LlmStatusResponse)
async def llm_status(request: Request):
    """Get LLM provider status."""
    llm = getattr(request.app.state, "llm", None)
    if llm is None:
        return {"provider": None, "available": False}
    return {
        "provider": llm.provider,
        "model": llm.model,
        "available": llm.available,
    }

@router.get("/{agent_id}", response_model=AgentDetailSchema)
async def get_agent(agent_id: str, request: Request):
    """Get a single agent's detail."""
    swarm = _swarm(request)
    agent = swarm.get_agent(agent_id)
    if agent is None:
        raise HTTPException(404, f"Agent '{agent_id}' not found")
    return agent

