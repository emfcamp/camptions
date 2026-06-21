"""Caption distribution endpoints — the live stream and history surface."""

import asyncio
import base64
import binascii
import json
from datetime import UTC, datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db, get_db_session
from ..models import Segment, Session, Venue
from ..schemas import PublicSegment, PublicSegmentsResponse
from ..services.distribution import distribution_manager
from ..services.schedule import schedule_service

router = APIRouter()

# TranscriptionManager will be set by main.py after initialization
_transcription_manager = None
_ws_limiter = None


def set_transcription_manager(manager) -> None:
    global _transcription_manager
    _transcription_manager = manager


def set_ws_limiter(limiter) -> None:
    """Inject the shared WSConnectionLimiter from main.py."""
    global _ws_limiter
    _ws_limiter = limiter


# ── Streaming endpoints ──────────────────────────────────────────────────────


_STREAM_DOC = """
Live caption WebSocket for a single venue.

The server pushes JSON messages with a `type` discriminator. There's no
client-side protocol — you only need to read; sending `"ping"` will get a
text `"pong"` reply if you want a heartbeat, but the server emits its own
`keepalive` every 30s regardless.

### Message catalogue

| `type` | When | Payload |
|--------|------|---------|
| `connected` | First message after the WS opens. Always sent. | `{ session_id?, is_live, transcription_enabled }` |
| `venue_live` | Pi audio is streaming *and* the transcription backend is handshaked. | `{ session_id }` |
| `venue_offline` | Pi audio is gone, or the transcription backend is unreachable. | — |
| `session_end` | The current transcription session ended (Pi disconnect). | `{ session_id }` |
| `committed` | A finalised caption segment. | `{ session_id, sequence, text, start_time?, end_time?, timestamp }` |
| `tentative` | The current in-progress text (may change/be replaced). Not persisted. | `{ session_id, text, timestamp }` |
| `transcription_disabled` | Admin paused transcription for this venue. | — |
| `transcription_enabled` | Admin re-enabled transcription. | — |
| `schedule_update` | EMF now-and-next data for this venue changed. | `{ now, next }` |
| `keepalive` | Every ~30s; discard. | — |

### Deduplication

Segments are uniquely identified by `(session_id, sequence)`. A backend
reconnect may replay the most recent ~5 seconds of audio against the
transcription server; the server-side dedupes these so consumers get each
committed segment exactly once within a session. Across sessions,
`sequence` resets to 1 — use the pair.

### Example

```js
const ws = new WebSocket('wss://stages.emf.camp/api/captions/stream/stage-a');
ws.onmessage = e => {
    const msg = JSON.parse(e.data);
    if (msg.type === 'committed') console.log(msg.text);
};
```
"""


@router.websocket("/stream/{venue_id}")
async def caption_stream(websocket: WebSocket, venue_id: str) -> None:
    # Per-IP simultaneous-connection cap. Documented in the API description.
    if _ws_limiter is not None and not await _ws_limiter.acquire(websocket):
        await websocket.close(code=1013)  # Try Again Later
        return

    await websocket.accept()
    await distribution_manager.subscribe(venue_id, websocket)
    try:
        is_live = _transcription_manager is not None and _transcription_manager.is_live(venue_id)
        session_id = (
            _transcription_manager.get_session_id(venue_id)
            if _transcription_manager is not None
            else None
        )
        transcription_enabled = await _venue_transcription_enabled(venue_id)
        await websocket.send_json(
            {
                "type": "connected",
                "venue_id": venue_id,
                "session_id": session_id,
                "is_live": is_live,
                "transcription_enabled": transcription_enabled,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )

        # Send current schedule for this venue if available so new clients
        # get the talk title without waiting for the next 60s poll.
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

        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                if message == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "keepalive"})

    except WebSocketDisconnect:
        pass
    finally:
        await distribution_manager.unsubscribe(venue_id, websocket)
        if _ws_limiter is not None:
            await _ws_limiter.release(websocket)


# Attach the long docstring to the route so it surfaces in /docs. (We keep
# the handler body terse; the prose lives here.)
caption_stream.__doc__ = _STREAM_DOC


@router.get(
    "/stream/{venue_id}/sse",
    response_class=StreamingResponse,
    summary="Server-Sent Events stream of live captions",
)
async def caption_stream_sse(venue_id: str) -> StreamingResponse:
    """
    Server-Sent Events alternative to the WebSocket stream. Each `data:`
    payload is the same JSON shape documented on the WebSocket endpoint —
    same message catalogue, same dedupe rules.

    Curl-friendly:

    ```
    curl -N https://stages.emf.camp/api/captions/stream/stage-a/sse
    ```

    The endpoint emits an SSE comment (`: keepalive`) every 15s so proxies
    don't close idle connections.
    """
    queue = await distribution_manager.subscribe_sse(venue_id)

    async def event_generator():
        try:
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


# ── History endpoint ─────────────────────────────────────────────────────────


def _encode_cursor(s: Segment) -> str:
    """Base64-encode the (created_at, id) tuple used for stable pagination."""
    payload = json.dumps(
        {
            "t": s.created_at.isoformat() if s.created_at else None,
            "id": s.id,
        },
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    """Decode a cursor previously emitted by `_encode_cursor`."""
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(cursor + padding).decode()
        data = json.loads(raw)
        ts = datetime.fromisoformat(data["t"]) if data.get("t") else None
        seg_id = data["id"]
        if ts is None or not isinstance(seg_id, str):
            raise ValueError("missing fields")
        return ts, seg_id
    except (ValueError, KeyError, json.JSONDecodeError, binascii.Error) as e:
        raise HTTPException(status_code=400, detail=f"Invalid cursor: {e}")


@router.get(
    "/history/{venue_id}",
    response_model=PublicSegmentsResponse,
    summary="Recent caption segments for a venue",
)
async def get_caption_history(
    venue_id: str,
    limit: int = Query(100, ge=1, le=1000, description="Maximum segments to return."),
    since: Optional[datetime] = Query(
        None, description="Only return segments created strictly after this ISO 8601 timestamp."
    ),
    until: Optional[datetime] = Query(
        None, description="Only return segments created at or before this timestamp."
    ),
    session_id: Optional[str] = Query(
        None, description="Restrict to a single transcription session (one talk's worth of audio)."
    ),
    order: Literal["asc", "desc"] = Query(
        "asc",
        description=(
            "Result order. `asc` (default) returns oldest-first within the page — "
            "natural for backfill walks. `desc` returns newest-first."
        ),
    ),
    cursor: Optional[str] = Query(
        None,
        description=(
            "Opaque cursor from a previous response's `next_cursor`. Walks in the "
            "direction of `order`; the cursor itself is exclusive of the boundary segment."
        ),
    ),
    db: AsyncSession = Depends(get_db),
) -> PublicSegmentsResponse:
    """
    Fetch finalised caption segments for a venue.

    Tentatives (in-progress text) are never persisted and never returned here —
    use the WebSocket / SSE stream for those.

    For full-archive walks: pick an `order` (usually `asc`), set a `limit`,
    and follow `next_cursor` until it's absent.
    """
    base = (
        select(Segment)
        .join(Session)
        .where(Session.venue_id == venue_id)
        .where(Segment.segment_type == "committed")
    )

    if since is not None:
        base = base.where(Segment.created_at > since)
    if until is not None:
        base = base.where(Segment.created_at <= until)
    if session_id is not None:
        base = base.where(Segment.session_id == session_id)

    if cursor is not None:
        cur_ts, cur_id = _decode_cursor(cursor)
        if order == "asc":
            base = base.where(
                or_(
                    Segment.created_at > cur_ts,
                    and_(Segment.created_at == cur_ts, Segment.id > cur_id),
                )
            )
        else:
            base = base.where(
                or_(
                    Segment.created_at < cur_ts,
                    and_(Segment.created_at == cur_ts, Segment.id < cur_id),
                )
            )

    if order == "asc":
        base = base.order_by(Segment.created_at.asc(), Segment.id.asc())
    else:
        base = base.order_by(Segment.created_at.desc(), Segment.id.desc())

    # Over-fetch by one to know whether there's a next page without an
    # extra count query.
    rows = (await db.execute(base.limit(limit + 1))).scalars().all()
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = _encode_cursor(page[-1]) if has_more and page else None

    return PublicSegmentsResponse(
        venue_id=venue_id,
        count=len(page),
        segments=[
            PublicSegment(
                id=s.id,
                session_id=s.session_id,
                venue_id=venue_id,
                sequence=s.sequence,
                text=s.text,
                start_time=s.start_time,
                end_time=s.end_time,
                created_at=s.created_at,
            )
            for s in page
        ],
        next_cursor=next_cursor,
    )


async def _venue_transcription_enabled(venue_id: str) -> bool:
    """Return whether transcription is enabled for `venue_id` (default True)."""
    async with get_db_session() as db:
        flag = await db.scalar(
            select(Venue.transcription_enabled).where(Venue.id == venue_id)
        )
    if flag is None:
        return True
    return bool(flag)
