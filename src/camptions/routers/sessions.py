"""Public sessions endpoints — read-only metadata for transcription sessions.

A "session" is one Pi-audio connection's worth of captions — usually one
talk's worth. Consumers want this to correlate segments back to the talk
they're watching.

The richer admin view (active flag, etc.) lives under `/api/admin/sessions`
and isn't part of the public surface.
"""

import base64
import binascii
import json
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import Session
from ..schemas import PublicSession, PublicSessionsResponse

router = APIRouter()


def _encode_cursor(s: Session) -> str:
    payload = json.dumps(
        {
            "t": s.started_at.isoformat() if s.started_at else None,
            "id": s.id,
        },
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(cursor + padding).decode()
        data = json.loads(raw)
        ts = datetime.fromisoformat(data["t"]) if data.get("t") else None
        sid = data["id"]
        if ts is None or not isinstance(sid, str):
            raise ValueError("missing fields")
        return ts, sid
    except (ValueError, KeyError, json.JSONDecodeError, binascii.Error) as e:
        raise HTTPException(status_code=400, detail=f"Invalid cursor: {e}")


@router.get(
    "",
    response_model=PublicSessionsResponse,
    summary="List transcription sessions",
)
async def list_sessions(
    venue_id: Optional[str] = Query(None, description="Filter to one venue."),
    since: Optional[datetime] = Query(
        None, description="Sessions started strictly after this ISO 8601 timestamp."
    ),
    until: Optional[datetime] = Query(
        None, description="Sessions started at or before this timestamp."
    ),
    active_only: bool = Query(False, description="Only sessions that haven't ended yet."),
    order: Literal["asc", "desc"] = Query("desc", description="Sort by `started_at`."),
    limit: int = Query(50, ge=1, le=500, description="Maximum sessions to return."),
    cursor: Optional[str] = Query(None, description="Cursor from a previous `next_cursor`."),
    db: AsyncSession = Depends(get_db),
) -> PublicSessionsResponse:
    """List transcription sessions. Each session is one Pi audio connection."""
    base = select(Session)
    if venue_id is not None:
        base = base.where(Session.venue_id == venue_id)
    if since is not None:
        base = base.where(Session.started_at > since)
    if until is not None:
        base = base.where(Session.started_at <= until)
    if active_only:
        base = base.where(Session.ended_at.is_(None))

    if cursor is not None:
        cur_ts, cur_id = _decode_cursor(cursor)
        if order == "asc":
            base = base.where(
                or_(
                    Session.started_at > cur_ts,
                    and_(Session.started_at == cur_ts, Session.id > cur_id),
                )
            )
        else:
            base = base.where(
                or_(
                    Session.started_at < cur_ts,
                    and_(Session.started_at == cur_ts, Session.id < cur_id),
                )
            )

    if order == "asc":
        base = base.order_by(Session.started_at.asc(), Session.id.asc())
    else:
        base = base.order_by(Session.started_at.desc(), Session.id.desc())

    rows = (await db.execute(base.limit(limit + 1))).scalars().all()
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = _encode_cursor(page[-1]) if has_more and page else None

    return PublicSessionsResponse(
        count=len(page),
        sessions=[
            PublicSession(
                id=s.id,
                venue_id=s.venue_id,
                title=s.title,
                started_at=s.started_at,
                ended_at=s.ended_at,
            )
            for s in page
        ],
        next_cursor=next_cursor,
    )


@router.get(
    "/{session_id}",
    response_model=PublicSession,
    summary="Get a single session by ID",
)
async def get_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> PublicSession:
    """Look up a single session's metadata."""
    s = await db.scalar(select(Session).where(Session.id == session_id))
    if s is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return PublicSession(
        id=s.id,
        venue_id=s.venue_id,
        title=s.title,
        started_at=s.started_at,
        ended_at=s.ended_at,
    )
