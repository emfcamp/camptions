"""EMF Camp schedule service - polls the now-and-next API and broadcasts updates."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from .distribution import distribution_manager

logger = logging.getLogger(__name__)

EMF_NOW_AND_NEXT_URL = "https://emfcamp.org/schedule/now-and-next.json"
POLL_INTERVAL = 60  # seconds


def _parse_talk(talk: dict[str, Any]) -> dict[str, Any]:
    """Extract relevant fields from a talk object."""
    return {
        "title": talk.get("title", ""),
        "speaker": talk.get("speaker", ""),
        "start_time": talk.get("start_time", ""),
        "end_time": talk.get("end_time", ""),
        "description": talk.get("description", ""),
        "link": talk.get("link", ""),
    }


class ScheduleService:
    """Polls the EMF Camp now-and-next API and broadcasts schedule updates to subscribers."""

    def __init__(self) -> None:
        self._cache: dict[str, dict[str, Any]] = {}  # venue_id -> {now, next}
        self._task: asyncio.Task | None = None
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        """Start the schedule polling service."""
        self._client = httpx.AsyncClient(timeout=10.0)
        # Fetch immediately so data is available before first WebSocket connection
        await self._fetch_and_update()
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("Schedule service started, polling every %ds", POLL_INTERVAL)

    async def stop(self) -> None:
        """Stop the schedule polling service."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._client:
            await self._client.aclose()
        logger.info("Schedule service stopped")

    async def _poll_loop(self) -> None:
        """Background loop that re-fetches schedule every POLL_INTERVAL seconds."""
        while True:
            await asyncio.sleep(POLL_INTERVAL)
            await self._fetch_and_update()

    async def _fetch_and_update(self) -> None:
        """Fetch schedule data and broadcast changes to venue subscribers."""
        try:
            resp = await self._client.get(EMF_NOW_AND_NEXT_URL)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning("Failed to fetch EMF schedule: %s", e)
            return

        for venue_id, talks in data.items():
            if not isinstance(talks, list):
                continue

            now = _parse_talk(talks[0]) if len(talks) > 0 else None
            next_ = _parse_talk(talks[1]) if len(talks) > 1 else None
            entry = {"now": now, "next": next_}

            # Only broadcast when something has actually changed
            if self._cache.get(venue_id) != entry:
                self._cache[venue_id] = entry
                await distribution_manager.broadcast(
                    venue_id,
                    {
                        "type": "schedule_update",
                        "venue_id": venue_id,
                        "now": now,
                        "next": next_,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )
                talk_title = now.get("title") if now else None
                logger.debug("Schedule updated for %s: %s", venue_id, talk_title)

    def get_now_and_next(self, venue_id: str) -> dict[str, Any] | None:
        """Return cached now-and-next data for a venue, or None if unavailable."""
        return self._cache.get(venue_id)

    def get_all(self) -> dict[str, dict[str, Any]]:
        """Return all cached schedule data keyed by venue ID."""
        return dict(self._cache)


# Global singleton — started in main.py lifespan
schedule_service = ScheduleService()
