"""EMF Camp schedule service - polls the now-and-next API and broadcasts updates."""

import asyncio
import logging
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .distribution import distribution_manager

logger = logging.getLogger(__name__)

# Full schedule (all talks, all occurrences) — the trimmed now-and-next.json
# only exposes ~1-2 upcoming items per venue and can omit things already in
# progress (verified live: it can miss a talk that's currently on stage).
# Bump the year each event.
EMF_SCHEDULE_URL = "https://www.emfcamp.org/schedule/2026.json"
POLL_INTERVAL = 60  # seconds

# Camp runs on UK local time; upstream occurrence dates are naive local time.
EMF_TZ = ZoneInfo("Europe/London")


def _parse_dt(value: str | None) -> datetime | None:
    """Parse an upstream `start_date`/`end_date` string as EMF-local time."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=EMF_TZ)
    except ValueError:
        return None


def _slugify(name: str) -> str:
    """Match the venue_id scheme used throughout camptions (e.g. "Stage A" -> "stage-a")."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")


def _parse_talk(talk: dict[str, Any], occ: dict[str, Any]) -> dict[str, Any]:
    """Extract relevant fields from a talk object and the occurrence in question."""
    return {
        "title": talk.get("title", ""),
        "speaker": talk.get("names", "") or "",
        "start_time": occ.get("start_time", ""),
        "end_time": occ.get("end_time", ""),
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
            resp = await self._client.get(EMF_SCHEDULE_URL)
            resp.raise_for_status()
            talks = resp.json()
        except Exception as e:
            logger.warning("Failed to fetch EMF schedule: %s", e)
            return

        # Flatten every talk's occurrences into per-venue lists — a talk can
        # recur (e.g. a daily workshop), so there's no single "the"
        # occurrence for it, and the currently-active one for a venue isn't
        # necessarily tied to the first talk in the raw list.
        by_venue: dict[str, list[tuple[datetime, datetime, dict, dict]]] = {}
        for talk in talks:
            for occ in talk.get("occurrences") or []:
                start = _parse_dt(occ.get("start_date"))
                end = _parse_dt(occ.get("end_date"))
                if start is None or end is None:
                    continue
                venue_id = _slugify(occ.get("venue", ""))
                if not venue_id:
                    continue
                by_venue.setdefault(venue_id, []).append((start, end, talk, occ))

        now_dt = datetime.now(EMF_TZ)
        for venue_id, occurrences in by_venue.items():
            occurrences.sort(key=lambda o: o[0])

            # Only call something "now" if the current time actually falls
            # inside its occurrence window; the first one that hasn't
            # started yet is "next".
            now = None
            next_ = None
            for start, end, talk, occ in occurrences:
                if now is None and start <= now_dt < end:
                    now = _parse_talk(talk, occ)
                    continue
                if start > now_dt:
                    next_ = _parse_talk(talk, occ)
                    break
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
