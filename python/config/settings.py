"""Central application settings."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from dotenv import load_dotenv

# Resolve paths relative to this file so startup location does not matter.
_this_file   = Path(__file__).resolve()          # python/config/settings.py
_python_dir  = _this_file.parent.parent           # python/
_project_dir = _python_dir.parent                 # Vision-I/

# Load python/.env first, then fall back to the project root .env file.
load_dotenv(_python_dir  / ".env", override=False)
load_dotenv(_project_dir / ".env", override=False)


@dataclass(frozen=True)
class Settings:
    internal_api_key: str = field(
        default_factory=lambda: os.getenv("INTERNAL_API_KEY", "")
    )
    allowed_origins: List[str] = field(
        default_factory=lambda: os.getenv(
            "ALLOWED_ORIGINS", "http://localhost:5000"
        ).split(",")
    )
    newsapi_key: str = field(
        default_factory=lambda: os.getenv("NEWSAPI_KEY", "")
    )
    opensky_user: str = field(
        default_factory=lambda: os.getenv("OPENSKY_USER", "")
    )
    opensky_pass: str = field(
        default_factory=lambda: os.getenv("OPENSKY_PASS", "")
    )

    # Optional airspace and satellite feeds.
    notam_feed_path: str = field(
        default_factory=lambda: os.getenv("NOTAM_FEED_PATH", "")
    )
    notam_api_url: str = field(
        default_factory=lambda: os.getenv("NOTAM_API_URL", "")
    )
    notam_api_key: str = field(
        default_factory=lambda: os.getenv("NOTAM_API_KEY", "")
    )
    notam_api_header: str = field(
        default_factory=lambda: os.getenv("NOTAM_API_HEADER", "X-API-Key")
    )
    sat_tle_path: str = field(
        default_factory=lambda: os.getenv("SAT_TLE_PATH", "")
    )
    sat_tle_text: str = field(
        default_factory=lambda: os.getenv("SAT_TLE_TEXT", "")
    )
    sat_tle_url: str = field(
        default_factory=lambda: os.getenv("SAT_TLE_URL", "")
    )
    sat_tle_cache_ttl_hours: int = field(
        default_factory=lambda: int(os.getenv("SAT_TLE_CACHE_TTL_HOURS", "12"))
    )
    pipeline_workers: int = field(
        default_factory=lambda: int(os.getenv("PIPELINE_WORKERS", "4"))
    )
    social_enrich_enabled: bool = field(
        default_factory=lambda: os.getenv("SOCIAL_ENRICH_ENABLED", "true").lower() == "true"
    )
    social_enrich_window_hours: int = field(
        default_factory=lambda: int(os.getenv("SOCIAL_ENRICH_WINDOW_HOURS", "6"))
    )
    social_enrich_max_events: int = field(
        default_factory=lambda: int(os.getenv("SOCIAL_ENRICH_MAX_EVENTS", "6"))
    )
    social_enrich_limit_per_event: int = field(
        default_factory=lambda: int(os.getenv("SOCIAL_ENRICH_LIMIT_PER_EVENT", "25"))
    )
    social_enrich_min_score: float = field(
        default_factory=lambda: float(os.getenv("SOCIAL_ENRICH_MIN_SCORE", "2.0"))
    )
    social_enrich_cooldown_minutes: int = field(
        default_factory=lambda: int(os.getenv("SOCIAL_ENRICH_COOLDOWN_MINUTES", "45"))
    )
    social_enrich_fallback_socials: bool = field(
        default_factory=lambda: os.getenv("SOCIAL_ENRICH_FALLBACK_SOCIALS", "true").lower() == "true"
    )
    postgres_dsn: str = field(
        default_factory=lambda: os.getenv(
            "POSTGRES_DSN",
            "postgresql+asyncpg://vision:vision@localhost:5432/vision_i",
        )
    )
    # Sync DSN for Alembic migrations.
    postgres_dsn_sync: str = field(
        default_factory=lambda: os.getenv(
            "POSTGRES_DSN_SYNC",
            "postgresql+psycopg2://vision:vision@localhost:5432/vision_i",
        )
    )
    neo4j_uri: str = field(
        default_factory=lambda: os.getenv("NEO4J_URI", "bolt://localhost:7687")
    )
    neo4j_user: str = field(
        default_factory=lambda: os.getenv("NEO4J_USER", "neo4j")
    )
    neo4j_pass: str = field(
        default_factory=lambda: os.getenv("NEO4J_PASS", "neo4j")
    )
    live_interval_seconds: int = field(
        default_factory=lambda: int(os.getenv("LIVE_INTERVAL_SECONDS", "300"))   # 5 minutes
    )
    text_interval_seconds: int = field(
        default_factory=lambda: int(os.getenv("TEXT_INTERVAL_SECONDS", "300"))   # 5 minutes
    )
    narrative_interval_seconds: int = field(
        default_factory=lambda: int(os.getenv("NARRATIVE_INTERVAL_SECONDS", "1800"))  # 30 minutes
    )
    anomaly_interval_seconds: int = field(
        default_factory=lambda: int(os.getenv("ANOMALY_INTERVAL_SECONDS", "3600"))   # 60 minutes
    )
    influence_interval_seconds: int = field(
        default_factory=lambda: int(os.getenv("INFLUENCE_INTERVAL_SECONDS", "21600"))  # 6 hours
    )
    telegram_api_id: str = field(
        default_factory=lambda: os.getenv("TELEGRAM_API_ID", "")
    )
    telegram_api_hash: str = field(
        default_factory=lambda: os.getenv("TELEGRAM_API_HASH", "")
    )
    telegram_bot_token: str = field(
        default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", "")
    )
    telegram_channels: str = field(
        default_factory=lambda: os.getenv("TELEGRAM_CHANNELS", "")
    )
    narrative_window_hours: int = field(
        default_factory=lambda: int(os.getenv("NARRATIVE_WINDOW_HOURS", "6"))
    )
    anomaly_z_threshold: float = field(
        default_factory=lambda: float(os.getenv("ANOMALY_Z_THRESHOLD", "2.0"))
    )
    alert_cooldown_hours: int = field(
        default_factory=lambda: int(os.getenv("ALERT_COOLDOWN_HOURS", "4"))
    )
    # Primary multilingual NER model.
    ner_model: str = field(
        default_factory=lambda: os.getenv(
            "NER_MODEL",
            "Davlan/xlm-roberta-base-ner-hrl",
        )
    )
    # English fallback when transformer NER is unavailable.
    spacy_model: str = field(
        default_factory=lambda: os.getenv("SPACY_MODEL", "en_core_web_sm")
    )
    # Primary multilingual sentiment model.
    sentiment_model: str = field(
        default_factory=lambda: os.getenv(
            "SENTIMENT_MODEL",
            "cardiffnlp/twitter-xlm-roberta-base-sentiment",
        )
    )
    # English fallback sentiment model.
    sentiment_model_fallback: str = field(
        default_factory=lambda: os.getenv(
            "SENTIMENT_MODEL_FALLBACK",
            "cardiffnlp/twitter-roberta-base-sentiment-latest",
        )
    )
    nlp_batch_size: int = field(
        default_factory=lambda: int(os.getenv("NLP_BATCH_SIZE", "32"))
    )
    # Entity resolution fuzzy match threshold.
    entity_resolution_threshold: int = field(
        default_factory=lambda: int(os.getenv("ENTITY_RESOLUTION_THRESHOLD", "88"))
    )
    redis_url: str = field(
        default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379/0")
    )
    db_statement_timeout_ms: int = field(
        default_factory=lambda: int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "15000"))
    )
    ais_api_url: str = field(
        default_factory=lambda: os.getenv("AIS_API_URL", "")
    )
    ais_api_key: str = field(
        default_factory=lambda: os.getenv("AIS_API_KEY", "")
    )
    ais_provider: str = field(
        default_factory=lambda: os.getenv("AIS_PROVIDER", "generic")
    )
    aisstream_api_key: str = field(
        default_factory=lambda: os.getenv("AISSTREAM_API_KEY", "")
    )
    embedding_model: str = field(
        default_factory=lambda: os.getenv(
            "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
        )
    )
    embedding_dim: int = 384
    correlation_time_window_hours: int = field(
        default_factory=lambda: int(os.getenv("CORRELATION_WINDOW_HOURS", "6"))
    )
    correlation_similarity_threshold: float = field(
        default_factory=lambda: float(os.getenv("CORRELATION_SIMILARITY", "0.75"))
    )
    correlation_min_signals: int = field(
        default_factory=lambda: int(os.getenv("CORRELATION_MIN_SIGNALS", "3"))
    )
    correlation_min_sources: int = field(
        default_factory=lambda: int(os.getenv("CORRELATION_MIN_SOURCES", "2"))
    )
    nasa_firms_key: str = field(
        default_factory=lambda: os.getenv("NASA_FIRMS_KEY", "")
    )
    circuit_breaker_threshold: int = field(
        default_factory=lambda: int(os.getenv("CIRCUIT_BREAKER_THRESHOLD", "3"))
    )
    circuit_breaker_cooldown: int = field(
        default_factory=lambda: int(os.getenv("CIRCUIT_BREAKER_COOLDOWN", "300"))
    )
    log_level: str = field(
        default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper()
    )
    log_json: bool = field(
        default_factory=lambda: os.getenv("LOG_JSON", "false").lower() == "true"
    )
    app_env: str = field(
        default_factory=lambda: os.getenv("APP_ENV", "development")
    )
    run_scheduler: bool = field(
        default_factory=lambda: os.getenv("RUN_SCHEDULER", "true").lower() == "true"
    )
    run_pipeline_worker: bool = field(
        default_factory=lambda: os.getenv("RUN_PIPELINE_WORKER", "true").lower() == "true"
    )
    load_embedder_on_startup: bool = field(
        default_factory=lambda: os.getenv("LOAD_EMBEDDER_ON_STARTUP", "true").lower() == "true"
    )
    bootstrap_live_on_startup: bool = field(
        default_factory=lambda: os.getenv("BOOTSTRAP_LIVE_ON_STARTUP", "true").lower() == "true"
    )
    default_tracked_queries_raw: str = field(
        default_factory=lambda: os.getenv(
            "DEFAULT_TRACKED_QUERIES",
            # Semicolon-separated default tracked queries.
            "war conflict military;"
            "geopolitics sanctions;"
            "Ukraine Russia;"
            "Middle East Iran Israel;"
            "China Taiwan South China Sea;"
            "cyber attack hack;"
            "terrorism extremism;"
            "elections protest;"
            "earthquake disaster;"
            "nuclear missile drone"
        )
    )
    classification_banner: str = field(
        default_factory=lambda: os.getenv("VISION_CLASSIFICATION", "UNCLASSIFIED // FOR OFFICIAL USE ONLY")
    )
    classification_color: str = field(
        default_factory=lambda: os.getenv("VISION_CLASSIFICATION_COLOR", "#15B371")
    )

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def default_tracked_queries(self) -> List[str]:
        raw = self.default_tracked_queries_raw.replace("\n", ";")
        return [item.strip() for item in raw.split(";") if item.strip()]

    @property
    def db_available(self) -> bool:
        """Return true when Postgres is configured through environment variables."""
        return os.getenv("POSTGRES_DSN") is not None or os.getenv("POSTGRES_PASSWORD") is not None

    @property
    def neo4j_available(self) -> bool:
        return os.getenv("NEO4J_URI") is not None

# Shared settings instance.
settings = Settings()




