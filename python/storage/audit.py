"""
storage/audit.py
----------------
Thin helper for writing immutable audit log entries.

Usage in FastAPI routes:
    from storage.audit import log_audit
    await log_audit(session, user_id="u123", action="watchlist.add", resource="entity:APT28")
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from storage.database import AuditLogModel

logger = logging.getLogger("vision_i.storage.audit")


async def log_audit(
    session: AsyncSession,
    action: str,
    *,
    user_id: Optional[str] = None,
    resource: Optional[str] = None,
    detail: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> None:
    """
    Append one audit record. Fire-and-forget: errors are logged but not raised
    so a broken audit write never takes down the main request.
    """
    try:
        entry = AuditLogModel(
            user_id    = user_id,
            action     = action,
            resource   = resource,
            detail     = detail,
            ip_address = ip_address,
        )
        session.add(entry)
        # Caller's session.commit() (or db_session context manager) persists the row.
    except Exception as exc:
        logger.error("audit write failed: %s", exc)
