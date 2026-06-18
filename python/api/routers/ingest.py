"""
api/routers/ingest.py
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
POST /ingest        â€” trigger ingestion run (returns job_id immediately)
GET  /ingest/{id}   â€” poll job status

Background job sequence:
  1. Orchestrator.run()     â†’ fetch + normalize all sources
  2. Enricher.enrich()      â†’ fill missing article bodies (if enrich=true)
  3. NLPPipeline.process()  â†’ NER + sentiment + entity resolution
  4. EventRepository.upsert_many()  â†’ persist to PostgreSQL (if available)
  5. GraphDB.write_events() â†’ persist to Neo4j (if available)
  6. EventRepository.save_job()     â†’ audit log (if available)
"""

import asyncio
import json
import time
import uuid
from typing import Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from pydantic import BaseModel, Field

from core.utils import utcnow_iso
from core.source_registry import source_registry

router = APIRouter(tags=["Ingest"])
# Replaced in-memory _jobs with Redis for horizontal scalability.
_JOB_MAX_AGE_HOURS = 24
_JOB_PREFIX = "ingest:job:"
# Some routers still import `_jobs` directly for "memory fallback" behavior.
# Redis remains the source of truth, but we keep a best-effort mirror here so
# imports don't crash and older endpoints can still function in single-instance.
_jobs: Dict[str, dict] = {}
_jobs_lock = asyncio.Lock()


async def _cleanup_old_jobs() -> None:
    # Best-effort cleanup of the in-memory mirror only.
    # Redis keys have TTL handled by cache_set(... ttl_seconds=...).
    cutoff_ms = int(time.time() * 1000) - (_JOB_MAX_AGE_HOURS * 3600 * 1000)
    async with _jobs_lock:
        stale = []
        for job_id, job in _jobs.items():
            started_at = (job or {}).get("started_at") or ""
            # started_at is ISO; if missing/unknown, keep it.
            if not started_at:
                continue
            # If parsing fails, keep it (don't accidentally delete).
            try:
                # Minimal parse: YYYY-MM-DDTHH:MM:SS (ms/zone ignored)
                y = int(started_at[0:4]); m = int(started_at[5:7]); d = int(started_at[8:10])
                hh = int(started_at[11:13]); mm = int(started_at[14:16]); ss = int(started_at[17:19])
                # Approximate to epoch ms using time.mktime (local) is OK for cleanup heuristic.
                approx_ms = int(time.mktime((y, m, d, hh, mm, ss, 0, 0, -1)) * 1000)
                if approx_ms < cutoff_ms:
                    stale.append(job_id)
            except Exception:
                continue
        for job_id in stale:
            _jobs.pop(job_id, None)

async def _get_job(request: Request, job_id: str) -> Optional[dict]:
    event_bus = getattr(request.app.state, "event_bus", None)
    if event_bus:
        try:
            job = await asyncio.wait_for(
                event_bus.cache_get(f"{_JOB_PREFIX}{job_id}"),
                timeout=1.5,
            )
        except Exception:
            job = None
        if job:
            return job

    async with _jobs_lock:
        job = _jobs.get(job_id)
    return job

async def _set_job(request: Request, job_id: str, job_data: dict) -> None:
    event_bus = getattr(request.app.state, "event_bus", None)
    if event_bus:
        try:
            await asyncio.wait_for(
                event_bus.cache_set(
                    f"{_JOB_PREFIX}{job_id}",
                    job_data,
                    ttl_seconds=_JOB_MAX_AGE_HOURS * 3600,
                ),
                timeout=1.5,
            )
        except Exception:
            pass
    async with _jobs_lock:
        _jobs[job_id] = job_data


class IngestRequest(BaseModel):
    query:   str                   = "world news"
    limit:   int                   = Field(default=10, ge=1, le=500)
    sources: Optional[List[str]]   = None   # None = all sources
    enrich:  bool                  = False  # article body enrichment


class IngestStatus(BaseModel):
    job_id:        str
    status:        str
    query:         str
    started_at:    str
    finished_at:   Optional[str]  = None
    total_events:  Optional[int]  = None
    source_counts: Optional[Dict[str, int]] = None
    source_errors: Optional[Dict[str, str]] = None
    error:         Optional[str]  = None

class IngestTriggerResponse(BaseModel):
    job_id: str
    status: str
    query:  str

async def _run_job(
    job_id:      str,
    req:         IngestRequest,
    orchestrator,
    enricher,
    nlp,
    db_available: bool,
    graph,
    event_bus=None,
) -> None:
    job_key = f"{_JOB_PREFIX}{job_id}"
    if event_bus:
        job = await event_bus.cache_get(job_key) or {}
    else:
        async with _jobs_lock:
            job = dict(_jobs.get(job_id) or {})

    job["status"] = "running"

    if event_bus:
        await event_bus.cache_set(job_key, job, ttl_seconds=_JOB_MAX_AGE_HOURS * 3600)
    async with _jobs_lock:
        _jobs[job_id] = job

    try:
        # 1. Ingest
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: orchestrator.run(query=req.query, limit=req.limit, sources=req.sources),
        )

        # 2. Enrich (optional, controlled by caller)
        if req.enrich:
            await loop.run_in_executor(None, lambda: enricher.enrich(result.events))

        # 3. NLP â€” full pipeline
        if result.events:
            await loop.run_in_executor(None, lambda: nlp.process(result.events))

        # 4 + 5. Persist (best-effort â€” don't fail the job if DB is down)
        if db_available and result.events:
            try:
                from storage.database import get_session
                from storage.event_repo import EventRepository
                async with get_session() as session:
                    repo = EventRepository(session)
                    await repo.upsert_many(result.events)
                    await repo.save_job({
                        "job_id":        job_id,
                        "query":         req.query,
                        "status":        "done",
                        "started_at":    job["started_at"],
                        "finished_at":   utcnow_iso(),
                        "total_events":  result.total,
                        "source_counts": result.source_counts,
                        "source_errors": result.source_errors,
                    })
            except Exception as db_exc:
                job.setdefault("warnings", []).append(f"DB persist: {db_exc}")

        if graph.available and result.events:
            try:
                await graph.write_events(result.events)
            except Exception as g_exc:
                job.setdefault("warnings", []).append(f"Graph write: {g_exc}")

        job.update(
            status        = "done",
            finished_at   = utcnow_iso(),
            total_events  = result.total,
            source_counts = result.source_counts,
            source_errors = result.source_errors,
            events        = result.events,   # kept for /events?job_id=
        )
        if event_bus:
            await event_bus.cache_set(job_key, job, ttl_seconds=_JOB_MAX_AGE_HOURS * 3600)
        async with _jobs_lock:
            _jobs[job_id] = job

    except Exception as exc:
        job.update(
            status      = "failed",
            finished_at = utcnow_iso(),
            error       = str(exc),
        )
        if event_bus:
            await event_bus.cache_set(job_key, job, ttl_seconds=_JOB_MAX_AGE_HOURS * 3600)
        async with _jobs_lock:
            _jobs[job_id] = job

@router.post("", summary="Trigger ingestion run", status_code=202, response_model=IngestTriggerResponse)
async def trigger_ingest(
    body:             IngestRequest,
    background_tasks: BackgroundTasks,
    request:          Request,
):
    unknown_sources = source_registry.unknown(body.sources)
    if unknown_sources:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unknown_sources",
                "message": f"Unknown sources: {', '.join(unknown_sources)}",
                "valid_sources": [definition.key for definition in source_registry.all()],
            },
        )

    normalized_sources = source_registry.normalize_many(body.sources)
    body = body.model_copy(update={"sources": normalized_sources or None})
    job_id = str(uuid.uuid4())

    job_data = {
        "job_id":       job_id,
        "status":       "pending",
        "query":        body.query,
        "started_at":   utcnow_iso(),
        "finished_at":  None,
        "total_events": None,
        "source_counts": None,
        "source_errors": None,
        "error":        None,
    }
    await _set_job(request, job_id, job_data)

    background_tasks.add_task(
        _run_job,
        job_id,
        body,
        request.app.state.orchestrator,
        request.app.state.enricher,
        request.app.state.nlp,
        request.app.state.db_available,
        request.app.state.graph,
        request.app.state.event_bus,
    )

    return {"job_id": job_id, "status": "pending", "query": body.query}


@router.get("/{job_id}", response_model=IngestStatus, summary="Poll job status")
async def job_status(job_id: str, request: Request):
    job = await _get_job(request, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return IngestStatus(**{k: v for k, v in job.items()
                           if k not in ("events", "warnings")})

