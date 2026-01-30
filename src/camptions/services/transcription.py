"""WhisperLiveKit transcription service."""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Optional

from ..config import Settings
from ..database import get_db_session
from ..models import Segment, Session
from .distribution import distribution_manager


@dataclass
class VenueTranscriber:
    """Manages transcription for a single venue."""

    venue_id: str
    session_id: str
    audio_processor: Any  # WhisperLiveKit AudioProcessor
    sequence: int = 0
    _task: Optional[asyncio.Task] = None


class TranscriptionManager:
    """Manages WhisperLiveKit and per-venue transcription."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.engine: Any = None  # WhisperLiveKit TranscriptionEngine
        self.venues: dict[str, VenueTranscriber] = {}
        self._started = False

    async def start(self) -> None:
        """Initialize the shared transcription engine."""
        if self._started:
            return

        try:
            from whisperlivekit import TranscriptionEngine

            self.engine = TranscriptionEngine(
                model=self.settings.whisper_model,
                lan=self.settings.whisper_language,
                backend=self.settings.whisper_backend,
                backend_policy=self.settings.whisper_backend_policy,
                diarization=self.settings.enable_diarization,
                vad=self.settings.enable_vad,
            )
            self._started = True
        except ImportError:
            # WhisperLiveKit not installed, use mock mode
            self.engine = None
            self._started = True
            print("Warning: WhisperLiveKit not installed, running in mock mode")

    async def stop(self) -> None:
        """Cleanup resources."""
        for venue_id in list(self.venues.keys()):
            await self.end_session(venue_id)
        self.venues.clear()
        self._started = False

    async def start_session(self, venue_id: str, title: Optional[str] = None) -> str:
        """Start a new transcription session for a venue."""
        if venue_id in self.venues:
            await self.end_session(venue_id)

        session_id = str(uuid.uuid4())

        # Create database session record
        async with get_db_session() as db:
            db_session = Session(
                id=session_id,
                venue_id=venue_id,
                title=title,
            )
            db.add(db_session)

        # Create audio processor if engine is available
        audio_processor = None
        if self.engine is not None:
            try:
                from whisperlivekit import AudioProcessor

                audio_processor = AudioProcessor(transcription_engine=self.engine)
            except ImportError:
                pass

        venue_transcriber = VenueTranscriber(
            venue_id=venue_id,
            session_id=session_id,
            audio_processor=audio_processor,
        )
        self.venues[venue_id] = venue_transcriber

        # Start processing task if audio processor is available
        if audio_processor is not None:
            venue_transcriber._task = asyncio.create_task(self._process_results(venue_id))

        return session_id

    async def end_session(self, venue_id: str) -> None:
        """End transcription session for a venue."""
        if venue_id not in self.venues:
            return

        venue = self.venues.pop(venue_id)

        # Cancel processing task
        if venue._task is not None:
            venue._task.cancel()
            try:
                await venue._task
            except asyncio.CancelledError:
                pass

        # Update database session record
        async with get_db_session() as db:
            from sqlalchemy import select, update

            await db.execute(
                update(Session)
                .where(Session.id == venue.session_id)
                .values(ended_at=datetime.now(UTC))
            )

        # Signal end of session to distribution
        await distribution_manager.broadcast(
            venue_id,
            {
                "type": "session_end",
                "session_id": venue.session_id,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )

    async def process_audio(self, venue_id: str, audio_data: bytes) -> None:
        """Process incoming audio for a venue."""
        if venue_id not in self.venues:
            raise ValueError(f"No active session for venue: {venue_id}")

        venue = self.venues[venue_id]
        if venue.audio_processor is not None:
            await venue.audio_processor.process_audio(audio_data)

    def has_active_session(self, venue_id: str) -> bool:
        """Check if venue has an active session."""
        return venue_id in self.venues

    def get_session_id(self, venue_id: str) -> Optional[str]:
        """Get the current session ID for a venue."""
        if venue_id in self.venues:
            return self.venues[venue_id].session_id
        return None

    async def _process_results(self, venue_id: str) -> None:
        """Process transcription results and distribute them."""
        if venue_id not in self.venues:
            return

        venue = self.venues[venue_id]
        if venue.audio_processor is None:
            return

        try:
            results_generator = await venue.audio_processor.create_tasks()

            async for result in results_generator:
                if venue_id not in self.venues:
                    break

                venue.sequence += 1

                # Map WhisperLiveKit output to our segment format
                segment = {
                    "id": str(uuid.uuid4()),
                    "session_id": venue.session_id,
                    "venue_id": venue_id,
                    "sequence": venue.sequence,
                    "type": self._map_segment_type(result.get("type", "partial")),
                    "text": result.get("text", ""),
                    "speaker": result.get("speaker"),
                    "start_time": result.get("start"),
                    "end_time": result.get("end"),
                    "timestamp": datetime.now(UTC).isoformat(),
                }

                # Broadcast to connected clients
                await distribution_manager.broadcast(venue_id, segment)

                # Store committed segments
                if segment["type"] == "committed":
                    await self._store_segment(segment)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"Error processing results for {venue_id}: {e}")

    def _map_segment_type(self, wlk_type: str) -> str:
        """Map WhisperLiveKit types to our types."""
        mapping = {
            "partial": "tentative",
            "complete": "committed",
            "final": "final",
        }
        return mapping.get(wlk_type, "tentative")

    async def _store_segment(self, segment: dict) -> None:
        """Persist committed segment to database."""
        async with get_db_session() as db:
            db_segment = Segment(
                id=segment["id"],
                session_id=segment["session_id"],
                sequence=segment["sequence"],
                segment_type=segment["type"],
                text=segment["text"],
                speaker=segment.get("speaker"),
                start_time=segment["start_time"],
                end_time=segment.get("end_time"),
            )
            db.add(db_segment)
