"""
api/routers/annotations.py
--------------------------
Analyst annotations (comments) on events.
"""
from __future__ import annotations

import logging
import uuid
from datetime import timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from storage.database import AnnotationModel, get_session
from storage.audit import log_audit
from sqlalchemy import delete, select

logger = logging.getLogger("vision_i.api.annotations")
router = APIRouter(tags=["Annotations"])


class AnnotationIn(BaseModel):
    body: str


class AnnotationOut(BaseModel):
    model_config = ConfigDict(extra="allow")
    annotation_id: str
    event_id: str
    author: str
    body: str
    created_at: Optional[str] = None


class AnnotationsResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    annotations: List[AnnotationOut] = Field(default_factory=list)
    total: int = 0


def _author(request: Request) -> str:
    user = getattr(request.state, "user", None)
    if user and hasattr(user, "name"):
        return user.name or "analyst"
    return request.headers.get("X-User-Name", "analyst")


@router.get("/{event_id}/annotations", response_model=AnnotationsResponse,
            summary="List annotations for an event")
async def list_annotations(event_id: str):
    async with get_session() as session:
        result = await session.execute(
            select(AnnotationModel)
            .where(AnnotationModel.event_id == event_id)
            .order_by(AnnotationModel.created_at.asc())
        )
        rows = result.scalars().all()
    annotations = [
        AnnotationOut(
            annotation_id=r.annotation_id,
            event_id=r.event_id,
            author=r.author,
            body=r.body,
            created_at=r.created_at.isoformat() if r.created_at else None,
        )
        for r in rows
    ]
    return AnnotationsResponse(annotations=annotations, total=len(annotations))


@router.post("/{event_id}/annotations", response_model=AnnotationOut, status_code=201,
             summary="Add annotation to an event")
async def add_annotation(event_id: str, request: Request, body: AnnotationIn):
    if not body.body.strip():
        raise HTTPException(status_code=422, detail="Annotation body must not be empty")

    author = _author(request)
    aid = str(uuid.uuid4())
    async with get_session() as session:
        row = AnnotationModel(
            annotation_id=aid,
            event_id=event_id,
            author=author,
            body=body.body.strip(),
        )
        session.add(row)
        await session.flush()
        created_at = row.created_at
        await log_audit(session, "annotation.create", resource=f"event:{event_id}")

    return AnnotationOut(
        annotation_id=aid,
        event_id=event_id,
        author=author,
        body=body.body.strip(),
        created_at=created_at.isoformat() if created_at else None,
    )


@router.delete("/{event_id}/annotations/{annotation_id}", status_code=204,
               summary="Delete annotation")
async def delete_annotation(event_id: str, annotation_id: str, request: Request):
    async with get_session() as session:
        result = await session.execute(
            delete(AnnotationModel).where(
                AnnotationModel.annotation_id == annotation_id,
                AnnotationModel.event_id == event_id,
            )
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Annotation not found")
        await log_audit(session, "annotation.delete", resource=f"annotation:{annotation_id}")
