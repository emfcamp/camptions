"""Caption distribution service for real-time broadcasting."""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

log = logging.getLogger(__name__)


@dataclass
class VenueSubscribers:
    """Track subscribers for a venue."""

    websockets: set[WebSocket] = field(default_factory=set)
    sse_queues: list[asyncio.Queue] = field(default_factory=list)


class DistributionManager:
    """Manages real-time caption distribution to clients."""

    def __init__(self) -> None:
        self.venues: dict[str, VenueSubscribers] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, venue_id: str, websocket: WebSocket) -> None:
        """Subscribe a WebSocket client to venue captions."""
        async with self._lock:
            if venue_id not in self.venues:
                self.venues[venue_id] = VenueSubscribers()
            self.venues[venue_id].websockets.add(websocket)

    async def unsubscribe(self, venue_id: str, websocket: WebSocket) -> None:
        """Unsubscribe a WebSocket client from venue captions."""
        async with self._lock:
            if venue_id in self.venues:
                self.venues[venue_id].websockets.discard(websocket)

    async def subscribe_sse(self, venue_id: str) -> asyncio.Queue:
        """Subscribe an SSE client to venue captions."""
        async with self._lock:
            if venue_id not in self.venues:
                self.venues[venue_id] = VenueSubscribers()
            queue: asyncio.Queue = asyncio.Queue()
            self.venues[venue_id].sse_queues.append(queue)
            return queue

    async def unsubscribe_sse(self, venue_id: str, queue: asyncio.Queue) -> None:
        """Unsubscribe an SSE client from venue captions."""
        async with self._lock:
            if venue_id in self.venues:
                try:
                    self.venues[venue_id].sse_queues.remove(queue)
                except ValueError:
                    pass

    async def broadcast(self, venue_id: str, message: dict[str, Any]) -> None:
        """Broadcast a message to all subscribers of a venue."""
        subs = self.venues.get(venue_id)
        if subs is None or (not subs.websockets and not subs.sse_queues):
            return

        dead_sockets: set[WebSocket] = set()
        message_json = json.dumps(message)

        for websocket in subs.websockets:
            try:
                await websocket.send_text(message_json)
            except Exception as e:
                log.warning("[%s] dist: send failed (%s); dropping subscriber", venue_id, e)
                dead_sockets.add(websocket)

        for queue in subs.sse_queues:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                log.warning("[%s] dist: SSE queue full; dropping message", venue_id)

        if dead_sockets:
            async with self._lock:
                subs.websockets -= dead_sockets

    def get_subscriber_count(self, venue_id: str) -> int:
        """Get total subscriber count for a venue."""
        if venue_id not in self.venues:
            return 0
        return len(self.venues[venue_id].websockets) + len(self.venues[venue_id].sse_queues)


# Global distribution manager instance
distribution_manager = DistributionManager()
