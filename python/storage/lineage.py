"""
storage/lineage.py
───────────────────
Data lineage tracking for the Vision-I pipeline.

Records the flow of data through pipeline stages so that every event
can be traced back to its source batch and processing steps.

Usage:
    from storage.lineage import LineageTracker
    tracker = LineageTracker(session)
    batch_id = await tracker.record_stage("raw_ingest", event_count=42, source="gdelt")
    await tracker.record_stage("nlp_enriched", event_count=42, parent_batch=batch_id)
"""

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.database import DataLineageModel

logger = logging.getLogger("vision_i.lineage")


class LineageTracker:
    """Records pipeline lineage stages to the data_lineage table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def new_batch_id(self) -> str:
        """Generate a unique batch ID."""
        return str(uuid.uuid4())[:12]

    async def record_stage(
        self,
        stage: str,
        event_count: int,
        batch_id: Optional[str] = None,
        source: Optional[str] = None,
        parent_batch: Optional[str] = None,
        started_at: Optional[datetime] = None,
        event_ids: Optional[List[str]] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Record a pipeline stage execution.

        Returns the batch_id (generated if not provided).
        """
        if batch_id is None:
            batch_id = self.new_batch_id()

        now = datetime.now(timezone.utc)
        checksum = None
        if event_ids:
            sorted_ids = sorted(event_ids)
            checksum = hashlib.sha256(
                ",".join(sorted_ids).encode()
            ).hexdigest()[:16]

        row = DataLineageModel(
            batch_id=batch_id,
            source=source,
            stage=stage,
            event_count=event_count,
            started_at=started_at or now,
            finished_at=now,
            parent_batch=parent_batch,
            checksum=checksum,
            meta=meta or {},
        )
        self._session.add(row)
        await self._session.flush()

        logger.info(
            "Lineage: stage=%s batch=%s events=%d parent=%s",
            stage, batch_id, event_count, parent_batch,
        )
        return batch_id

    async def get_chain(self, batch_id: str) -> List[Dict[str, Any]]:
        """Get the full lineage chain for a batch_id (follows parent_batch links)."""
        chain = []
        current = batch_id
        for _ in range(10):  # max depth safety
            result = await self._session.execute(
                select(DataLineageModel)
                .where(DataLineageModel.batch_id == current)
                .order_by(DataLineageModel.id)
            )
            rows = result.scalars().all()
            if not rows:
                break
            for row in rows:
                chain.append({
                    "batch_id": row.batch_id,
                    "stage": row.stage,
                    "source": row.source,
                    "event_count": row.event_count,
                    "started_at": row.started_at.isoformat() if row.started_at else None,
                    "finished_at": row.finished_at.isoformat() if row.finished_at else None,
                    "parent_batch": row.parent_batch,
                    "checksum": row.checksum,
                })
            # Follow parent
            parent = rows[0].parent_batch
            if parent and parent != current:
                current = parent
            else:
                break
        return chain
