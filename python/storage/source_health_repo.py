"""
storage/source_health_repo.py
──────────────────────────────
Simple repository for the source_health table.

The table is created on first write/read so no migration is needed.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import text

logger = logging.getLogger("vision_i.storage.source_health_repo")

_DDL = """
CREATE TABLE IF NOT EXISTS source_health (
    source_name      VARCHAR(128) PRIMARY KEY,
    status           VARCHAR(32)  NOT NULL DEFAULT 'unknown',
    last_success     TIMESTAMPTZ,
    last_failure     TIMESTAMPTZ,
    records_ingested INT          NOT NULL DEFAULT 0,
    error_summary    TEXT,
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
)
"""


async def _ensure_table(session) -> None:
    """Create the table if it doesn't already exist."""
    await session.execute(text(_DDL))


async def upsert_source_health(
    session,
    source_name: str,
    status: str,
    last_success: Optional[datetime] = None,
    last_failure: Optional[datetime] = None,
    records_ingested: int = 0,
    error_summary: Optional[str] = None,
) -> None:
    """
    Insert or update a row in source_health.
    Creates the table on the first call if it doesn't exist.
    """
    try:
        await _ensure_table(session)
        await session.execute(
            text("""
                INSERT INTO source_health
                    (source_name, status, last_success, last_failure,
                     records_ingested, error_summary, updated_at)
                VALUES
                    (:source_name, :status, :last_success, :last_failure,
                     :records_ingested, :error_summary, NOW())
                ON CONFLICT (source_name) DO UPDATE SET
                    status           = EXCLUDED.status,
                    last_success     = COALESCE(EXCLUDED.last_success,     source_health.last_success),
                    last_failure     = COALESCE(EXCLUDED.last_failure,     source_health.last_failure),
                    records_ingested = EXCLUDED.records_ingested,
                    error_summary    = EXCLUDED.error_summary,
                    updated_at       = NOW()
            """),
            {
                "source_name":      source_name,
                "status":           status,
                "last_success":     last_success,
                "last_failure":     last_failure,
                "records_ingested": records_ingested,
                "error_summary":    error_summary,
            },
        )
        await session.flush()
    except Exception as exc:
        logger.error("upsert_source_health failed for %s: %s", source_name, exc)
        try:
            await session.rollback()
        except Exception:
            pass


async def get_all_source_health(session) -> List[Dict[str, Any]]:
    """
    Return all rows from source_health as a list of dicts.
    Creates the table if it doesn't exist.
    Returns an empty list on any error.
    """
    try:
        await _ensure_table(session)
        result = await session.execute(
            text("""
                SELECT
                    source_name,
                    status,
                    last_success,
                    last_failure,
                    records_ingested,
                    error_summary,
                    updated_at
                FROM source_health
                ORDER BY updated_at DESC
            """)
        )
        rows = result.mappings().all()
        return [
            {
                "source_name":      r["source_name"],
                "status":           r["status"],
                "last_success":     r["last_success"].isoformat() if r["last_success"] else None,
                "last_failure":     r["last_failure"].isoformat() if r["last_failure"] else None,
                "records_ingested": r["records_ingested"],
                "error_summary":    r["error_summary"],
                "updated_at":       r["updated_at"].isoformat() if r["updated_at"] else None,
            }
            for r in rows
        ]
    except Exception as exc:
        logger.error("get_all_source_health failed: %s", exc)
        return []
