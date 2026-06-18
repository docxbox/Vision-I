"""
storage/database.py
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Async PostgreSQL setup using SQLAlchemy 2.0 + asyncpg.

Provides:
  - async_engine   : the SQLAlchemy async engine
  - AsyncSession   : session factory for dependency injection
  - Base           : declarative base for all models
  - init_db()      : creates all tables (called at startup)

All models are defined here so Alembic can discover them via autogenerate.
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, Float,
    Index, Integer, String, Text, func, text
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from pgvector.sqlalchemy import Vector

from config.settings import settings

logger = logging.getLogger("vision_i.storage.database")
async_engine = create_async_engine(
    settings.postgres_dsn,
    pool_size     = 40,
    max_overflow  = 10,
    pool_timeout  = 10,       # fail fast instead of blocking 380s
    pool_recycle  = 1800,     # recycle stale connections every 30 min
    pool_pre_ping = True,
    echo          = False,    # set True to log SQL in development
    connect_args  = {
        "server_settings": {"statement_timeout": str(settings.db_statement_timeout_ms)},
    },
)

AsyncSessionFactory = async_sessionmaker(
    bind        = async_engine,
    expire_on_commit = False,
    class_      = AsyncSession,
)
class Base(DeclarativeBase):
    pass

class EventModel(Base):
    """
    Stores normalised Vision-I events.
    One row per unique event_id (upsert on conflict).
    """
    __tablename__ = "events"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    event_id        = Column(String(128), unique=True, nullable=False, index=True)
    source          = Column(String(64),  nullable=False, index=True)
    source_id       = Column(String(256), nullable=True)
    event_type      = Column(String(64),  nullable=False, index=True)
    title           = Column(Text,        nullable=False)
    description     = Column(Text,        nullable=True)
    body            = Column(Text,        nullable=True)
    url             = Column(Text,        nullable=True)
    language        = Column(String(10),  nullable=True, default="en")
    author          = Column(String(256), nullable=True)
    timestamp       = Column(DateTime(timezone=True), nullable=True, index=True)
    ingest_time     = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    sentiment_label = Column(String(16),  nullable=True)
    sentiment_score = Column(Float,       nullable=True)
    location_lat    = Column(Float,       nullable=True)
    location_lon    = Column(Float,       nullable=True)
    location_name   = Column(String(256), nullable=True)
    actors          = Column(JSONB,       nullable=True, default=list)
    tags            = Column(JSONB,       nullable=True, default=list)
    extras          = Column(JSONB,       nullable=True, default=dict)
    confidence_score    = Column(Float, nullable=True)
    influence_score     = Column(Float, nullable=True)
    risk_score          = Column(Float, nullable=True, index=True)
    supporting_signals  = Column(JSONB, nullable=True, default=list)
    signal_count        = Column(Integer, nullable=True, default=0)
    reasoning           = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_events_timestamp_source", "timestamp", "source"),
        Index("ix_events_location", "location_lat", "location_lon"),
    )


class TrackedQueryModel(Base):
    """Queries that the scheduler runs periodically."""
    __tablename__ = "tracked_queries"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    query      = Column(String(512), nullable=False, unique=True)
    created_by = Column(String(256), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    is_active  = Column(Boolean, nullable=False, default=True)
    last_run   = Column(DateTime(timezone=True), nullable=True)
    run_count  = Column(Integer, nullable=False, default=0)


class IngestJobModel(Base):
    """Audit log of every ingestion job that ran."""
    __tablename__ = "ingest_jobs"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    job_id       = Column(String(64), unique=True, nullable=False, index=True)
    query        = Column(String(512), nullable=True)
    status       = Column(String(32),  nullable=False, default="pending")
    started_at   = Column(DateTime(timezone=True), server_default=func.now())
    finished_at  = Column(DateTime(timezone=True), nullable=True)
    total_events = Column(Integer, nullable=True)
    source_counts = Column(JSONB,  nullable=True)
    source_errors = Column(JSONB,  nullable=True)
    error        = Column(Text,    nullable=True)


class NarrativeModel(Base):
    """
    Detected narrative signals from the NarrativeDetector.

    A narrative is a pattern detected across multiple sources â€” velocity spike,
    cross-source amplification, or sentiment divergence.
    """
    __tablename__ = "narratives"

    id           = Column(Integer,    primary_key=True, autoincrement=True)
    narrative_id = Column(String(64), unique=True, nullable=False, index=True)
    signal_type  = Column(String(64), nullable=False, index=True)
    topic        = Column(String(256), nullable=False)
    strength     = Column(Float,      nullable=False, default=0.0)
    confidence   = Column(Float,      nullable=False, default=0.0)
    severity     = Column(String(16), nullable=False, default="low", index=True)
    event_count  = Column(Integer,    nullable=False, default=0)
    source_count = Column(Integer,    nullable=False, default=0)
    sources      = Column(JSONB,      nullable=True, default=list)
    actors       = Column(JSONB,      nullable=True, default=list)
    sample_titles = Column(JSONB,     nullable=True, default=list)
    window_start = Column(DateTime(timezone=True), nullable=True)
    window_end   = Column(DateTime(timezone=True), nullable=True)
    detected_at  = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    meta_data     = Column(JSONB,      nullable=True, default=dict)
    status       = Column(String(16), nullable=False, default="active")  # active | resolved

    __table_args__ = (
        Index("ix_narratives_detected_topic", "detected_at", "topic"),
    )


class SourceCheckpointModel(Base):
    """
    Per-source high-water mark for incremental ingestion.
    Each source stores the timestamp and ID of the newest event seen,
    so subsequent runs only fetch newer data.
    """
    __tablename__ = "source_checkpoints"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    source         = Column(String(64), unique=True, nullable=False, index=True)
    last_event_ts  = Column(DateTime(timezone=True), nullable=True)
    last_event_id  = Column(String(128), nullable=True)
    last_run_at    = Column(DateTime(timezone=True), nullable=True)
    events_fetched    = Column(Integer, nullable=False, default=0)
    meta              = Column(JSONB, nullable=True, default=dict)
    credibility_score = Column(Float,  nullable=True)
    credibility_note  = Column(Text,   nullable=True)


class DataLineageModel(Base):
    """
    Tracks data flow through the pipeline.
    Each pipeline stage (raw_ingest, nlp_enriched, ontology_mapped, intelligence)
    creates a row with parent_batch linking to the upstream stage.
    """
    __tablename__ = "data_lineage"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    batch_id     = Column(String(64), nullable=False, index=True)
    source       = Column(String(64), nullable=True)
    stage        = Column(String(32), nullable=False)
    event_count  = Column(Integer, nullable=False, default=0)
    started_at   = Column(DateTime(timezone=True), nullable=False)
    finished_at  = Column(DateTime(timezone=True), nullable=True)
    parent_batch = Column(String(64), nullable=True, index=True)
    checksum     = Column(String(64), nullable=True)
    meta         = Column(JSONB, nullable=True, default=dict)


class OntologyActorModel(Base):
    """
    PostgreSQL cache of ontology actors â€” queryable without Neo4j.
    Synced from the ontology mapper after each ingestion cycle.
    """
    __tablename__ = "ontology_actors"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    actor_id        = Column(String(128), unique=True, nullable=False, index=True)
    canonical_name  = Column(String(256), nullable=False)
    entity_type     = Column(String(32), nullable=False, default="UNKNOWN")
    aliases         = Column(JSONB, nullable=True, default=list)
    first_seen      = Column(DateTime(timezone=True), nullable=True)
    last_seen       = Column(DateTime(timezone=True), nullable=True)
    mention_count   = Column(Integer, nullable=False, default=0)
    source_count    = Column(Integer, nullable=False, default=0)
    influence_score = Column(Float, nullable=True)
    meta            = Column(JSONB, nullable=True, default=dict)


class OntologyLocationModel(Base):
    """
    PostgreSQL cache of ontology locations â€” queryable without Neo4j.
    """
    __tablename__ = "ontology_locations"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    location_id  = Column(String(128), unique=True, nullable=False, index=True)
    name         = Column(String(256), nullable=False)
    lat          = Column(Float, nullable=True)
    lon          = Column(Float, nullable=True)
    country      = Column(String(128), nullable=True)
    event_count  = Column(Integer, nullable=False, default=0)


class AlertModel(Base):
    """
    Anomaly alerts from the AnomalyDetector.

    Persisted so operators can see alert history, acknowledge, and track trends.
    """
    __tablename__ = "alerts"

    id          = Column(Integer,    primary_key=True, autoincrement=True)
    alert_id    = Column(String(64), unique=True, nullable=False, index=True)
    alert_type  = Column(String(64), nullable=False, index=True)
    severity    = Column(String(16), nullable=False, default="medium", index=True)
    title       = Column(String(512), nullable=False)
    description = Column(Text,       nullable=True)
    entity      = Column(String(256), nullable=True, index=True)
    entity_type = Column(String(64),  nullable=True)
    event_count = Column(Integer,     nullable=False, default=0)
    baseline    = Column(Float,       nullable=True)
    z_score     = Column(Float,       nullable=True)
    sources     = Column(JSONB,       nullable=True, default=list)
    location    = Column(String(256), nullable=True)
    detected_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    resolved_at  = Column(DateTime(timezone=True), nullable=True)
    acknowledged = Column(Boolean,   nullable=False, default=False)
    escalated    = Column(Boolean,   nullable=False, default=False)
    dismissed    = Column(Boolean,   nullable=False, default=False)
    meta_data    = Column(JSONB,      nullable=True, default=dict)

    __table_args__ = (
        Index("ix_alerts_detected_severity", "detected_at", "severity"),
    )


class SignalModel(Base):
    """
    Normalised, embedded signals derived from raw events.
    Each VisionEvent produces one Signal with a 384-dim embedding vector
    used for semantic similarity search via pgvector.
    """
    __tablename__ = "signals"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    signal_id       = Column(String(128), unique=True, nullable=False, index=True)
    source_event_id = Column(String(128), nullable=False, index=True)
    source          = Column(String(64),  nullable=False, index=True)
    signal_type     = Column(String(32),  nullable=False, default="raw")
    title           = Column(Text,        nullable=False)
    body            = Column(Text,        nullable=True)
    content_hash    = Column(String(64),  nullable=True, index=True)
    embedding       = Column(Vector(384), nullable=True)
    timestamp       = Column(DateTime(timezone=True), nullable=True, index=True)
    actors          = Column(JSONB,       nullable=True, default=list)
    location_name   = Column(String(256), nullable=True)
    location_lat    = Column(Float,       nullable=True)
    location_lon    = Column(Float,       nullable=True)
    sentiment_score = Column(Float,       nullable=True)
    confidence      = Column(Float,       nullable=False, default=0.5)
    cluster_id      = Column(String(64),  nullable=True, index=True)
    meta            = Column(JSONB,       nullable=True, default=dict)

    __table_args__ = (
        Index("ix_signals_timestamp_source", "timestamp", "source"),
    )


class DecisionModel(Base):
    """
    Analyst decision records â€” captures approved Courses of Action from Operations.
    One row per execute action taken on a COA recommendation.
    Outcome field enables feedback loops back into risk scoring.
    """
    __tablename__ = "decisions"

    id            = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    event_id      = Column(String(256), nullable=False, index=True)
    coa_index     = Column(Integer, nullable=False)
    coa_text      = Column(Text, nullable=False)
    analyst       = Column(String(256), nullable=False, default="system")
    status        = Column(String(32), nullable=False, default="approved", index=True)
    rationale     = Column(Text, nullable=True)
    # Feedback loop: outcome recorded after execution
    outcome       = Column(String(32), nullable=True, index=True)   # effective|ineffective|inconclusive
    outcome_notes = Column(Text, nullable=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    __table_args__ = (
        Index("ix_decisions_event_id_created", "event_id", "created_at"),
    )


class PlaybookRunModel(Base):
    """
    Formal execution tracking for playbook runs.
    Represents the lifecycle of a recommended playbook from trigger to completion.
    This is a first-class ontology object in the Kinetic Layer.
    """
    __tablename__ = "playbook_runs"

    id             = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    event_id       = Column(String(256), nullable=False, index=True)
    playbook_name  = Column(String(256), nullable=False)
    objective      = Column(Text, nullable=True)
    trigger_reason = Column(Text, nullable=True)
    analyst        = Column(String(256), nullable=False, default="system")
    status         = Column(String(32), nullable=False, default="in_progress", index=True)
    # status: in_progress | completed | cancelled | escalated
    steps_total    = Column(Integer, nullable=False, default=0)
    steps_done     = Column(Integer, nullable=False, default=0)
    steps_state    = Column(JSONB, nullable=True, default=list)
    started_at     = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    completed_at   = Column(DateTime(timezone=True), nullable=True)
    outcome_summary = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_playbook_runs_event_status", "event_id", "status"),
    )


class SituationModel(Base):
    """
    A Situation is a cluster of â‰¥2 related events that share actors, geo proximity,
    or time window. Represents a higher-level intelligence object above raw events.
    """
    __tablename__ = "situations"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    situation_id  = Column(String(64), unique=True, nullable=False, index=True)
    title         = Column(String(512), nullable=False)
    description   = Column(Text, nullable=True)
    event_ids     = Column(JSONB, nullable=True, default=list)
    actor_ids     = Column(JSONB, nullable=True, default=list)
    risk_score    = Column(Float, nullable=False, default=0.0)
    severity      = Column(String(16), nullable=False, default="low", index=True)
    region        = Column(String(256), nullable=True)
    event_count   = Column(Integer, nullable=False, default=0)
    status        = Column(String(16), nullable=False, default="active", index=True)
    detected_at   = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at    = Column(DateTime(timezone=True), server_default=func.now())
    meta          = Column(JSONB, nullable=True, default=dict)

    __table_args__ = (
        Index("ix_situations_severity_status", "severity", "status"),
    )


class AssetModel(Base):
    """
    Tracked physical assets (aircraft, vessels, facilities).
    Flights are assets, NOT events â€” events are created only from anomalies.
    """
    __tablename__ = "assets"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    asset_id       = Column(String(128), unique=True, nullable=False, index=True)
    asset_type     = Column(String(32),  nullable=False, index=True)
    name           = Column(String(256), nullable=True)
    callsign       = Column(String(32),  nullable=True, index=True)
    identifier     = Column(String(64),  nullable=True, index=True)
    origin_country = Column(String(128), nullable=True)
    last_lat       = Column(Float,       nullable=True)
    last_lon       = Column(Float,       nullable=True)
    last_altitude  = Column(Float,       nullable=True)
    last_speed     = Column(Float,       nullable=True)
    last_heading   = Column(Float,       nullable=True)
    last_seen      = Column(DateTime(timezone=True), nullable=True, index=True)
    on_ground      = Column(Boolean,     nullable=True)
    track_history  = Column(JSONB,       nullable=True, default=list)
    meta           = Column(JSONB,       nullable=True, default=dict)

class WatchlistItemModel(Base):
    """
    Per-user watchlist of tracked entities/actors.
    Filters priority feed to show matching events first.
    """
    __tablename__ = "watchlist_items"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    item_id     = Column(String(64), unique=True, nullable=False, index=True)
    user_id     = Column(String(128), nullable=False, index=True)
    entity_name = Column(String(256), nullable=False)
    entity_type = Column(String(64),  nullable=True)
    notes       = Column(Text,        nullable=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    __table_args__ = (
        Index("ix_watchlist_user_entity", "user_id", "entity_name"),
    )


class AnnotationModel(Base):
    """Analyst comments/annotations attached to a specific event."""
    __tablename__ = "annotations"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    annotation_id = Column(String(64), unique=True, nullable=False, index=True)
    event_id   = Column(String(256), nullable=False, index=True)
    author     = Column(String(256), nullable=False)
    user_id    = Column(String(128), nullable=True, index=True)
    body       = Column(Text,        nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_annotations_event_created", "event_id", "created_at"),
    )


class BookmarkModel(Base):
    """User-pinned events for quick reference."""
    __tablename__ = "bookmarks"

    id          = Column(Integer,    primary_key=True, autoincrement=True)
    bookmark_id = Column(String(64), unique=True, nullable=False, index=True)
    user_id     = Column(String(128), nullable=False, index=True)
    event_id    = Column(String(256), nullable=False, index=True)
    note        = Column(Text,        nullable=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    __table_args__ = (
        Index("ix_bookmarks_user_event", "user_id", "event_id"),
    )


class AlertSubscriptionModel(Base):
    """Per-user alert notification subscriptions."""
    __tablename__ = "alert_subscriptions"

    id             = Column(Integer,    primary_key=True, autoincrement=True)
    subscription_id = Column(String(64), unique=True, nullable=False, index=True)
    user_id        = Column(String(128), nullable=False, index=True)
    severity       = Column(String(16),  nullable=True)   # null = all severities
    alert_type     = Column(String(64),  nullable=True)   # null = all types
    entity_filter  = Column(String(256), nullable=True)
    is_active      = Column(Boolean,     nullable=False, default=True)
    created_at     = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    __table_args__ = (
        Index("ix_alert_subs_user", "user_id", "is_active"),
    )


class AuditLogModel(Base):
    """Immutable security audit trail — one row per significant user action."""
    __tablename__ = "python_audit_log"

    id         = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id    = Column(String(128), nullable=True,  index=True)
    action     = Column(String(128), nullable=False, index=True)  # e.g. "watchlist.add"
    resource   = Column(String(256), nullable=True)               # e.g. "event:abc123"
    detail     = Column(Text,        nullable=True)               # optional JSON payload
    ip_address = Column(String(64),  nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    __table_args__ = (
        Index("ix_audit_log_user_action", "user_id", "action"),
    )


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Async context manager for database sessions.

    Usage in FastAPI routes:
        async with get_session() as session:
            result = await session.execute(...)

    Usage as FastAPI dependency (inject into router):
        async def my_route(session: AsyncSession = Depends(db_session)):
            ...
    """
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception as exc:
            await session.rollback()
            logger.error("DB session error: %s (%s)", exc, type(exc).__name__)
            raise


async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI Depends()-compatible session generator."""
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception as exc:
            await session.rollback()
            logger.error("DB session error: %s (%s)", exc, type(exc).__name__)
            raise

async def init_db() -> None:
    """Create all tables and extensions. Called once at application startup."""
    try:
        async def _column_exists(table: str, column: str) -> bool:
            async with async_engine.connect() as conn:
                result = await conn.execute(text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema = current_schema() "
                    "AND table_name = :table AND column_name = :column "
                    "LIMIT 1"
                ), {"table": table, "column": column})
                return result.scalar() is not None

        async def _index_exists(index_name: str) -> bool:
            async with async_engine.connect() as conn:
                result = await conn.execute(text(
                    "SELECT 1 FROM pg_indexes "
                    "WHERE schemaname = current_schema() AND indexname = :index_name "
                    "LIMIT 1"
                ), {"index_name": index_name})
                return result.scalar() is not None

        async with async_engine.begin() as conn:
            # Enable pgvector extension for semantic search
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
            # HNSW index for fast cosine similarity search on signal embeddings
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_signals_embedding "
                "ON signals USING hnsw (embedding vector_cosine_ops)"
            ))
        # create_all() never alters existing tables. Check the catalog first so
        # we only run repairs that are genuinely needed, instead of issuing
        # heavyweight ALTER TABLE statements on every process startup.
        repairs: list[str] = []

        if not await _column_exists("events", "influence_score"):
            repairs.append("ALTER TABLE events ADD COLUMN influence_score FLOAT")
        if not await _column_exists("events", "risk_score"):
            repairs.append("ALTER TABLE events ADD COLUMN risk_score FLOAT")
        if not await _index_exists("ix_events_risk_score"):
            repairs.append("CREATE INDEX ix_events_risk_score ON events (risk_score)")
        if not await _index_exists("ix_events_event_type_ingest_time"):
            repairs.append("CREATE INDEX ix_events_event_type_ingest_time ON events (event_type, ingest_time DESC)")
        if not await _index_exists("ix_situations_event_ids_gin"):
            repairs.append("CREATE INDEX ix_situations_event_ids_gin ON situations USING gin (event_ids)")
        if not await _column_exists("events", "mitre_tags"):
            repairs.append("ALTER TABLE events ADD COLUMN mitre_tags JSONB")
        if not await _column_exists("watchlist_items", "notes"):
            repairs.append("ALTER TABLE watchlist_items ADD COLUMN notes TEXT")
        if not await _index_exists("ix_python_audit_log_created"):
            repairs.append("CREATE INDEX ix_python_audit_log_created ON python_audit_log (created_at DESC)")
        if not await _column_exists("alerts", "escalated"):
            repairs.append("ALTER TABLE alerts ADD COLUMN escalated BOOLEAN NOT NULL DEFAULT FALSE")
        if not await _column_exists("alerts", "dismissed"):
            repairs.append("ALTER TABLE alerts ADD COLUMN dismissed BOOLEAN NOT NULL DEFAULT FALSE")
        if not await _column_exists("source_checkpoints", "credibility_score"):
            repairs.append("ALTER TABLE source_checkpoints ADD COLUMN credibility_score FLOAT")
        if not await _column_exists("source_checkpoints", "credibility_note"):
            repairs.append("ALTER TABLE source_checkpoints ADD COLUMN credibility_note TEXT")

        # Run best-effort schema repairs one statement at a time so a timeout or
        # benign failure does not abort the rest of the migration batch.
        for stmt in repairs:
            try:
                async with async_engine.begin() as conn:
                    await conn.execute(text(stmt))
            except Exception as m_exc:
                logger.warning("Migration skipped (%s): %s", stmt[:60], m_exc)

        logger.info("PostgreSQL tables initialised (pgvector enabled)")
    except Exception as exc:
        logger.error("PostgreSQL init failed: %s", exc)
        raise


async def close_db() -> None:
    """Dispose engine. Called at application shutdown."""
    await async_engine.dispose()
    logger.info("PostgreSQL connection pool closed")
