"""
agents/base.py
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Core abstractions for the Vision-I agent swarm.

AgentBase   â€” abstract class every agent inherits from
AgentMessage â€” message envelope passed on the inter-agent bus
AgentStatus â€” state enum (idle / working / alert / error / offline)
"""

from __future__ import annotations

import uuid
import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from agents.swarm import MessageBus


class AgentStatus(str, Enum):
    IDLE = "idle"
    WORKING = "working"
    ALERT = "alert"
    ERROR = "error"
    OFFLINE = "offline"


class AgentMessage:
    """Envelope passed between agents on the message bus."""

    __slots__ = ("id", "sender", "recipient", "msg_type", "payload", "timestamp")

    def __init__(
        self,
        sender: str,
        recipient: str,
        msg_type: str,
        payload: Dict[str, Any],
    ) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.sender = sender
        self.recipient = recipient        # agent_id or "*" for broadcast
        self.msg_type = msg_type          # task | result | alert | status
        self.payload = payload
        self.timestamp = datetime.now(timezone.utc)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "sender": self.sender,
            "recipient": self.recipient,
            "msg_type": self.msg_type,
            "payload": self.payload,
            "timestamp": self.timestamp.isoformat(),
        }


class AgentBase(ABC):
    """
    Abstract base for all Vision-I intelligence agents.

    Subclasses must implement:
      think(context) â†’ plan dict     â€” decide what to do
      act(plan)      â†’ result dict   â€” execute the plan
    """

    def __init__(self, agent_id: str, name: str, role: str) -> None:
        self.agent_id = agent_id
        self.name = name
        self.role = role
        self.status = AgentStatus.IDLE
        self._inbox: asyncio.Queue[AgentMessage] = asyncio.Queue()
        self._bus: Optional[MessageBus] = None   # set by SwarmManager.register()
        self._current_task: Optional[str] = None
        self.logger = logging.getLogger(f"vision_i.agents.{agent_id}")

    @abstractmethod
    async def think(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Analyse *context* and return an action plan."""

    @abstractmethod
    async def act(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        """Execute *plan* and return results."""

    async def receive(self, message: AgentMessage) -> None:
        """Called by the bus when a message arrives for this agent."""
        await self._inbox.put(message)

    async def send(self, recipient: str, msg_type: str, payload: Dict[str, Any]) -> None:
        """Send a message to another agent (or ``*`` for broadcast)."""
        if self._bus is not None:
            msg = AgentMessage(self.agent_id, recipient, msg_type, payload)
            await self._bus.dispatch(msg)

    async def wait_for(self, msg_type: str, timeout: float = 300) -> AgentMessage:
        """Block until a message of the given type arrives, or raise TimeoutError."""
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"{self.agent_id}: timed out waiting for {msg_type}")
            try:
                msg = await asyncio.wait_for(self._inbox.get(), timeout=remaining)
                if msg.msg_type == msg_type:
                    return msg
                # Put back unmatched messages
                await self._inbox.put(msg)
                await asyncio.sleep(0.05)
            except asyncio.TimeoutError:
                raise TimeoutError(f"{self.agent_id}: timed out waiting for {msg_type}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "role": self.role,
            "status": self.status.value,
            "current_task": self._current_task,
        }

