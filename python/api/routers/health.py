"""
api/routers/health.py
──────────────────────
GET /health — liveness probe. No auth required.

Returns overall status, version, DB availability, and a timestamp.
The .NET API calls this on a timer to decide if Python is reachable.
"""

import asyncio

from fastapi import APIRouter, Request
from core.utils import utcnow_iso
from config.settings import settings

router = APIRouter(tags=["Health"])


@router.get("/config/classification", summary="Classification banner config")
async def classification():
    return {
        "banner": settings.classification_banner,
        "color":  settings.classification_color,
    }


@router.get("/health", summary="Liveness probe")
async def health(request: Request):
    result = {
        "status":          "ok",
        "version":         request.app.version,
        "timestamp":       utcnow_iso(),
        "db_available":    getattr(request.app.state, "db_available", False),
        "neo4j_available": getattr(request.app.state, "graph", None) and
                           request.app.state.graph.available,
        "classification": {
            "banner": settings.classification_banner,
            "color":  settings.classification_color,
        },
    }

    # Agent swarm status
    swarm = getattr(request.app.state, "swarm", None)
    if swarm:
        missions = await swarm.list_missions(limit=100)
        result["agent_swarm"] = {
            "agents": len(swarm.list_agents()),
            "active_missions": len([m for m in missions if m.get("status") == "running"]),
        }

    # LLM provider status
    llm = getattr(request.app.state, "llm", None)
    if llm:
        result["llm"] = {
            "provider": llm.provider or "none",
            "model": llm.model or "n/a",
            "available": llm.available,
            "runtime_source": getattr(llm, "runtime_source", "environment"),
        }

    return result


@router.get("/ready", summary="Readiness probe")
async def ready(request: Request):
    """
    Readiness is stricter than liveness:
    - If a dependency is configured, we require it to be reachable quickly.
    - If it's not configured, we don't block readiness on it.
    """
    checks = {
        "postgres": {"configured": settings.db_available, "ok": None},
        "redis": {"configured": bool(getattr(settings, "redis_url", "")), "ok": None},
        "neo4j": {"configured": settings.neo4j_available, "ok": None},
    }

    # PostgreSQL quick check
    if checks["postgres"]["configured"]:
        try:
            from storage.database import get_session
            from sqlalchemy import text
            async with get_session() as session:
                await asyncio.wait_for(session.execute(text("SELECT 1")), timeout=2.0)
            checks["postgres"]["ok"] = True
        except Exception:
            checks["postgres"]["ok"] = False
    else:
        checks["postgres"]["ok"] = True

    # Redis quick check
    if checks["redis"]["configured"]:
        bus = getattr(request.app.state, "event_bus", None)
        if bus is None:
            checks["redis"]["ok"] = False
        else:
            try:
                await asyncio.wait_for(bus.redis.ping(), timeout=1.5)
                checks["redis"]["ok"] = True
            except Exception:
                checks["redis"]["ok"] = False
    else:
        checks["redis"]["ok"] = True

    # Neo4j quick check
    if checks["neo4j"]["configured"]:
        graph = getattr(request.app.state, "graph", None)
        checks["neo4j"]["ok"] = bool(graph and graph.available)
    else:
        checks["neo4j"]["ok"] = True

    ok = all(v["ok"] for v in checks.values())
    return {
        "ready": ok,
        "timestamp": utcnow_iso(),
        "version": request.app.version,
        "checks": checks,
    }
