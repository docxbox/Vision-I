"""
playbook/engine.py
──────────────────
Declarative playbook runtime. Loads YAML playbooks, evaluates triggers against
event payloads, and executes ordered action steps using `actions.ACTION_REGISTRY`.

A playbook YAML entry:

  - id: narrative-spike
    name: Narrative Spike Containment
    objective: Contain rapid narrative amplification before it goes viral
    trigger:
      narrative_strength_gte: 0.75
      severity_in: [high, critical]
    requires_approval: true
    steps:
      - action: notify
        target: duty_analyst
        message: "Narrative spike detected"
      - action: create_alert
        alert_type: narrative
        severity: high
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

try:
    import yaml  # type: ignore
    _HAS_YAML = True
except Exception:
    _HAS_YAML = False

from .actions import ACTION_REGISTRY

logger = logging.getLogger("playbook.engine")


@dataclass
class PlaybookStep:
    action: str
    params: Dict[str, Any] = field(default_factory=dict)
    automated: bool = True


@dataclass
class Playbook:
    id: str
    name: str
    objective: str
    trigger: Dict[str, Any]
    steps: List[PlaybookStep]
    requires_approval: bool = False
    status: str = "ready"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "objective": self.objective,
            "trigger": self.trigger,
            "requires_approval": self.requires_approval,
            "status": self.status,
            "steps": [
                {"action": s.action, "params": s.params, "automated": s.automated}
                for s in self.steps
            ],
        }


def _matches_trigger(trigger: Dict[str, Any], context: Dict[str, Any]) -> bool:
    if not trigger:
        return True
    for key, expected in trigger.items():
        if key.endswith("_gte"):
            actual = context.get(key[:-4])
            if actual is None or float(actual) < float(expected):
                return False
        elif key.endswith("_lte"):
            actual = context.get(key[:-4])
            if actual is None or float(actual) > float(expected):
                return False
        elif key.endswith("_in"):
            actual = context.get(key[:-3])
            if actual not in expected:
                return False
        elif key.endswith("_eq"):
            if context.get(key[:-3]) != expected:
                return False
        else:
            if context.get(key) != expected:
                return False
    return True


def load_playbooks(path: Optional[str] = None) -> List[Playbook]:
    """Load playbook definitions from YAML. Defaults to `playbook/playbooks.yaml`."""
    if path is None:
        path = str(Path(__file__).parent / "playbooks.yaml")
    if not _HAS_YAML or not os.path.exists(path):
        logger.warning("playbook: yaml file missing or PyYAML unavailable (%s)", path)
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or []
    except Exception as exc:
        logger.error("playbook: failed to load %s: %s", path, exc)
        return []

    playbooks: List[Playbook] = []
    for entry in raw:
        steps = [
            PlaybookStep(
                action=step.get("action", "notify"),
                params={k: v for k, v in step.items() if k not in ("action", "automated")},
                automated=step.get("automated", True),
            )
            for step in entry.get("steps", [])
        ]
        playbooks.append(
            Playbook(
                id=entry.get("id", entry.get("name", "unknown")),
                name=entry.get("name", entry.get("id", "Untitled")),
                objective=entry.get("objective", ""),
                trigger=entry.get("trigger", {}),
                steps=steps,
                requires_approval=bool(entry.get("requires_approval", False)),
            )
        )
    return playbooks


class PlaybookEngine:
    def __init__(self, playbooks: Optional[Iterable[Playbook]] = None) -> None:
        self.playbooks: List[Playbook] = list(playbooks) if playbooks else load_playbooks()

    def list(self) -> List[Dict[str, Any]]:
        return [p.to_dict() for p in self.playbooks]

    def get(self, playbook_id: str) -> Optional[Playbook]:
        return next((p for p in self.playbooks if p.id == playbook_id), None)

    def matches(self, context: Dict[str, Any]) -> List[Playbook]:
        return [p for p in self.playbooks if _matches_trigger(p.trigger, context)]

    async def execute(self, playbook_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
        playbook = self.get(playbook_id)
        if playbook is None:
            return {"playbook_id": playbook_id, "status": "not_found", "results": []}

        results: List[Dict[str, Any]] = []
        for step in playbook.steps:
            handler = ACTION_REGISTRY.get(step.action)
            if handler is None:
                results.append({"action": step.action, "status": "unknown_action"})
                continue
            merged = {**context, **step.params}
            try:
                result = await handler(merged)
            except Exception as exc:
                logger.exception("playbook step %s failed", step.action)
                result = {"action": step.action, "status": "error", "error": str(exc)}
            results.append(result)

        return {
            "playbook_id": playbook.id,
            "name": playbook.name,
            "status": "executed",
            "results": results,
        }
