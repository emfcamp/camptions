"""Schedule endpoints — exposes the cached EMF Camp now-and-next data."""

from fastapi import APIRouter, HTTPException

from ..schemas import NowAndNext, ScheduleSlot
from ..services.schedule import schedule_service

router = APIRouter()


@router.get(
    "/now-and-next",
    summary="Now-and-next for every venue we know about",
    response_model=dict[str, dict[str, ScheduleSlot | None]],
)
async def get_now_and_next() -> dict:
    """
    Return the current and next talk for every venue, keyed by venue ID.

    The data is sourced from the EMF Camp now-and-next API and cached for
    60 s. The same payload (per-venue) is pushed over the caption stream
    as `schedule_update` messages — use that if you want change
    notifications without polling.
    """
    return schedule_service.get_all()


@router.get(
    "/now-and-next/{venue_id}",
    summary="Now-and-next for a single venue",
    response_model=NowAndNext,
)
async def get_venue_now_and_next(venue_id: str) -> NowAndNext:
    """Return the current and next talk for a specific venue."""
    data = schedule_service.get_now_and_next(venue_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"No schedule data for venue '{venue_id}'")
    return NowAndNext(venue_id=venue_id, now=data.get("now"), next=data.get("next"))
