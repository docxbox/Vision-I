"""
api/routers/bookmarks.py
─────────────────────────
User event bookmarks (pins).

GET    /bookmarks          — list user's bookmarks
POST   /bookmarks          — add bookmark
DELETE /bookmarks/{id}     — remove bookmark
"""

import logging
import uuid
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

logger = logging.getLogger("vision_i.api.bookmarks")
router = APIRouter(tags=["bookmarks"])


class BookmarkIn(BaseModel):
    event_id: str
    note: Optional[str] = None


class BookmarkOut(BaseModel):
    bookmark_id: str
    event_id:    str
    note:        Optional[str] = None
    created_at:  Optional[str] = None

    class Config:
        extra = "allow"


@router.get("", response_model=List[BookmarkOut])
async def list_bookmarks(
    request: Request,
    user_id: str = Query(...),
    limit:   int = Query(100, ge=1, le=500),
):
    if not request.app.state.db_available:
        return []
    try:
        from sqlalchemy import select, desc
        from storage.database import BookmarkModel, get_session
        async with get_session() as session:
            rows = (await session.execute(
                select(BookmarkModel)
                .where(BookmarkModel.user_id == user_id)
                .order_by(desc(BookmarkModel.created_at))
                .limit(limit)
            )).scalars().all()
        return [
            {
                "bookmark_id": r.bookmark_id,
                "event_id":    r.event_id,
                "note":        r.note,
                "created_at":  r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    except Exception as exc:
        logger.error("list_bookmarks failed: %s", exc)
        return []


@router.post("", response_model=BookmarkOut, status_code=201)
async def add_bookmark(request: Request, body: BookmarkIn, user_id: str = Query(...)):
    if not request.app.state.db_available:
        raise HTTPException(status_code=503, detail="Database unavailable")
    try:
        from sqlalchemy import select
        from storage.database import BookmarkModel, get_session
        async with get_session() as session:
            existing = (await session.execute(
                select(BookmarkModel)
                .where(BookmarkModel.user_id == user_id, BookmarkModel.event_id == body.event_id)
            )).scalar_one_or_none()
            if existing:
                return {
                    "bookmark_id": existing.bookmark_id,
                    "event_id":    existing.event_id,
                    "note":        existing.note,
                    "created_at":  existing.created_at.isoformat() if existing.created_at else None,
                }
            bm = BookmarkModel(
                bookmark_id=str(uuid.uuid4())[:16],
                user_id=user_id,
                event_id=body.event_id,
                note=body.note,
            )
            session.add(bm)
            await session.flush()
            return {
                "bookmark_id": bm.bookmark_id,
                "event_id":    bm.event_id,
                "note":        bm.note,
                "created_at":  bm.created_at.isoformat() if bm.created_at else None,
            }
    except Exception as exc:
        logger.error("add_bookmark failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/{bookmark_id}", status_code=204)
async def remove_bookmark(bookmark_id: str, request: Request, user_id: str = Query(...)):
    if not request.app.state.db_available:
        return
    try:
        from sqlalchemy import delete
        from storage.database import BookmarkModel, get_session
        async with get_session() as session:
            await session.execute(
                delete(BookmarkModel)
                .where(BookmarkModel.bookmark_id == bookmark_id, BookmarkModel.user_id == user_id)
            )
    except Exception as exc:
        logger.error("remove_bookmark failed: %s", exc)
