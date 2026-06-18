"""
worker.py
─────────
Background worker process for Vision-I.

Runs heavy/background tasks (scheduler jobs, event-driven pipeline worker, embeddings)
separately from the FastAPI HTTP server to keep API latency stable.
"""

import asyncio
import logging
import threading
import time
from pathlib import Path

from config.logging_config import setup_logging
from config.settings import settings

setup_logging()
logger = logging.getLogger("vision_i.worker")
WORKER_HEARTBEAT_PATH = Path("/tmp/vision-worker-heartbeat.json")

async def _start_tle_refresher() -> asyncio.Task:
    """
    Periodically fetch TLEs to a local file (shared via /app/data bind mount).
    This keeps the HTTP API satellite endpoint fast and avoids doing network I/O
    inside the request's 4s timeout window.
    """
    async def _loop():
        import os
        import time
        import tempfile
        from pathlib import Path

        url = settings.sat_tle_url.strip()
        path = settings.sat_tle_path.strip() or "/app/data/tles.active.tle"
        ttl_s = max(1, int(settings.sat_tle_cache_ttl_hours)) * 3600

        if not url:
            logger.info("TLE refresher disabled (SAT_TLE_URL not set)")
            return

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)

        while True:
            try:
                now = time.time()
                # Refresh if file is missing/empty or older than TTL.
                need = True
                try:
                    st = target.stat()
                    need = (st.st_size <= 0) or ((now - st.st_mtime) > ttl_s)
                except FileNotFoundError:
                    need = True
                except Exception:
                    need = True

                if not need:
                    await asyncio.sleep(60)
                    continue

                logger.info("TLE refresher: fetching %s", url)
                import requests
                resp = requests.get(
                    url,
                    timeout=10,
                    headers={"User-Agent": "Vision-I/1.0 (+https://localhost)"},
                )
                if resp.status_code != 200:
                    body = (resp.text or "").strip().replace("\r", "")
                    # CelesTrak often returns a custom 403 explaining throttling.
                    raise RuntimeError(f"HTTP {resp.status_code}: {body[:300]}")
                text = resp.text.strip() + "\n"
                if len(text) < 10:
                    raise ValueError("TLE refresher: response too small")

                # Atomic write to avoid partial reads by the API.
                with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=str(target.parent)) as tmp:
                    tmp.write(text)
                    tmp_path = tmp.name
                os.replace(tmp_path, str(target))

                logger.info("TLE refresher: wrote %d bytes to %s", len(text), str(target))
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("TLE refresher failed: %s", exc)
                # If we have no active TLE file yet, seed from a bundled file so the API
                # can compute quickly (even if it yields 0 passes for a small bbox).
                try:
                    if not target.exists() or target.stat().st_size <= 0:
                        seed = target.parent / "tles.seed.tle"
                        if seed.exists() and seed.stat().st_size > 0:
                            import shutil
                            shutil.copyfile(str(seed), str(target))
                            logger.info("TLE refresher: seeded %s from %s", str(target), str(seed))
                except Exception:
                    pass
                await asyncio.sleep(60)

    return asyncio.create_task(_loop())


def _start_worker_heartbeat() -> tuple[threading.Event, threading.Thread]:
    """
    Emit a small heartbeat file so container health checks can verify the worker
    process is alive without relying on an HTTP endpoint that does not exist.

    This runs in a daemon thread instead of the asyncio loop so heavy NLP or
    graph work cannot starve the heartbeat and mark a healthy worker unhealthy.
    """
    import json
    from datetime import datetime, timezone

    stop_event = threading.Event()
    WORKER_HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)

    def _loop() -> None:
        while not stop_event.is_set():
            try:
                WORKER_HEARTBEAT_PATH.write_text(
                    json.dumps(
                        {
                            "status": "ok",
                            "env": settings.app_env,
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                        }
                    ),
                    encoding="utf-8",
                )
            except Exception as exc:
                logger.warning("Worker heartbeat write failed: %s", exc)

            stop_event.wait(15)

    thread = threading.Thread(target=_loop, name="vision-worker-heartbeat", daemon=True)
    thread.start()
    return stop_event, thread


def _sources_cache_key(source: str, fetch_kwargs: dict, run_nlp: bool) -> str:
    import hashlib
    import json
    payload = {"source": source, "run_nlp": run_nlp, "kwargs": fetch_kwargs}
    raw = json.dumps(payload, sort_keys=True, default=str)
    return "sources:cache:" + hashlib.md5(raw.encode("utf-8")).hexdigest()


def _extractor_for_source(source_name: str):
    # Map the stable source_name values (used in API payloads) to extractor classes.
    # These names come from extractor_cls.source_name in each extractor.
    source_name = (source_name or "").strip().lower()
    if source_name == "newsapi":
        from extractors.news import NewsExtractor
        return NewsExtractor
    if source_name == "reddit":
        from extractors.socials import RedditExtractor
        return RedditExtractor
    if source_name == "youtube":
        from extractors.socials import YouTubeExtractor
        return YouTubeExtractor
    if source_name == "rss":
        from extractors.rss import RSSExtractor
        return RSSExtractor
    if source_name == "hackernews":
        from extractors.hackernews import HackerNewsExtractor
        return HackerNewsExtractor
    if source_name == "telegram":
        from extractors.telegram_monitor import TelegramExtractor
        return TelegramExtractor
    if source_name == "twitter":
        from extractors.twitter import TwitterExtractor
        return TwitterExtractor
    if source_name == "gdelt":
        from extractors.gdelt import GDELTExtractor
        return GDELTExtractor
    if source_name == "usgs":
        from extractors.usgs import USGSExtractor
        return USGSExtractor
    if source_name in {"stocks", "yahoo_finance"}:
        from extractors.stocks import StockExtractor
        return StockExtractor
    if source_name == "opensky":
        from extractors.opensky import OpenSkyExtractor
        return OpenSkyExtractor
    if source_name == "ais":
        from extractors.ais import AISExtractor
        return AISExtractor
    return None


async def _start_sources_warmer(event_bus, nlp) -> asyncio.Task:
    async def _loop():
        try:
            pubsub = await event_bus.subscribe("sources_warm")
            sem = asyncio.Semaphore(2)  # keep API smooth by throttling warm work

            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    import json
                    payload = json.loads(message["data"])
                    source = payload.get("source") or ""
                    run_nlp = bool(payload.get("run_nlp", True))
                    kwargs = payload.get("kwargs") or {}
                    cache_ttl = int(payload.get("cache_ttl") or 30)

                    extractor_cls = _extractor_for_source(source)
                    if extractor_cls is None:
                        logger.warning("Sources warmer: unknown source=%s", source)
                        continue

                    cache_key = _sources_cache_key(source, kwargs, run_nlp)

                    async def _work():
                        async with sem:
                            logger.info("Sources warmer: warming source=%s key=%s", source, cache_key)
                            def run():
                                ext = extractor_cls()
                                events = ext.run(**kwargs)
                                if run_nlp and events:
                                    nlp.process(events)
                                return events

                            loop = asyncio.get_running_loop()
                            try:
                                events = await asyncio.wait_for(loop.run_in_executor(None, run), timeout=12.0)
                            except asyncio.TimeoutError:
                                logger.warning("Sources warmer: timeout source=%s key=%s", source, cache_key)
                                events = []
                            value = {
                                "total": len(events),
                                "source": source,
                                "events": [{k: v for k, v in e.items() if k != "raw"} for e in events],
                                "cached": True,
                            }
                            await event_bus.cache_set(cache_key, value, ttl_seconds=max(0, min(cache_ttl, 600)))
                            logger.info("Sources warmer: cached source=%s total=%d", source, len(events))

                    asyncio.create_task(_work())

                except Exception as exc:
                    logger.error("Sources warmer: failed to process message: %s", exc)
                    continue
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.error("sources warmer loop crashed: %s", exc)

    return asyncio.create_task(_loop())


async def main() -> None:
    logger.info("Vision-I worker starting (env=%s)", settings.app_env)

    # Keep thread pool bounded; heavy CPU work should be explicitly offloaded.
    loop = asyncio.get_running_loop()

    heartbeat_stop, heartbeat_thread = _start_worker_heartbeat()
    logger.info("Worker heartbeat started: %s", WORKER_HEARTBEAT_PATH)

    # 0 ── TLE refresher (satellite passes) ──────────────────────────────────
    # Start early so the API gets local TLEs even while embeddings are loading.
    tle_task = None
    try:
        tle_task = await _start_tle_refresher()
        if tle_task:
            logger.info("TLE refresher started")
    except Exception as exc:
        logger.error("TLE refresher failed to start: %s", exc)

    # 1 ── PostgreSQL ──────────────────────────────────────────────────────────
    db_available = False
    if settings.db_available:
        try:
            from storage.database import init_db
            await init_db()
            db_available = True
        except Exception as exc:
            logger.warning("PostgreSQL unavailable: %s", exc)
    else:
        logger.warning("PostgreSQL not configured; worker will run limited mode")

    # 2 ── Neo4j ───────────────────────────────────────────────────────────────
    from storage.graph import GraphDB
    graph = GraphDB()
    if settings.neo4j_available:
        if graph.connect():
            graph.create_indexes()

    # 3 ── Redis Event Bus ─────────────────────────────────────────────────────
    event_bus = None
    try:
        from core.event_bus import EventBus
        event_bus = EventBus(settings.redis_url)
        await event_bus.connect()
        logger.info("Redis event bus connected")
    except Exception as exc:
        logger.warning("Redis unavailable: %s — worker continuing without event bus", exc)

    # 4 ── NLP + Orchestrator + Enricher ───────────────────────────────────────
    from nlp.pipeline import NLPPipeline
    from core.orchestrator import Orchestrator
    from core.enricher import Enricher

    nlp = NLPPipeline()
    orchestrator = Orchestrator(
        news_api_key=settings.newsapi_key,
        max_workers=settings.pipeline_workers,
    )
    enricher = Enricher()

    # 5 ── Embedder (optional) ─────────────────────────────────────────────────
    embedder = None
    if settings.load_embedder_on_startup:
        try:
            from intelligence.embedder import EmbeddingService
            embedder = EmbeddingService()
            await loop.run_in_executor(None, embedder.load)
            logger.info("Embedding model loaded: %s", embedder._model_name)
        except Exception as exc:
            logger.warning("Embedding service failed to load: %s", exc)

    # 6 ── Scheduler (optional) ────────────────────────────────────────────────
    scheduler = None
    if db_available and settings.run_scheduler:
        try:
            from scheduler.jobs import create_scheduler
            # Build a minimal swarm so anomaly_scan_job can generate LLM CEO summaries.
            _worker_swarm = None
            try:
                from agents.swarm import SwarmManager
                from agents.coordinator_agent import CoordinatorAgent
                from agents.llm_provider import LLMProvider
                _llm = LLMProvider()
                _worker_swarm = SwarmManager(
                    orchestrator=orchestrator,
                    enricher=enricher,
                    nlp=nlp,
                    graph=graph,
                    llm=_llm,
                )
                _worker_swarm.register(CoordinatorAgent(swarm=_worker_swarm))
            except Exception as _exc:
                logger.warning("Worker swarm init failed: %s", _exc)
            scheduler = create_scheduler(
                orchestrator=orchestrator,
                enricher=enricher,
                nlp=nlp,
                graph=graph,
                event_bus=event_bus,
                embedder=embedder,
                swarm=_worker_swarm,
            )
            scheduler.start()
            logger.info("Scheduler started")
        except Exception as exc:
            logger.error("Scheduler failed to start: %s", exc)

    # 7 ── Event-driven pipeline worker (optional) ─────────────────────────────
    pipeline_task = None
    if event_bus and db_available and settings.run_pipeline_worker:
        try:
            from intelligence.pipeline_worker import start_pipeline_worker
            pipeline_task = await start_pipeline_worker(event_bus=event_bus, graph=graph)
            logger.info("Pipeline worker started")
        except Exception as exc:
            logger.error("Pipeline worker failed to start: %s", exc)

    # 7b ── Cache warmer (sources/*) ───────────────────────────────────────────
    sources_warmer_task = None
    if event_bus:
        try:
            sources_warmer_task = await _start_sources_warmer(event_bus=event_bus, nlp=nlp)
            logger.info("Sources cache warmer started")
        except Exception as exc:
            logger.error("Sources cache warmer failed to start: %s", exc)

    logger.info("Vision-I worker ready")

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        logger.info("Worker shutting down...")
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=2)
        if tle_task:
            tle_task.cancel()
        if sources_warmer_task:
            sources_warmer_task.cancel()
        if pipeline_task:
            pipeline_task.cancel()
        if scheduler:
            scheduler.shutdown(wait=True)
        if event_bus:
            await event_bus.close()
        if db_available:
            from storage.database import close_db
            await close_db()
        graph.close()
        try:
            WORKER_HEARTBEAT_PATH.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())

