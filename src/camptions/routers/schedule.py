"""Schedule endpoints — exposes the cached EMF now-and-next data via REST."""

from fastapi import APIRouter, HTTPException

from ..services.schedule import schedule_service

router = APIRouter()


@router.get("/now-and-next")
async def get_now_and_next() -> dict:
    """Get current and next talks for all venues."""
    return schedule_service.get_all()


@router.get("/now-and-next/{venue_id}")
async def get_venue_now_and_next(venue_id: str) -> dict:
    """Get current and next talk for a specific venue."""
    data = schedule_service.get_now_and_next(venue_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"No schedule data for venue '{venue_id}'")
    return {"venue_id": venue_id, **data}
