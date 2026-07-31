"""One physical outbound agent connection shared by all realtime protocols."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket


@dataclass(eq=False)
class AgentConnection:
    """Serialize JSON and binary writes on one cluster-agent WebSocket."""

    websocket: WebSocket
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send_json(self, message: Any) -> None:
        payload = message.model_dump(mode="json") if hasattr(message, "model_dump") else message
        async with self.send_lock:
            await self.websocket.send_json(payload)

    async def send_bytes(self, payload: bytes) -> None:
        async with self.send_lock:
            await self.websocket.send_bytes(payload)


class AgentConnectionRegistry:
    """Cluster identity to the single currently authoritative outbound socket."""

    def __init__(self) -> None:
        self._connections: dict[str, AgentConnection] = {}

    def current(self, cluster_id: str) -> AgentConnection | None:
        return self._connections.get(cluster_id)

    def register(
        self,
        cluster_id: str,
        websocket: WebSocket,
    ) -> tuple[AgentConnection, AgentConnection | None]:
        connection = AgentConnection(websocket)
        previous = self._connections.get(cluster_id)
        self._connections[cluster_id] = connection
        return connection, previous

    def unregister(self, cluster_id: str, connection: AgentConnection) -> bool:
        if self._connections.get(cluster_id) is not connection:
            return False
        self._connections.pop(cluster_id, None)
        return True
