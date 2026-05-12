"""Venue management endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_admin
from ..database import get_db
from ..models import Venue
from ..schemas import VenueCreate, VenueResponse
from ..services.distribution import distribution_manager

router = APIRouter()


@router.get("")
async def list_venues(
    active_only: bool = True,
    db: AsyncSession = Depends(get_db),
) -> list[VenueResponse]:
    """List all venues."""
    query = select(Venue)
    if active_only:
        query = query.where(Venue.is_active == 1)
    query = query.order_by(Venue.name)

    result = await db.execute(query)
    venues = result.scalars().all()

    return [VenueResponse.model_validate(v) for v in venues]


@router.get("/{venue_id}")
async def get_venue(
    venue_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get venue details including live status."""
    result = await db.execute(select(Venue).where(Venue.id == venue_id))
    venue = result.scalar_one_or_none()

    if not venue:
        raise HTTPException(status_code=404, detail="Venue not found")

    return {
        "id": venue.id,
        "name": venue.name,
        "description": venue.description,
        "is_active": bool(venue.is_active),
        "created_at": venue.created_at.isoformat() if venue.created_at else None,
        "subscriber_count": distribution_manager.get_subscriber_count(venue_id),
    }


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
    db: AsyncSession = Depends(get_db),
) -> VenueResponse:
    """Update venue details."""
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

    await db.commit()
    await db.refresh(venue)

    return VenueResponse.model_validate(venue)
