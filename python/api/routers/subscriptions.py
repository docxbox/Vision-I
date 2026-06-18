"""
api/routers/subscriptions.py
─────────────────────────────
Per-user alert notification subscriptions.

GET    /subscriptions          — list user's subscriptions
POST   /subscriptions          — create subscription
DELETE /subscriptions/{id}     — remove subscription
"""

import logging
import uuid
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

logger = logging.getLogger("vision_i.api.subscriptions")
router = APIRouter(tags=["subscriptions"])


class SubscriptionIn(BaseModel):
    severity:      Optional[str] = None   # critical|high|medium|low|null=all
    alert_type:    Optional[str] = None   # null = all types
    entity_filter: Optional[str] = None


class SubscriptionOut(BaseModel):
    subscription_id: str
    severity:        Optional[str] = None
    alert_type:      Optional[str] = None
    entity_filter:   Optional[str] = None
    is_active:       bool = True
    created_at:      Optional[str] = None

    class Config:
        extra = "allow"


@router.get("", response_model=List[SubscriptionOut])
async def list_subscriptions(request: Request, user_id: str = Query(...)):
    if not request.app.state.db_available:
        return []
    try:
        from sqlalchemy import select
        from storage.database import AlertSubscriptionModel, get_session
        async with get_session() as session:
            rows = (await session.execute(
                select(AlertSubscriptionModel)
                .where(AlertSubscriptionModel.user_id == user_id,
                       AlertSubscriptionModel.is_active == True)  # noqa
            )).scalars().all()
        return [
            {
                "subscription_id": r.subscription_id,
                "severity":        r.severity,
                "alert_type":      r.alert_type,
                "entity_filter":   r.entity_filter,
                "is_active":       r.is_active,
                "created_at":      r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    except Exception as exc:
        logger.error("list_subscriptions failed: %s", exc)
        return []


@router.post("", response_model=SubscriptionOut, status_code=201)
async def create_subscription(request: Request, body: SubscriptionIn, user_id: str = Query(...)):
    if not request.app.state.db_available:
        raise HTTPException(status_code=503, detail="Database unavailable")
    try:
        from storage.database import AlertSubscriptionModel, get_session
        async with get_session() as session:
            sub = AlertSubscriptionModel(
                subscription_id=str(uuid.uuid4())[:16],
                user_id=user_id,
                severity=body.severity,
                alert_type=body.alert_type,
                entity_filter=body.entity_filter,
                is_active=True,
            )
            session.add(sub)
            await session.flush()
            return {
                "subscription_id": sub.subscription_id,
                "severity":        sub.severity,
                "alert_type":      sub.alert_type,
                "entity_filter":   sub.entity_filter,
                "is_active":       sub.is_active,
                "created_at":      sub.created_at.isoformat() if sub.created_at else None,
            }
    except Exception as exc:
        logger.error("create_subscription failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/{subscription_id}", status_code=204)
async def delete_subscription(subscription_id: str, request: Request, user_id: str = Query(...)):
    if not request.app.state.db_available:
        return
    try:
        from sqlalchemy import update
        from storage.database import AlertSubscriptionModel, get_session
        async with get_session() as session:
            await session.execute(
                update(AlertSubscriptionModel)
                .where(AlertSubscriptionModel.subscription_id == subscription_id,
                       AlertSubscriptionModel.user_id == user_id)
                .values(is_active=False)
            )
    except Exception as exc:
        logger.error("delete_subscription failed: %s", exc)
