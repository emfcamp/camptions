"""Admin endpoints for session and system management."""

from datetime import UTC, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..models import Segment, Session, Venue
from ..services.distribution import distribution_manager

router = APIRouter()

# TranscriptionManager will be set by main.py after initialization
_transcription_manager = None


def set_transcription_manager(manager) -> None:
    """Set the transcription manager (called by main.py to avoid circular imports)."""
    global _transcription_manager
    _transcription_manager = manager


def get_transcription_manager():
    """Get the transcription manager."""
    if _transcription_manager is None:
        raise RuntimeError("TranscriptionManager not initialized")
    return _transcription_manager


@router.post("/sessions/{venue_id}/start")
async def start_session(
    venue_id: str,
    title: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Manually start a transcription session for a venue."""
    # Verify venue exists
    result = await db.execute(select(Venue).where(Venue.id == venue_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Venue not found")

    transcription_manager = get_transcription_manager()

    if transcription_manager.has_active_session(venue_id):
        raise HTTPException(status_code=400, detail="Session already active for this venue")

    session_id = await transcription_manager.start_session(venue_id, title)

    return {
        "status": "started",
        "session_id": session_id,
        "venue_id": venue_id,
    }


@router.post("/sessions/{venue_id}/stop")
async def stop_session(venue_id: str) -> dict:
    """Manually stop a transcription session for a venue."""
    transcription_manager = get_transcription_manager()

    if not transcription_manager.has_active_session(venue_id):
        raise HTTPException(status_code=400, detail="No active session for this venue")

    session_id = transcription_manager.get_session_id(venue_id)
    await transcription_manager.end_session(venue_id)

    return {
        "status": "stopped",
        "session_id": session_id,
        "venue_id": venue_id,
    }


@router.get("/sessions")
async def list_sessions(
    venue_id: Optional[str] = None,
    active_only: bool = False,
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List transcription sessions."""
    query = select(Session).order_by(Session.started_at.desc()).limit(limit)

    if venue_id:
        query = query.where(Session.venue_id == venue_id)

    if active_only:
        query = query.where(Session.ended_at.is_(None))

    result = await db.execute(query)
    sessions = result.scalars().all()

    transcription_manager = get_transcription_manager()

    return {
        "count": len(sessions),
        "sessions": [
            {
                "id": s.id,
                "venue_id": s.venue_id,
                "title": s.title,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "ended_at": s.ended_at.isoformat() if s.ended_at else None,
                "is_live": transcription_manager.get_session_id(s.venue_id) == s.id,
            }
            for s in sessions
        ],
    }


@router.get("/stats")
async def get_stats(db: AsyncSession = Depends(get_db)) -> dict:
    """Get system statistics."""
    # Count venues
    venue_count = await db.scalar(select(func.count(Venue.id)))

    # Count sessions
    session_count = await db.scalar(select(func.count(Session.id)))

    # Count segments
    segment_count = await db.scalar(select(func.count(Segment.id)))

    # Get active sessions
    transcription_manager = get_transcription_manager()
    active_venues = list(transcription_manager.venues.keys())

    # Get total subscriber count
    total_subscribers = sum(
        distribution_manager.get_subscriber_count(v) for v in active_venues
    )

    return {
        "venues": {
            "total": venue_count,
            "active": len(active_venues),
            "active_list": active_venues,
        },
        "sessions": {
            "total": session_count,
        },
        "segments": {
            "total": segment_count,
        },
        "subscribers": {
            "total": total_subscribers,
        },
    }


@router.post("/cleanup")
async def cleanup_old_data(
    retention_hours: int = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Clean up old caption data based on retention policy."""
    hours = retention_hours or settings.caption_retention_hours
    cutoff = datetime.now(UTC) - timedelta(hours=hours)

    # Delete old segments
    segment_result = await db.execute(
        delete(Segment).where(Segment.created_at < cutoff)
    )
    deleted_segments = segment_result.rowcount

    # Delete old sessions that have ended
    session_result = await db.execute(
        delete(Session)
        .where(Session.ended_at.isnot(None))
        .where(Session.ended_at < cutoff)
    )
    deleted_sessions = session_result.rowcount

    await db.commit()

    return {
        "status": "completed",
        "cutoff": cutoff.isoformat(),
        "deleted": {
            "segments": deleted_segments,
            "sessions": deleted_sessions,
        },
    }


@router.post("/init-venues")
async def init_default_venues(db: AsyncSession = Depends(get_db)) -> dict:
    """Initialize default venues from configuration."""
    created = []
    existing = []

    for venue_id in settings.default_venues:
        result = await db.execute(select(Venue).where(Venue.id == venue_id))
        if result.scalar_one_or_none():
            existing.append(venue_id)
        else:
            venue = Venue(
                id=venue_id,
                name=venue_id.replace("-", " ").title(),
            )
            db.add(venue)
            created.append(venue_id)

    await db.commit()

    return {
        "created": created,
        "existing": existing,
    }
