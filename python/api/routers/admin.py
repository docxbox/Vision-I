"""
api/routers/admin.py
â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
Admin endpoints  --  called by the .NET Web API's admin panel.

GET    /admin/health               --  full system health check
GET    /admin/queries              --  list tracked ingestion queries
POST   /admin/queries              --  add a new tracked query
DELETE /admin/queries/{id}         --  deactivate a tracked query
GET    /admin/jobs                 --  recent ingest job history
GET    /admin/stats                --  event counts by source and type
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from api.routers.ingest import _JOB_PREFIX
from api.routers.ingest import _jobs
from core.utils import utcnow_iso

logger = logging.getLogger("vision_i.api.admin")
router = APIRouter(tags=["Admin"])

# ── Response schemas ───────────────────────────────────────────────────────

class AdminHealthResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    status: str = "ok"

class AdminGenericResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

class AdminQueriesResponse(BaseModel):
    queries: List[Any] = Field(default_factory=list)

class AdminQueryMutationResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

class AdminJobsResponse(BaseModel):
    total: int = 0
    jobs: List[Any] = Field(default_factory=list)

class AdminStatsResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    total_events: int = 0

class AdminDlqResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    events: List[Any] = Field(default_factory=list)
    size: int = 0


class NewQuery(BaseModel):
    query:      str = Field(..., max_length=500)
    created_by: Optional[str] = None


class RuntimeLlmConfig(BaseModel):
    provider: str = Field(..., min_length=3, max_length=32)
    api_key: str = ""
    model: Optional[str] = Field(default=None, max_length=1000)
    base_url: Optional[str] = Field(default=None, max_length=500)
    enabled: bool = True

class LlmCompleteRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    system: Optional[str] = None
    max_tokens: int = Field(default=1024, ge=16, le=8192)
    temperature: float = Field(default=0.2, ge=0.0, le=1.0)

@router.get("/health", summary="Full system health  --  sources, DB, scheduler", response_model=AdminHealthResponse)
async def system_health(request: Request):
    """
    Comprehensive health report used by the admin dashboard.
    Includes: source statuses, DB connectivity, scheduler job status,
    memory job store size.
    """
    orchestrator = request.app.state.orchestrator
    scheduler    = request.app.state.scheduler

    # Try cached health first (source checks are expensive  --  they ping every extractor)
    event_bus = request.app.state.event_bus
    source_checks = None
    if event_bus:
        try:
            source_checks = await event_bus.cache_get("cache:source_health")
        except Exception:
            pass

    if not source_checks:
        import asyncio
        loop = asyncio.get_running_loop()
        try:
            source_checks = await asyncio.wait_for(
                loop.run_in_executor(None, orchestrator.health),
                timeout=10.0,
            )
        except asyncio.TimeoutError:
            logger.warning("orchestrator.health() timed out after 10 s  --  returning partial data")
            source_checks = {}
        # Cache for 15 min on success, 30 s on timeout (so we don't hammer extractors)
        if event_bus:
            try:
                ttl = 900 if source_checks else 30
                await event_bus.cache_set("cache:source_health", source_checks or {}, ttl_seconds=ttl)
            except Exception:
                pass

    overall = "ok" if all(v.get("status") == "ok" for v in source_checks.values()) else "degraded"

    scheduler_info = None
    if scheduler:
        jobs_info = []
        for job in scheduler.get_jobs():
            jobs_info.append({
                "id":       job.id,
                "name":     job.name,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            })
        scheduler_info = {"running": True, "jobs": jobs_info}
    else:
        scheduler_info = {"running": False, "reason": "no DB configured or start failed"}

    # LLM provider info
    llm = getattr(request.app.state, "llm", None)
    llm_info = None
    if llm:
        llm_info = {
            "provider":  llm.provider or "none",
            "model":     llm.model or "n/a",
            "available": llm.available,
        }

    # Agent swarm info
    swarm = getattr(request.app.state, "swarm", None)
    swarm_info = None
    if swarm:
        agents = swarm.list_agents()
        missions = await swarm.list_missions(limit=100)
        swarm_info = {
            "agents":          len(agents),
            "agent_statuses":  {a["agent_id"]: a["status"] for a in agents},
            "total_missions":  len(missions),
            "active_missions": len([m for m in missions if m.get("status") == "running"]),
        }

    # Memory jobs count from Redis
    memory_jobs_count = 0
    if event_bus:
        try:
            count = 0
            async for _ in event_bus.redis.scan_iter(f"{_JOB_PREFIX}*"):
                count += 1
            memory_jobs_count = count
        except Exception:
            pass

    return {
        "status":          overall,
        "timestamp":       utcnow_iso(),
        "version":         request.app.version,
        "db_available":    request.app.state.db_available,
        "neo4j_available": request.app.state.graph.available,
        "sources":         source_checks,
        "scheduler":       scheduler_info,
        "memory_jobs":     memory_jobs_count,
        "llm":             llm_info,
        "swarm":           swarm_info,
    }


@router.get("/pipeline-topology", summary="Pipeline topology and service status", response_model=AdminGenericResponse)
async def pipeline_topology(request: Request):
    return {
        "generated_at": utcnow_iso(),
        "services": {
            "event_bus": bool(getattr(request.app.state, "event_bus", None)),
            "scheduler": bool(getattr(request.app.state, "scheduler", None)),
            "embedder": bool(getattr(request.app.state, "embedder", None)),
            "neo4j": bool(getattr(request.app.state, "graph", None) and request.app.state.graph.available),
            "postgres": bool(getattr(request.app.state, "db_available", False)),
        },
    }


@router.get("/llm/runtime", summary="Current Python runtime LLM config", response_model=AdminGenericResponse)
async def get_runtime_llm(request: Request):
    llm = getattr(request.app.state, "llm", None)
    if not llm:
        return {"provider": "none", "available": False, "supported_providers": []}
    summary = llm.runtime_summary()
    summary["supported_providers"] = llm.supported_catalog()
    return summary


@router.post("/llm/runtime", summary="Apply runtime LLM config", response_model=AdminGenericResponse)
async def set_runtime_llm(body: RuntimeLlmConfig, request: Request):
    llm = getattr(request.app.state, "llm", None)
    if llm is None:
        raise HTTPException(status_code=503, detail="LLM service unavailable")

    if not body.enabled:
        llm.clear_runtime_config()
        return {"status": "disabled", **llm.runtime_summary()}

    try:
        provider_key = llm.normalize_provider(body.provider)
        llm.apply_runtime_config(
            provider=provider_key,
            api_key=body.api_key,
            model=body.model,
            base_url=body.base_url,
        )
        return {"status": "applied", **llm.runtime_summary()}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/llm/complete", summary="Run a completion via the active LLM provider")
async def llm_complete(body: LlmCompleteRequest, request: Request):
    """Single LLM gateway. The .NET tier routes all completions here so there is one
    provider implementation (this LLMProvider) and one config, not two."""
    llm = getattr(request.app.state, "llm", None)
    if llm is None or not getattr(llm, "available", False):
        return {"ok": False, "text": "", "model": "none", "provider": "none",
                "error": "LLM provider not configured"}
    try:
        text = await llm.complete(
            prompt=body.prompt,
            system=body.system or "You are an expert intelligence analyst for the Vision-I platform.",
            max_tokens=body.max_tokens,
            temperature=body.temperature,
        )
        return {"ok": True, "text": text,
                "model": getattr(llm, "last_model_used", None) or llm.model,
                "provider": llm.provider}
    except Exception as exc:
        logger.error("llm_complete failed: %s", exc)
        return {"ok": False, "text": "", "model": llm.model, "provider": llm.provider, "error": str(exc)}


@router.post("/llm/runtime/test", summary="Test runtime LLM config", response_model=AdminGenericResponse)
async def test_runtime_llm(body: RuntimeLlmConfig, request: Request):
    llm = getattr(request.app.state, "llm", None)
    if llm is None:
        raise HTTPException(status_code=503, detail="LLM service unavailable")

    previous = llm.runtime_summary()
    previous_key = getattr(llm, "api_key", None)
    try:
        if body.enabled:
            provider_key = llm.normalize_provider(body.provider)
            llm.apply_runtime_config(
                provider=provider_key,
                api_key=body.api_key,
                model=body.model,
                base_url=body.base_url,
            )
        result = await llm.test_connection()
        return {"status": "tested", "result": result}
    finally:
        if previous.get("runtime_source") == "runtime" and previous.get("provider") and previous_key:
            llm.apply_runtime_config(
                provider=previous["provider"],
                api_key=previous_key,
                model=previous.get("model"),
                base_url=previous.get("base_url"),
            )
        else:
            llm.clear_runtime_config()


@router.get("/queries", summary="List tracked ingestion queries", response_model=AdminQueriesResponse)
async def list_queries(request: Request):
    if request.app.state.db_available:
        try:
            from storage.database import get_session
            from storage.database import TrackedQueryModel
            async with get_session() as session:
                rows = (await session.execute(select(TrackedQueryModel).order_by(
                    TrackedQueryModel.created_at.desc()
                ))).scalars().all()
            return {
                "queries": [
                    {
                        "id":         r.id,
                        "query":      r.query,
                        "created_by": r.created_by,
                        "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
                        "is_active":  r.is_active,
                        "last_run":   r.last_run.isoformat() + "Z" if r.last_run else None,
                        "run_count":  r.run_count,
                    }
                    for r in rows
                ]
            }
        except Exception as exc:
            logger.warning("DB list_queries failed: %s", exc)

    return {"queries": [], "note": "PostgreSQL not available"}


@router.post("/queries", summary="Add a tracked query", status_code=201, response_model=AdminQueryMutationResponse)
async def add_query(body: NewQuery, request: Request):
    if not request.app.state.db_available:
        raise HTTPException(status_code=503, detail="PostgreSQL not available")

    try:
        from storage.database import get_session, TrackedQueryModel
        async with get_session() as session:
            existing = (await session.execute(
                select(TrackedQueryModel)
                .where(TrackedQueryModel.query == body.query)
            )).scalar_one_or_none()

            if existing:
                existing.is_active = True
                await session.flush()
                return {"id": existing.id, "query": existing.query, "status": "reactivated"}

            row = TrackedQueryModel(query=body.query, created_by=body.created_by, is_active=True)
            session.add(row)
            await session.flush()
            return {"id": row.id, "query": row.query, "status": "created"}
    except Exception as exc:
        logger.error("add_query failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/queries/{query_id}", summary="Deactivate a tracked query", response_model=AdminQueryMutationResponse)
async def delete_query(query_id: int, request: Request):
    if not request.app.state.db_available:
        raise HTTPException(status_code=503, detail="PostgreSQL not available")

    try:
        from storage.database import get_session, TrackedQueryModel
        async with get_session() as session:
            row = (await session.execute(
                select(TrackedQueryModel)
                .where(TrackedQueryModel.id == query_id)
            )).scalar_one_or_none()

            if not row:
                raise HTTPException(status_code=404, detail=f"Query {query_id} not found")

            row.is_active = False
            await session.flush()
            return {"id": query_id, "status": "deactivated"}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("delete_query failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/jobs", summary="Recent ingest job history", response_model=AdminJobsResponse)
async def list_jobs(
    request: Request,
    limit:   int = Query(20, ge=1, le=100),
    status:  Optional[str] = Query(None, description="pending | running | done | failed"),
):
    """Returns the most recent ingest jobs from the in-memory store and DB."""
    # In-memory jobs (always available)
    mem_jobs = [
        {k: v for k, v in job.items() if k != "events"}
        for job in sorted(_jobs.values(), key=lambda j: j.get("started_at", ""), reverse=True)
        if not status or job.get("status") == status
    ][:limit]

    # DB jobs
    db_jobs = []
    if request.app.state.db_available:
        try:
            from storage.database import get_session, IngestJobModel
            from sqlalchemy import select
            async with get_session() as session:
                q = select(IngestJobModel).order_by(IngestJobModel.started_at.desc()).limit(limit)
                if status:
                    q = q.where(IngestJobModel.status == status)
                rows = (await session.execute(q)).scalars().all()
            db_jobs = [
                {
                    "job_id":        r.job_id,
                    "query":         r.query,
                    "status":        r.status,
                    "started_at":    r.started_at.isoformat() + "Z" if r.started_at else None,
                    "finished_at":   r.finished_at.isoformat() + "Z" if r.finished_at else None,
                    "total_events":  r.total_events,
                    "source_counts": r.source_counts,
                    "source_errors": r.source_errors,
                    "error":         r.error,
                }
                for r in rows
            ]
        except Exception as exc:
            logger.warning("DB list_jobs failed: %s", exc)

    # Merge: DB jobs take precedence (deduplicate by job_id)
    seen    = {j["job_id"] for j in db_jobs}
    merged  = db_jobs + [j for j in mem_jobs if j["job_id"] not in seen]
    merged.sort(key=lambda j: j.get("started_at") or "", reverse=True)

    return {"total": len(merged), "jobs": merged[:limit]}


@router.get("/stats", summary="Event counts by source and type", response_model=AdminStatsResponse)
async def stats(request: Request):
    """Quick counts  --  useful for the admin dashboard summary cards."""
    # Try precomputed cache first
    event_bus = request.app.state.event_bus
    if event_bus:
        try:
            cached = await event_bus.cache_get("precomputed:dashboard_summary")
            if cached and isinstance(cached, dict):
                return {
                    "total_events": cached.get("total_events", 0),
                    "by_source":    cached.get("events_by_source", cached.get("by_source", {})),
                    "by_type":      cached.get("by_type", {}),
                    "generated_at": cached.get("generated_at", utcnow_iso()),
                    "_served_from": "precomputed",
                }
        except Exception:
            pass

    if request.app.state.db_available:
        try:
            from storage.database import get_session, EventModel
            from sqlalchemy import func, select
            async with get_session() as session:
                by_source = (await session.execute(
                    select(EventModel.source, func.count().label("count"))
                    .group_by(EventModel.source)
                    .order_by(func.count().desc())
                )).all()
                by_type = (await session.execute(
                    select(EventModel.event_type, func.count().label("count"))
                    .group_by(EventModel.event_type)
                    .order_by(func.count().desc())
                )).all()
                total = (await session.execute(
                    select(func.count()).select_from(EventModel)
                )).scalar_one()

            return {
                "total_events": total,
                "by_source":    {r.source: r.count for r in by_source},
                "by_type":      {r.event_type: r.count for r in by_type},
                "generated_at": utcnow_iso(),
            }
        except Exception as exc:
            logger.warning("DB stats failed: %s", exc)

    # Memory fallback
    by_source: Dict[str, int] = {}
    by_type:   Dict[str, int] = {}
    for job in _jobs.values():
        for e in job.get("events") or []:
            src = e.get("source", "unknown")
            typ = e.get("event_type", "unknown")
            by_source[src] = by_source.get(src, 0) + 1
            by_type[typ]   = by_type.get(typ,   0) + 1

    return {
        "total_events": sum(by_source.values()),
        "by_source":    by_source,
        "by_type":      by_type,
        "generated_at": utcnow_iso(),
        "note":         "memory-only mode",
    }


@router.get("/dlq", summary="Dead-letter queue contents", response_model=AdminDlqResponse)
async def get_dlq(request: Request, limit: int = Query(50, ge=1, le=200)):
    """Returns failed events from the dead-letter queue."""
    event_bus = request.app.state.event_bus
    if not event_bus:
        return {"events": [], "size": 0, "note": "No event bus available"}
    try:
        events = await event_bus.get_dlq_events(limit=limit)
        size = await event_bus.dlq_size()
        return {"events": events, "size": size}
    except Exception as exc:
        logger.warning("DLQ read failed: %s", exc)
        return {"events": [], "size": 0, "error": str(exc)}


@router.post("/dlq/retry", summary="Retry a DLQ event", status_code=200, response_model=AdminGenericResponse)
async def retry_dlq(request: Request, index: int = Query(0, ge=0)):
    """Pop and re-publish a DLQ event."""
    event_bus = request.app.state.event_bus
    if not event_bus:
        raise HTTPException(status_code=503, detail="No event bus available")
    try:
        entry = await event_bus.retry_dlq_event(index=index)
        if entry is None:
            raise HTTPException(status_code=404, detail="No event at that index")
        return {"status": "retried", "event": entry}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("DLQ retry failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/signals/stats", summary="Signal and correlation statistics", response_model=AdminGenericResponse)
async def signal_stats(request: Request):
    """Quick counts for signals, clusters, and composite events."""
    event_bus = request.app.state.event_bus
    if event_bus:
        try:
            cached = await event_bus.cache_get("precomputed:correlation_summary")
            if cached:
                return {**cached, "_served_from": "precomputed"}
        except Exception:
            pass

    try:
        import asyncio
        from storage.signal_repo import SignalRepository
        repo = SignalRepository()
        stats = await asyncio.wait_for(repo.count_signals(window_hours=24), timeout=8.0)
        return stats
    except asyncio.TimeoutError:
        logger.warning("count_signals timed out")
        return {"total": 0, "clustered": 0, "cluster_count": 0, "source_count": 0}
    except Exception as exc:
        logger.warning("Signal stats failed: %s", exc)
        return {"total": 0, "clustered": 0, "cluster_count": 0, "source_count": 0}

@router.post("/trigger-live", summary="Fire live ingest immediately", status_code=202, response_model=AdminGenericResponse)
async def trigger_live(request: Request):
    """
    Kicks off live + text ingest jobs immediately without waiting for the
    next scheduler tick.  Safe to call repeatedly — runs in background.
    Uses the same job functions as the scheduler so assets are extracted
    and only run_live_only / run_text_only paths are hit.
    """
    from scheduler.jobs import live_ingest_job, text_ingest_job
    from api.routers.ingest import _cleanup_old_jobs, _set_job, _get_job
    import uuid
    import asyncio
    from core.utils import utcnow_iso as _now

    app = request.app
    if not getattr(app.state, "db_available", False):
        raise HTTPException(status_code=503, detail="DB not available — cannot ingest")

    embedder = getattr(app.state, "embedder", None)

    async def _run_live_tracked(job_id: str) -> None:
        jdata = await _get_job(request, job_id) or {}
        jdata["status"] = "running"
        await _set_job(request, job_id, jdata)
        try:
            await live_ingest_job(
                orchestrator = app.state.orchestrator,
                enricher     = app.state.enricher,
                nlp          = app.state.nlp,
                repo         = None,
                graph        = app.state.graph,
                event_bus    = app.state.event_bus,
                embedder     = embedder,
            )
            jdata = await _get_job(request, job_id) or jdata
            jdata.update(status="done", finished_at=_now())
            await _set_job(request, job_id, jdata)
        except Exception as exc:
            jdata = await _get_job(request, job_id) or jdata
            jdata.update(status="failed", finished_at=_now(), error=str(exc))
            await _set_job(request, job_id, jdata)
            logger.error("trigger-live: live job %s failed: %s", job_id, exc)

    async def _run_text_tracked(job_id: str) -> None:
        jdata = await _get_job(request, job_id) or {}
        jdata["status"] = "running"
        await _set_job(request, job_id, jdata)
        try:
            await text_ingest_job(
                orchestrator = app.state.orchestrator,
                enricher     = app.state.enricher,
                nlp          = app.state.nlp,
                graph        = app.state.graph,
                event_bus    = app.state.event_bus,
                embedder     = embedder,
            )
            jdata = await _get_job(request, job_id) or jdata
            jdata.update(status="done", finished_at=_now())
            await _set_job(request, job_id, jdata)
        except Exception as exc:
            jdata = await _get_job(request, job_id) or jdata
            jdata.update(status="failed", finished_at=_now(), error=str(exc))
            await _set_job(request, job_id, jdata)
            logger.error("trigger-live: text job %s failed: %s", job_id, exc)

    job_ids = []
    await _cleanup_old_jobs()

    for label, runner in [("live", _run_live_tracked), ("text", _run_text_tracked)]:
        job_id = str(uuid.uuid4())
        await _set_job(request, job_id, {
            "job_id":       job_id,
            "status":       "pending",
            "query":        label,
            "started_at":   _now(),
            "finished_at":  None,
            "total_events": None,
            "source_counts": None,
            "source_errors": None,
            "error":        None,
        })
        asyncio.create_task(runner(job_id))
        job_ids.append(job_id)
        logger.info("trigger-live: queued %s job %s", label, job_id)

    return {"status": "accepted", "job_ids": job_ids}
