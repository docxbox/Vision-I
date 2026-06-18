"""
api/routers/watchlist.py
------------------------
Per-user watchlist of tracked entities.
Persists to the watchlist_items table.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from storage.database import WatchlistItemModel, get_session
from storage.audit import log_audit
from sqlalchemy import delete, select

logger = logging.getLogger("vision_i.api.watchlist")
router = APIRouter(tags=["Watchlist"])


class WatchlistItemIn(BaseModel):
    entity_name: str
    entity_type: Optional[str] = None
    notes: Optional[str] = None


class WatchlistItemOut(BaseModel):
    model_config = ConfigDict(extra="allow")
    item_id: str
    user_id: str
    entity_name: str
    entity_type: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[str] = None


class WatchlistResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    items: List[WatchlistItemOut] = Field(default_factory=list)
    total: int = 0


def _user_id(request: Request) -> str:
    user = getattr(request.state, "user", None)
    if user and hasattr(user, "sub"):
        return user.sub
    return request.headers.get("X-User-Id", "anonymous")


@router.get("/", response_model=WatchlistResponse, summary="List watchlist items for current user")
async def list_watchlist(request: Request):
    uid = _user_id(request)
    async with get_session() as session:
        result = await session.execute(
            select(WatchlistItemModel)
            .where(WatchlistItemModel.user_id == uid)
            .order_by(WatchlistItemModel.created_at.desc())
        )
        rows = result.scalars().all()
    items = [
        WatchlistItemOut(
            item_id=r.item_id,
            user_id=r.user_id,
            entity_name=r.entity_name,
            entity_type=r.entity_type,
            notes=r.notes,
            created_at=r.created_at.isoformat() if r.created_at else None,
        )
        for r in rows
    ]
    return WatchlistResponse(items=items, total=len(items))


@router.post("/", response_model=WatchlistItemOut, status_code=201, summary="Add entity to watchlist")
async def add_to_watchlist(request: Request, body: WatchlistItemIn):
    uid = _user_id(request)
    item_id = str(uuid.uuid4())
    async with get_session() as session:
        # Prevent duplicate entity per user
        existing = await session.execute(
            select(WatchlistItemModel)
            .where(
                WatchlistItemModel.user_id == uid,
                WatchlistItemModel.entity_name == body.entity_name,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Entity already in watchlist")

        row = WatchlistItemModel(
            item_id=item_id,
            user_id=uid,
            entity_name=body.entity_name,
            entity_type=body.entity_type,
            notes=body.notes,
        )
        session.add(row)
        await session.flush()
        created_at = row.created_at
        await log_audit(session, "watchlist.add", user_id=uid, resource=f"entity:{body.entity_name}")

    return WatchlistItemOut(
        item_id=item_id,
        user_id=uid,
        entity_name=body.entity_name,
        entity_type=body.entity_type,
        notes=body.notes,
        created_at=created_at.isoformat() if created_at else None,
    )


@router.delete("/{item_id}", status_code=204, summary="Remove item from watchlist")
async def remove_from_watchlist(request: Request, item_id: str):
    uid = _user_id(request)
    async with get_session() as session:
        result = await session.execute(
            delete(WatchlistItemModel)
            .where(
                WatchlistItemModel.item_id == item_id,
                WatchlistItemModel.user_id == uid,
            )
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Watchlist item not found")
        await log_audit(session, "watchlist.remove", user_id=uid, resource=f"watchlist_item:{item_id}")
