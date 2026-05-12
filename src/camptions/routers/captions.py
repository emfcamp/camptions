"""Caption distribution endpoints."""

import asyncio
import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import Segment, Session
from ..services.distribution import distribution_manager
from ..services.schedule import schedule_service

router = APIRouter()

# TranscriptionManager will be set by main.py after initialization
_transcription_manager = None


def set_transcription_manager(manager) -> None:
    global _transcription_manager
    _transcription_manager = manager


@router.websocket("/stream/{venue_id}")
async def caption_stream(websocket: WebSocket, venue_id: str) -> None:
    """
    WebSocket endpoint for receiving live captions.

    Clients connect here to receive real-time caption updates.
    """
    await websocket.accept()

    await distribution_manager.subscribe(venue_id, websocket)

    try:
        # Send connection confirmation including current live status.
        # "Live" requires both Pi audio and WL to be connected.
        is_live = _transcription_manager is not None and _transcription_manager.is_live(venue_id)
        await websocket.send_json(
            {
                "type": "connected",
                "venue_id": venue_id,
                "is_live": is_live,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )

        # Send current schedule for this venue if available
        schedule = schedule_service.get_now_and_next(venue_id)
        if schedule is not None:
            await websocket.send_json(
                {
                    "type": "schedule_update",
                    "venue_id": venue_id,
                    "now": schedule["now"],
                    "next": schedule["next"],
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )

        # Keep connection alive
        while True:
            try:
                # Handle any client messages (e.g., ping/pong)
                message = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                if message == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                # Send keepalive
                await websocket.send_json({"type": "keepalive"})

    except WebSocketDisconnect:
        pass
    finally:
        await distribution_manager.unsubscribe(venue_id, websocket)


@router.get("/stream/{venue_id}/sse")
async def caption_stream_sse(venue_id: str) -> StreamingResponse:
    """
    Server-Sent Events endpoint for caption streaming.

    Alternative to WebSocket for simpler client implementations.
    """
    queue = await distribution_manager.subscribe_sse(venue_id)

    async def event_generator():
        try:
            # Send initial connection event
            yield f"data: {json.dumps({'type': 'connected', 'venue_id': venue_id})}\n\n"

            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            await distribution_manager.unsubscribe_sse(venue_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/history/{venue_id}")
async def get_caption_history(
    venue_id: str,
    limit: int = Query(100, ge=1, le=1000),
    since: datetime = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get historical captions for a venue."""
    query = (
        select(Segment)
        .join(Session)
        .where(Session.venue_id == venue_id)
        .where(Segment.segment_type == "committed")
        .order_by(Segment.created_at.desc())
        .limit(limit)
    )

    if since:
        query = query.where(Segment.created_at > since)

    result = await db.execute(query)
    segments = result.scalars().all()

    return {
        "venue_id": venue_id,
        "count": len(segments),
        "segments": [
            {
                "id": s.id,
                "sequence": s.sequence,
                "text": s.text,
                "start_time": s.start_time,
                "end_time": s.end_time,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in reversed(segments)
        ],
    }
