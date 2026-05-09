"""Audio ingestion WebSocket endpoint."""

import logging
import time
from datetime import UTC, datetime

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

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
    session_title: str = Query(None),
) -> None:
    """
    WebSocket endpoint for audio ingestion from Raspberry Pi.

    Expects raw PCM audio: 44.1 kHz, 16-bit signed, mono (s16le).
    """
    peer = f"{websocket.client.host}:{websocket.client.port}" if websocket.client else "?"
    await websocket.accept()
    log.info("[%s] Pi WS accepted from %s", venue_id, peer)

    transcription_manager = get_transcription_manager()
    session_id = await transcription_manager.start_session(venue_id, session_title)

    await distribution_manager.broadcast(
        venue_id,
        {
            "type": "venue_live",
            "venue_id": venue_id,
            "session_id": session_id,
            "timestamp": datetime.now(UTC).isoformat(),
        },
    )

    chunks = 0
    bytes_in = 0
    t_open = time.monotonic()
    t_first_chunk: float | None = None

    try:
        await websocket.send_json(
            {
                "type": "session_started",
                "session_id": session_id,
                "venue_id": venue_id,
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
