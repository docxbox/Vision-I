"""
storage/intelligence_repo.py
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Repository for narratives and alerts â€” the intelligence layer's persistence.

Provides clean read/write abstractions over NarrativeModel and AlertModel.
All SQL is confined to this file.
"""

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from sqlalchemy import and_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from intelligence.narrative_detector import NarrativeSignal
from intelligence.anomaly_detector   import AnomalyAlert
from storage.database                import AlertModel, NarrativeModel

logger = logging.getLogger("vision_i.storage.intelligence_repo")

_HIGH_SEVERITIES = {"high", "critical"}


async def _fire_alert_webhook(alerts: List["AnomalyAlert"]) -> None:
    url = os.getenv("ALERT_WEBHOOK_URL", "").strip()
    if not url:
        return
    high = [a for a in alerts if (a.severity or "").lower() in _HIGH_SEVERITIES]
    if not high:
        return
    payload = {
        "source": "vision-i",
        "alerts": [
            {
                "title": a.title,
                "severity": a.severity,
                "entity": a.entity,
                "alert_type": a.alert_type,
                "z_score": a.z_score,
                "detected_at": a.detected_at.isoformat(),
            }
            for a in high
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload,
                                     headers={"Content-Type": "application/json"})
            logger.info("Webhook fired → %s  status=%d  alerts=%d", url, resp.status_code, len(high))
    except Exception as exc:
        logger.warning("Alert webhook failed: %s", exc)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

class NarrativeRepository:

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_signals(self, signals: List[NarrativeSignal]) -> int:
        """
        Persist narrative signals.
        Uses narrative_id as a deterministic dedup key.
        """
        if not signals:
            return 0

        rows = []
        for sig in signals:
            window_str = sig.window_start.strftime("%Y%m%d%H") if sig.window_start else "unknown"

            nid = str(uuid.uuid5(
                uuid.NAMESPACE_DNS,
                f"{sig.topic}::{sig.signal_type}::{window_str}"
            ))

            # Stash geographic_spread inside meta_data (no schema migration needed)
            meta = dict(sig.metadata or {})
            if getattr(sig, "geographic_spread", None):
                meta["geographic_spread"] = sig.geographic_spread

            rows.append({
                "narrative_id":  nid,
                "signal_type":   sig.signal_type,
                "topic":         sig.topic,
                "strength":      sig.strength,
                "confidence":    sig.confidence,
                "severity":      sig.severity,
                "event_count":   sig.event_count,
                "source_count":  sig.source_count,
                "sources":       sig.sources,
                "actors":        sig.actors,
                "sample_titles": sig.sample_titles,
                "window_start":  sig.window_start,
                "window_end":    sig.window_end,
                "detected_at":   sig.detected_at,
                "meta_data":     meta,

                "status":        "active",
            })

        stmt = pg_insert(NarrativeModel).values(rows)

        stmt = stmt.on_conflict_do_update(
            index_elements=["narrative_id"],
            set_={
                "strength":      stmt.excluded.strength,
                "confidence":    stmt.excluded.confidence,
                "severity":      stmt.excluded.severity,
                "event_count":   stmt.excluded.event_count,
                "source_count":  stmt.excluded.source_count,
                "sources":       stmt.excluded.sources,
                "actors":        stmt.excluded.actors,
                "sample_titles": stmt.excluded.sample_titles,
                "meta_data":     stmt.excluded.meta_data,

                "status":        stmt.excluded.status,
            },
        )

        result = await self._session.execute(stmt)
        return result.rowcount

    async def list_narratives(
        self,
        signal_type: Optional[str] = None,
        severity:    Optional[str] = None,
        status:      Optional[str] = "active",
        from_time:   Optional[str] = None,
        limit:       int = 50,
        offset:      int = 0,
    ) -> Tuple[int, List[Dict]]:

        conditions = []

        if signal_type:
            conditions.append(NarrativeModel.signal_type == signal_type)

        if severity:
            conditions.append(NarrativeModel.severity == severity)

        if status:
            conditions.append(NarrativeModel.status == status)

        if from_time:
            try:
                dt = datetime.fromisoformat(from_time.replace("Z", "+00:00"))
                conditions.append(NarrativeModel.detected_at >= dt)
            except ValueError:
                pass

        where = and_(*conditions) if conditions else True

        from sqlalchemy import func

        total = (
            await self._session.execute(
                select(func.count()).select_from(NarrativeModel).where(where)
            )
        ).scalar_one()

        rows = (
            await self._session.execute(
                select(NarrativeModel)
                .where(where)
                .order_by(NarrativeModel.detected_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).scalars().all()

        return total, [_narrative_to_dict(r) for r in rows]

class AlertRepository:

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_alerts(self, alerts: List[AnomalyAlert]) -> int:
        if not alerts:
            return 0

        rows_by_id: Dict[str, Dict[str, Any]] = {}
        for alert in alerts:
            hour_str = alert.detected_at.strftime("%Y%m%d%H")

            aid = str(uuid.uuid5(
                uuid.NAMESPACE_DNS,
                f"{alert.entity or 'none'}::{alert.alert_type}::{hour_str}"
            ))

            row = {
                "alert_id":    aid,
                "alert_type":  alert.alert_type,
                "severity":    alert.severity,
                "title":       alert.title,
                "description": alert.description,
                "entity":      alert.entity,
                "entity_type": alert.entity_type,
                "event_count": alert.event_count,
                "baseline":    alert.baseline,
                "z_score":     alert.z_score,
                "sources":     alert.sources,
                "location":    alert.location,
                "detected_at": alert.detected_at,
                "meta_data":   alert.metadata,

                "acknowledged": False,
            }

            existing = rows_by_id.get(aid)
            if not existing:
                rows_by_id[aid] = row
                continue

            if (
                (row.get("event_count") or 0) > (existing.get("event_count") or 0)
                or (row.get("z_score") or 0) > (existing.get("z_score") or 0)
            ):
                rows_by_id[aid] = row

        rows = list(rows_by_id.values())

        stmt = pg_insert(AlertModel).values(rows)

        stmt = stmt.on_conflict_do_update(
            index_elements=["alert_id"],
            set_={
                "severity":    stmt.excluded.severity,
                "event_count": stmt.excluded.event_count,
                "z_score":     stmt.excluded.z_score,
                "meta_data":   stmt.excluded.meta_data,
            },
        )

        result = await self._session.execute(stmt)
        rowcount = result.rowcount

        # Fire webhook for high/critical alerts (non-blocking)
        asyncio.ensure_future(_fire_alert_webhook(alerts))

        return rowcount

    async def list_alerts(
        self,
        alert_type:   Optional[str] = None,
        severity:     Optional[str] = None,
        acknowledged: Optional[bool] = None,
        from_time:    Optional[str] = None,
        limit:        int = 50,
        offset:       int = 0,
    ) -> Tuple[int, List[Dict]]:

        conditions = []

        if alert_type:
            conditions.append(AlertModel.alert_type == alert_type)

        if severity:
            conditions.append(AlertModel.severity == severity)

        if acknowledged is not None:
            conditions.append(AlertModel.acknowledged == acknowledged)

        if from_time:
            try:
                dt = datetime.fromisoformat(from_time.replace("Z", "+00:00"))
                conditions.append(AlertModel.detected_at >= dt)
            except ValueError:
                pass

        where = and_(*conditions) if conditions else True

        from sqlalchemy import func

        total = (
            await self._session.execute(
                select(func.count()).select_from(AlertModel).where(where)
            )
        ).scalar_one()

        rows = (
            await self._session.execute(
                select(AlertModel)
                .where(where)
                .order_by(AlertModel.detected_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).scalars().all()

        return total, [_alert_to_dict(r) for r in rows]

    async def acknowledge(self, alert_id: str) -> bool:
        result = await self._session.execute(
            update(AlertModel)
            .where(AlertModel.alert_id == alert_id)
            .values(acknowledged=True)
        )
        return result.rowcount > 0

    async def resolve(self, alert_id: str) -> bool:
        result = await self._session.execute(
            update(AlertModel)
            .where(AlertModel.alert_id == alert_id)
            .values(resolved_at=_utcnow())
        )
        return result.rowcount > 0

    async def get_unacknowledged_count(self) -> int:
        from sqlalchemy import func

        count = (
            await self._session.execute(
                select(func.count())
                .select_from(AlertModel)
                .where(AlertModel.acknowledged == False)  # noqa
            )
        ).scalar_one()

        return count

def _narrative_to_dict(row: NarrativeModel) -> Dict[str, Any]:
    meta = row.meta_data or {}
    spread = meta.get("geographic_spread") or {}
    return {
        "narrative_id":      row.narrative_id,
        "signal_type":       row.signal_type,
        "topic":             row.topic,
        "strength":          row.strength,
        "confidence":        row.confidence,
        "severity":          row.severity,
        "event_count":       row.event_count,
        "source_count":      row.source_count,
        "sources":           row.sources or [],
        "actors":            row.actors or [],
        "sample_titles":     row.sample_titles or [],
        "window_start":      row.window_start.isoformat() + "Z" if row.window_start else None,
        "window_end":        row.window_end.isoformat() + "Z" if row.window_end else None,
        "detected_at":       row.detected_at.isoformat() + "Z" if row.detected_at else None,
        "metadata":          meta,

        "geographic_spread": spread,
        "status":            row.status,
    }


def _alert_to_dict(row: AlertModel) -> Dict[str, Any]:
    return {
        "alert_id":    row.alert_id,
        "alert_type":  row.alert_type,
        "severity":    row.severity,
        "title":       row.title,
        "description": row.description,
        "entity":      row.entity,
        "entity_type": row.entity_type,
        "event_count": row.event_count,
        "baseline":    row.baseline,
        "z_score":     row.z_score,
        "sources":     row.sources or [],
        "location":    row.location,
        "detected_at": row.detected_at.isoformat() + "Z" if row.detected_at else None,
        "resolved_at": row.resolved_at.isoformat() + "Z" if row.resolved_at else None,
        "acknowledged": row.acknowledged,
        "metadata":    row.meta_data or {},
    }

