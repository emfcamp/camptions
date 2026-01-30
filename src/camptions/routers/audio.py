"""Audio ingestion WebSocket endpoint."""

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

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

    Expects raw PCM audio: 16kHz, 16-bit signed, mono (s16le)
    """
    await websocket.accept()

    transcription_manager = get_transcription_manager()

    # Start transcription session
    session_id = await transcription_manager.start_session(venue_id, session_title)

    try:
        await websocket.send_json(
            {
                "type": "session_started",
                "session_id": session_id,
                "venue_id": venue_id,
            }
        )

        while True:
            # Receive raw audio bytes
            audio_data = await websocket.receive_bytes()
            await transcription_manager.process_audio(venue_id, audio_data)

    except WebSocketDisconnect:
        print(f"Audio source disconnected: {venue_id}")
    except Exception as e:
        print(f"Audio ingestion error for {venue_id}: {e}")
    finally:
        await transcription_manager.end_session(venue_id)
