"""Venue management endpoints."""

from datetime import UTC, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_admin
from ..database import get_db
from ..models import Venue
from ..schemas import PublicVenueStatus, VenueCreate, VenueResponse
from ..services.distribution import distribution_manager

router = APIRouter()

# TranscriptionManager will be set by main.py after initialization
_transcription_manager = None


def set_transcription_manager(manager) -> None:
    global _transcription_manager
    _transcription_manager = manager


def _enrich_venue(venue: Venue) -> PublicVenueStatus:
    """Attach live runtime metrics to a Venue ORM object."""
    vid = venue.id
    dist_drops = distribution_manager.get_drop_counts()
    audio_drops = 0
    is_live = False
    if _transcription_manager is not None:
        is_live = _transcription_manager.is_live(vid)
        vs = _transcription_manager.venues.get(vid)
        if vs is not None:
            audio_drops = vs.audio_drops
    return PublicVenueStatus(
        **VenueResponse.model_validate(venue).model_dump(),
        is_live=is_live,
        subscriber_count=distribution_manager.get_subscriber_count(vid),
        audio_drops=audio_drops,
        distribution_drops=dist_drops.get(vid, 0),
    )


@router.get(
    "",
    response_model=list[PublicVenueStatus],
    summary="List venues (stages we caption)",
)
async def list_venues(
    active_only: bool = Query(
        True, description="Exclude venues marked inactive (e.g. removed stages)."
    ),
    db: AsyncSession = Depends(get_db),
) -> list[PublicVenueStatus]:
    """List all venues. Use `active_only=false` to include inactive ones too."""
    query = select(Venue)
    if active_only:
        query = query.where(Venue.is_active == 1)
    query = query.order_by(Venue.name)

    result = await db.execute(query)
    venues = result.scalars().all()

    return [_enrich_venue(v) for v in venues]


@router.get(
    "/{venue_id}",
    response_model=PublicVenueStatus,
    summary="Venue details including live status and metrics",
)
async def get_venue(
    venue_id: str,
    db: AsyncSession = Depends(get_db),
) -> PublicVenueStatus:
    """
    Return venue metadata plus current runtime status.

    `is_live` is true when both the Pi audio source is streaming and
    WhisperLive is handshaked for this venue.  `subscriber_count` is the
    number of caption viewers connected right now.  `audio_drops` and
    `distribution_drops` are non-zero only when the pipeline is under
    pressure.
    """
    result = await db.execute(select(Venue).where(Venue.id == venue_id))
    venue = result.scalar_one_or_none()

    if not venue:
        raise HTTPException(status_code=404, detail="Venue not found")

    return _enrich_venue(venue)


@router.post("", dependencies=[Depends(require_admin)])
async def create_venue(
    venue: VenueCreate,
    db: AsyncSession = Depends(get_db),
) -> VenueResponse:
    """Create a new venue."""
    # Check if venue already exists
    result = await db.execute(select(Venue).where(Venue.id == venue.id))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Venue already exists")

    db_venue = Venue(
        id=venue.id,
        name=venue.name,
        description=venue.description,
    )
    db.add(db_venue)
    await db.commit()
    await db.refresh(db_venue)

    return VenueResponse.model_validate(db_venue)


@router.patch("/{venue_id}", dependencies=[Depends(require_admin)])
async def update_venue(
    venue_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    is_active: Optional[bool] = None,
    transcription_enabled: Optional[bool] = None,
    stream_url: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
) -> VenueResponse:
    """Update venue details. Pass `stream_url=""` to clear a configured URL."""
    result = await db.execute(select(Venue).where(Venue.id == venue_id))
    venue = result.scalar_one_or_none()

    if not venue:
        raise HTTPException(status_code=404, detail="Venue not found")

    if name is not None:
        venue.name = name
    if description is not None:
        venue.description = description
    if is_active is not None:
        venue.is_active = 1 if is_active else 0
    if stream_url is not None:
        venue.stream_url = stream_url or None

    transcription_state_changed = False
    if transcription_enabled is not None:
        new_val = 1 if transcription_enabled else 0
        if new_val != venue.transcription_enabled:
            venue.transcription_enabled = new_val
            transcription_state_changed = True

    await db.commit()
    await db.refresh(venue)

    if transcription_state_changed:
        # When disabling, also tear down any active session so audio stops
        # being transcribed immediately. The Pi may still be streaming —
        # the audio router treats a missing session as "discard".
        if not transcription_enabled and _transcription_manager is not None:
            if _transcription_manager.has_active_session(venue_id):
                await _transcription_manager.end_session(venue_id)
        await distribution_manager.broadcast(
            venue_id,
            {
                "type": "transcription_disabled" if not transcription_enabled
                        else "transcription_enabled",
                "venue_id": venue_id,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )

    return VenueResponse.model_validate(venue)
