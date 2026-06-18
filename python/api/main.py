"""FastAPI entry point for the Vision-I intelligence layer."""

import time
import uuid
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from prometheus_client import Counter, Histogram, make_asgi_app
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from config.logging_config import setup_logging
from config.settings import settings

setup_logging()
logger = logging.getLogger("vision_i.api")
HTTP_REQUESTS_TOTAL = Counter(
    "vision_http_requests_total",
    "HTTP requests processed",
    ["method", "path", "status"],
)
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "vision_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
    buckets=(0.005, 0.01, 0.02, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
)
limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])


async def _run_startup_bootstrap(app: FastAPI, graph) -> None:
    """Run the initial startup bootstrap jobs when enabled."""
    if not app.state.db_available or not settings.bootstrap_live_on_startup:
        return

    try:
        from scheduler.jobs import live_ingest_job, text_ingest_job

        logger.info("Startup bootstrap: launching initial live ingest")
        await live_ingest_job(
            orchestrator=app.state.orchestrator,
            enricher=app.state.enricher,
            nlp=app.state.nlp,
            repo=None,
            graph=graph,
            event_bus=app.state.event_bus,
            embedder=app.state.embedder,
        )
        logger.info("Startup bootstrap: initial live ingest complete")

        logger.info("Startup bootstrap: launching initial text ingest")
        await text_ingest_job(
            orchestrator=app.state.orchestrator,
            enricher=app.state.enricher,
            nlp=app.state.nlp,
            graph=graph,
            event_bus=app.state.event_bus,
            embedder=app.state.embedder,
        )
        logger.info("Startup bootstrap: initial text ingest complete")
    except Exception as exc:
        logger.warning("Startup bootstrap failed: %s", exc)


async def _warm_stream_cache_from_db(app: FastAPI) -> None:
    """Seed precomputed:live_streams from recent DB events when cache is cold."""
    event_bus = getattr(app.state, "event_bus", None)
    if not event_bus or not getattr(app.state, "db_available", False):
        return
    try:
        existing = await event_bus.cache_get("precomputed:live_streams")
        if existing:
            return
    except Exception:
        return
    try:
        from datetime import datetime, timezone, timedelta
        from sqlalchemy import desc as sa_desc, select
        from storage.database import EventModel, get_session

        cutoff = datetime.now(timezone.utc) - timedelta(hours=6)
        async with get_session() as session:
            rows = (await session.execute(
                select(EventModel)
                .where(EventModel.ingest_time >= cutoff)
                .order_by(sa_desc(EventModel.ingest_time))
                .limit(100)
            )).scalars().all()

        events = [
            {
                "event_id":   r.event_id,
                "title":      r.title,
                "source":     r.source,
                "event_type": r.event_type,
                "timestamp":  r.timestamp.isoformat() if r.timestamp else None,
                "risk_score": r.risk_score,
                "actors":     r.actors or [],
                "tags":       r.tags or [],
            }
            for r in rows
        ]
        if events:
            await event_bus.cache_set("precomputed:live_streams", events, ttl_seconds=900)
            logger.info("Startup: seeded precomputed:live_streams with %d events from DB", len(events))
    except Exception as exc:
        logger.warning("Startup stream cache warm failed: %s", exc)


async def _neo4j_reconnect_loop(graph) -> None:
    """Retry Neo4j connect with backoff until available (max 10 attempts)."""
    import asyncio
    delay = 5
    loop = asyncio.get_running_loop()
    for attempt in range(1, 11):
        if graph.available:
            return
        await asyncio.sleep(delay)
        logger.info("Neo4j reconnect attempt %d/10", attempt)
        connected = await loop.run_in_executor(None, graph.connect)
        if connected:
            await loop.run_in_executor(None, graph.create_indexes)
            logger.info("Neo4j reconnected on attempt %d", attempt)
            return
        delay = min(delay * 2, 60)
    logger.warning("Neo4j reconnect exhausted — graph features remain disabled")


async def _bootstrap_tracked_queries() -> None:
    """Seed tracked queries once when the table is empty."""
    defaults = settings.default_tracked_queries
    if not defaults:
        return

    try:
        from sqlalchemy import func, select
        from storage.database import TrackedQueryModel, get_session

        async with get_session() as session:
            existing_count = (
                await session.execute(select(func.count()).select_from(TrackedQueryModel))
            ).scalar_one()

            if existing_count:
                return

            for query in defaults:
                session.add(TrackedQueryModel(
                    query=query,
                    created_by="bootstrap",
                    is_active=True,
                ))

        logger.info("Bootstrapped %d tracked queries into PostgreSQL", len(defaults))
    except Exception as exc:
        logger.warning("Tracked query bootstrap skipped: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Vision-I API starting  (env=%s)", settings.app_env)

    # Keep the default thread pool bounded.
    import asyncio
    import concurrent.futures
    loop = asyncio.get_running_loop()
    loop.set_default_executor(concurrent.futures.ThreadPoolExecutor(max_workers=16))
    app.state.db_available = False
    if settings.db_available:
        try:
            from storage.database import init_db
            await init_db()
            app.state.db_available = True
            await _bootstrap_tracked_queries()
        except Exception as exc:
            logger.warning("PostgreSQL unavailable: %s -- memory-only mode", exc)
    else:
        logger.info("PostgreSQL not configured -- memory-only mode")
    from storage.graph import GraphDB
    graph = GraphDB()
    app.state._neo4j_reconnect_task = None
    if settings.neo4j_available:
        if graph.connect():
            graph.create_indexes()
        else:
            import asyncio as _asyncio
            app.state._neo4j_reconnect_task = _asyncio.create_task(_neo4j_reconnect_loop(graph))
    app.state.graph = graph
    from nlp.pipeline import NLPPipeline
    app.state.nlp = NLPPipeline()
    logger.info("NLP pipeline initialized (models will load lazily on first use)")
    app.state.event_bus = None
    try:
        from core.event_bus import EventBus
        event_bus = EventBus(settings.redis_url)
        await event_bus.connect()
        app.state.event_bus = event_bus
        logger.info("Redis event bus connected")
        if app.state.db_available:
            import asyncio as _asyncio
            _asyncio.create_task(_warm_stream_cache_from_db(app))
    except Exception as exc:
        logger.warning("Redis unavailable: %s -- running without event bus", exc)

    # Load the embedding service when this process is responsible for it.
    app.state.embedder = None
    if settings.load_embedder_on_startup:
        try:
            from intelligence.embedder import EmbeddingService
            embedder = EmbeddingService()
            await loop.run_in_executor(None, embedder.load)
            app.state.embedder = embedder
            logger.info("Embedding model loaded: %s", embedder._model_name)
        except Exception as exc:
            logger.warning("Embedding service failed to load: %s -- signals will lack embeddings", exc)
    else:
        logger.info("Embedding service disabled for this process")
    from core.orchestrator import Orchestrator
    from core.enricher import Enricher
    app.state.orchestrator = Orchestrator(
        news_api_key=settings.newsapi_key,
        max_workers=settings.pipeline_workers,
    )
    app.state.enricher = Enricher()

    # Init LLM + agent swarm before the scheduler so anomaly_scan_job gets the
    # fully-wired swarm (with LLM) when computing JARVIS CEO summaries.
    llm = None
    try:
        from agents.llm_provider import LLMProvider
        llm = LLMProvider()
        app.state.llm = llm
        if llm.available:
            logger.info("LLM provider ready: %s (model: %s)", llm.provider, llm.model)
        else:
            logger.info("LLM provider: none configured (agents work without LLM)")
    except Exception as exc:
        logger.warning("LLM provider failed to init: %s", exc)
    try:
        from agents.swarm import SwarmManager
        from agents.ingestion_agent import IngestionAgent
        from agents.analysis_agent import AnalysisAgent
        from agents.specialist_agents import NarrativeAgent, AnomalyAgent, GraphAgent
        from agents.coordinator_agent import CoordinatorAgent

        swarm = SwarmManager(
            orchestrator=app.state.orchestrator,
            enricher=app.state.enricher,
            nlp=app.state.nlp,
            graph=graph,
            llm=llm,
        )
        swarm.register(IngestionAgent(orchestrator=app.state.orchestrator))
        swarm.register(AnalysisAgent(nlp=app.state.nlp, enricher=app.state.enricher, llm=llm))
        swarm.register(NarrativeAgent(graph=graph))
        swarm.register(AnomalyAgent())
        swarm.register(GraphAgent(graph=graph))
        swarm.register(CoordinatorAgent(swarm=swarm))
        app.state.swarm = swarm
        logger.info("Agent swarm ready -- %d agents registered", len(swarm.list_agents()))
    except Exception as exc:
        logger.warning("Agent swarm failed to initialise: %s", exc)
        app.state.swarm = None

    app.state.scheduler = None
    if app.state.db_available and settings.run_scheduler:
        try:
            from scheduler.jobs import create_scheduler
            scheduler = create_scheduler(
                orchestrator=app.state.orchestrator,
                enricher=app.state.enricher,
                nlp=app.state.nlp,
                graph=graph,
                event_bus=app.state.event_bus,
                embedder=app.state.embedder,
                swarm=app.state.swarm,
            )
            scheduler.start()
            app.state.scheduler = scheduler
            logger.info("Scheduler started")
        except Exception as exc:
            logger.warning("Scheduler failed to start: %s", exc)
    elif app.state.db_available and not settings.run_scheduler:
        logger.info("Scheduler disabled for this process")

    app.state.bootstrap_task = None
    if app.state.db_available and settings.bootstrap_live_on_startup and settings.run_scheduler:
        import asyncio as _asyncio
        app.state.bootstrap_task = _asyncio.create_task(_run_startup_bootstrap(app, graph))

    app.state.pipeline_worker = None
    if app.state.event_bus and app.state.db_available and settings.run_pipeline_worker:
        try:
            from intelligence.pipeline_worker import start_pipeline_worker
            worker_task = await start_pipeline_worker(
                event_bus=app.state.event_bus,
                graph=graph,
            )
            app.state.pipeline_worker = worker_task
            logger.info("Intelligence pipeline worker started")
        except Exception as exc:
            logger.warning("Pipeline worker failed to start: %s", exc)
    elif app.state.db_available and not settings.run_pipeline_worker:
        logger.info("Pipeline worker disabled for this process")

    logger.info("Vision-I API ready")

    yield
    logger.info("Shutting down...")
    if app.state.bootstrap_task:
        app.state.bootstrap_task.cancel()
    if getattr(app.state, "_neo4j_reconnect_task", None):
        app.state._neo4j_reconnect_task.cancel()
    if app.state.pipeline_worker:
        app.state.pipeline_worker.cancel()
    if app.state.scheduler:
        app.state.scheduler.shutdown(wait=True)
    if llm:
        await llm.close()
    if app.state.event_bus:
        await app.state.event_bus.close()
    if app.state.db_available:
        from storage.database import close_db
        await close_db()
    app.state.graph.close()
    logger.info("Shutdown complete")
app = FastAPI(
    title="Vision-I Intelligence API",
    description=(
        "Internal intelligence layer for the Vision-I platform. "
        "Called only by the .NET Web API Core -- not exposed publicly."
    ),
    version="1.0.0",
    # Hide docs in production.
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    lifespan=lifespan,
)

app.version = "1.1.0"
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)
FastAPIInstrumentor.instrument_app(app)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Internal-Key", "X-Request-ID"],
)
@app.middleware("http")
async def request_metadata_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    start = time.perf_counter()

    response = await call_next(request)

    elapsed_ms = (time.perf_counter() - start) * 1000
    elapsed_s = elapsed_ms / 1000.0
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.1f}"

    path = request.url.path
    # Skip metrics self-scrapes.
    if path != "/metrics":
        HTTP_REQUESTS_TOTAL.labels(
            method=request.method,
            path=path,
            status=str(response.status_code),
        ).inc()
        HTTP_REQUEST_DURATION_SECONDS.labels(
            method=request.method,
            path=path,
        ).observe(elapsed_s)

    logger.info(
        "%s %s â†’ %d  %.1fms  rid=%s",
        request.method, request.url.path,
        response.status_code, elapsed_ms, request_id,
    )
    return response
_AUDIT_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def audit_middleware(request: Request, call_next):
    response = await call_next(request)
    if request.method not in _AUDIT_METHODS or not getattr(app.state, "db_available", False):
        return response
    if request.url.path in _OPEN:
        return response
    try:
        logger.info(
            "AUDIT %s %s status=%d actor=%s ip=%s rid=%s",
            request.method,
            request.url.path,
            response.status_code,
            request.headers.get("X-User", "internal"),
            request.client.host if request.client else "-",
            response.headers.get("X-Request-ID", "-"),
        )
    except Exception as exc:
        logger.debug("audit log skipped: %s", exc)
    return response
# Keep probes and metrics open for health checks and scraping.
_OPEN = {
    "/health",
    "/ready",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/config/classification",
    "/overview",
    "/overview/source-health",
    "/delta",
}


@app.middleware("http")
async def require_internal_key(request: Request, call_next):
    path = request.url.path
    if path in _OPEN or path.startswith("/metrics"):
        return await call_next(request)
    key = request.headers.get("X-Internal-Key", "")
    if settings.internal_api_key and key != settings.internal_api_key:
        logger.warning("Unauthorized request to %s from %s",
                       path, request.client.host if request.client else "unknown")
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return await call_next(request)
from api.routers import (  # noqa: E402
    admin, alerts, agents as agents_router, airspace, assets, copilot, decisions, entities, events,
    event_detail, health, ingest, influence, intelligence as intelligence_router, narratives, ontology,
    playbooks, sentiment, signals, situations, sources, streams, workspace as workspace_router,
    threatboard as threatboard_router,
    watchlist as watchlist_router,
    annotations as annotations_router,
)
from api.routers import reports as reports_router  # noqa: E402  (added Sprint G)
from api.routers import ingest_doc as ingest_doc_router  # noqa: E402  (added Sprint G)
from api.routers import bookmarks as bookmarks_router  # noqa: E402  (added Sprint I)
from api.routers import subscriptions as subscriptions_router  # noqa: E402  (added Sprint I)
from api.routers import overview as overview_router   # noqa: E402
from api.routers import delta as delta_router          # noqa: E402
from api.routers import objects as objects_router       # noqa: E402  (ontology object read-model)

app.include_router(health.router)
app.include_router(event_detail.router)
app.include_router(ingest.router,     prefix="/ingest")
app.include_router(events.router,     prefix="/events")
app.include_router(entities.router,   prefix="/entities")
app.include_router(streams.router,    prefix="/streams")
app.include_router(sentiment.router,  prefix="/sentiment")
app.include_router(sources.router,    prefix="/sources")
app.include_router(airspace.router,   prefix="/airspace")
app.include_router(admin.router,      prefix="/admin")
app.include_router(narratives.router, prefix="/narratives")
app.include_router(influence.router,  prefix="/influence")
app.include_router(alerts.router,     prefix="/alerts")
app.include_router(agents_router.router, prefix="/agents")
app.include_router(signals.router,       prefix="/signals")
app.include_router(assets.router,        prefix="/assets")
app.include_router(ontology.router,      prefix="/ontology")
app.include_router(decisions.router,     prefix="/decisions")
app.include_router(situations.router,    prefix="/situations")
app.include_router(copilot.router,       prefix="/copilot")
app.include_router(playbooks.router)
app.include_router(intelligence_router.router, prefix="/intelligence")
app.include_router(workspace_router.router,   prefix="/workspace")
app.include_router(threatboard_router.router, prefix="/threatboard")
app.include_router(watchlist_router.router,   prefix="/watchlist")
app.include_router(annotations_router.router, prefix="/events")
app.include_router(reports_router.router,     prefix="/reports")
app.include_router(ingest_doc_router.router,        prefix="/ingest")
app.include_router(bookmarks_router.router,         prefix="/bookmarks")
app.include_router(subscriptions_router.router,     prefix="/subscriptions")
app.include_router(overview_router.router,          prefix="/overview")
app.include_router(delta_router.router,             prefix="/delta")
app.include_router(objects_router.router,           prefix="/objects")





