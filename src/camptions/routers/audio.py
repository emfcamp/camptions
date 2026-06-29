"""Audio ingestion WebSocket endpoint."""

import logging
import time
from datetime import UTC, datetime

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from ..auth import verify_ingest_token
from ..database import get_db_session
from ..models import Venue
from ..services.distribution import distribution_manager

log = logging.getLogger(__name__)

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


@router.websocket("/ingest/{venue_id}")
async def audio_ingest(
    websocket: WebSocket,
    venue_id: str,
    token: str = Query(None),
    session_title: str = Query(None),
) -> None:
    """
    WebSocket endpoint for audio ingestion from Raspberry Pi.

    Expects raw PCM audio: 16 kHz, 16-bit signed, mono (s16le).
    Requires ?token=<CAMPTIONS_INGEST_TOKEN> query parameter.
    """
    try:
        verify_ingest_token(token)
    except Exception:
        await websocket.close(code=4401)
        return

    peer = f"{websocket.client.host}:{websocket.client.port}" if websocket.client else "?"
    await websocket.accept()
    log.info("[%s] Pi WS accepted from %s", venue_id, peer)

    transcription_manager = get_transcription_manager()

    # Honor the per-venue transcription toggle. We always create a session so
    # the venue can be paused/unpaused mid-stream without disconnecting the Pi
    # or viewers — when paused, the session stays up but process_audio drops
    # incoming audio and no captions are produced (see set_paused).
    transcription_enabled = await _venue_transcription_enabled(venue_id)
    paused = not transcription_enabled

    session_id = await transcription_manager.start_session(
        venue_id, session_title, paused=paused
    )
    # venue_live is broadcast by TranscriptionManager once WL actually
    # connects and handshakes — viewers only show "Live" when captions can be
    # produced, and the client suppresses it while paused.
    if paused:
        log.info("[%s] transcription paused for venue; dropping audio", venue_id)
        await distribution_manager.broadcast(
            venue_id,
            {
                "type": "transcription_disabled",
                "venue_id": venue_id,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )

    chunks = 0
    bytes_in = 0
    t_open = time.monotonic()
    t_first_chunk: float | None = None

    try:
        # Always reply with `session_started` — the Pi client expects this
        # type as its connection handshake. `transcription_enabled` reflects the
        # venue's current pause state; the Pi streams regardless, and the
        # backend drops the audio while paused.
        await websocket.send_json(
            {
                "type": "session_started",
                "session_id": session_id,
                "venue_id": venue_id,
                "transcription_enabled": transcription_enabled,
            }
        )

        while True:
            audio_data = await websocket.receive_bytes()
            chunks += 1
            bytes_in += len(audio_data)
            if t_first_chunk is None:
                t_first_chunk = time.monotonic()
                log.info(
                    "[%s] first audio chunk: %d bytes, %.0f ms after accept",
                    venue_id, len(audio_data), (t_first_chunk - t_open) * 1000,
                )
            # process_audio drops the chunk while the venue is paused; otherwise
            # it queues it for WhisperLive.
            await transcription_manager.process_audio(venue_id, audio_data)

    except WebSocketDisconnect as e:
        elapsed_ms = (time.monotonic() - t_open) * 1000
        log.info(
            "[%s] Pi disconnected: code=%s after %.0f ms / %d chunks / %d bytes (first chunk: %s)",
            venue_id, e.code, elapsed_ms, chunks, bytes_in,
            f"+{(t_first_chunk - t_open) * 1000:.0f} ms" if t_first_chunk else "never",
        )
    except Exception:
        log.exception("[%s] Pi ingest error", venue_id)
    finally:
        await transcription_manager.end_session(venue_id)
        await distribution_manager.broadcast(
            venue_id,
            {
                "type": "venue_offline",
                "venue_id": venue_id,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )


async def _venue_transcription_enabled(venue_id: str) -> bool:
    """Return whether transcription is enabled for `venue_id`.

    Defaults to True when the venue row is missing — the audio ingest path
    historically auto-created sessions for unknown venues, so we don't want
    a missing row to silently swallow audio.
    """
    async with get_db_session() as db:
        row = await db.execute(
            select(Venue.transcription_enabled).where(Venue.id == venue_id)
        )
        flag = row.scalar_one_or_none()
    if flag is None:
        return True
    return bool(flag)
