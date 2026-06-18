"""
playbook/actions.py
───────────────────
Action handlers a playbook can call. Each handler receives a context dict and
returns a result dict that is appended to the playbook run history.

Add new actions with @register_action("my_action").
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict

logger = logging.getLogger("playbook.actions")

ActionHandler = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]
ACTION_REGISTRY: Dict[str, ActionHandler] = {}


def register_action(name: str) -> Callable[[ActionHandler], ActionHandler]:
    def decorator(fn: ActionHandler) -> ActionHandler:
        ACTION_REGISTRY[name] = fn
        return fn
    return decorator


@register_action("notify")
async def notify(ctx: Dict[str, Any]) -> Dict[str, Any]:
    target = ctx.get("target", "operator")
    message = ctx.get("message") or ctx.get("title") or "Vision-I playbook notification"
    logger.info("notify → %s: %s", target, message)
    return {"action": "notify", "target": target, "message": message, "status": "sent"}


@register_action("create_alert")
async def create_alert(ctx: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "action": "create_alert",
        "alert_type": ctx.get("alert_type", "playbook"),
        "severity": ctx.get("severity", "medium"),
        "title": ctx.get("title") or "Playbook-generated alert",
        "status": "queued",
    }


@register_action("escalate")
async def escalate(ctx: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "action": "escalate",
        "to": ctx.get("to", "duty_analyst"),
        "reason": ctx.get("reason", "playbook trigger"),
        "status": "pending_ack",
    }


@register_action("trigger_ingest")
async def trigger_ingest(ctx: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "action": "trigger_ingest",
        "source": ctx.get("source", "all"),
        "query": ctx.get("query", ""),
        "status": "queued",
    }


@register_action("record_decision")
async def record_decision(ctx: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "action": "record_decision",
        "event_id": ctx.get("event_id"),
        "coa_text": ctx.get("coa_text") or "Auto-recorded by playbook",
        "analyst": ctx.get("analyst", "playbook-engine"),
        "status": "recorded",
    }
