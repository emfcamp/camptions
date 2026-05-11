"""Transcription service — bridges Pi audio to a WhisperLiveKit sidecar.

Per-venue lifecycle:

    Pi audio WS  ──►  process_audio()  ──►  audio_queue
                                                  │
                                                  ▼
                                         AudioStreamer._send_loop
                                                  │   (shared WLKConnection)
                                                  ▼
                                              WLK WS
                                                  │
                                                  ▼
                                    TranscriptionProcessor._recv_loop
                                                  │
                                                  ▼
                                       distribution + DB

Send and receive share one WLKConnection. WLK going away does not end the
session — audio keeps queueing, both loops reconnect, transcription resumes.
Session ends only when end_session() is called (Pi disconnect or shutdown).
"""

import asyncio
import contextlib
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Optional

import websockets
from sqlalchemy import update

from ..config import Settings
from ..database import get_db_session
from ..models import Segment, Session
from .audio_streamer import AudioStreamer
from .distribution import distribution_manager
from .session import VenueSession, await_or_cancel, sleep_or_stop

log = logging.getLogger(__name__)


def _parse_timestamp(value: Any) -> Optional[float]:
    """WLK serialises segment times as 'H:MM:SS.cc' strings; convert to float."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            seconds = 0.0
            for part in value.split(":"):
                seconds = seconds * 60 + float(part)
            return seconds
        except ValueError:
            return None
    return None


class TranscriptionProcessor:
    """Receives and processes transcription responses from WLK."""

    def __init__(self, venue: VenueSession, settings: Settings) -> None:
        self.venue = venue
        self.settings = settings

    async def start(self) -> None:
        self.venue.recv_task = asyncio.create_task(
            self._recv_loop(), name=f"recv:{self.venue.venue_id}"
        )

    async def stop(self) -> None:
        await await_or_cancel(self.venue.recv_task, "receiver", self.venue.venue_id)

    async def _recv_loop(self) -> None:
        msg_total = 0
        venue = self.venue
        url = self.settings.wlk_url + "?mode=diff"

        while not venue.stop_event.is_set():
            ws = await venue.wlk.ensure(url, venue.stop_event)
            if ws is None:
                break

            try:
                async for raw in ws:
                    msg_total += 1
                    if isinstance(raw, bytes):
                        continue
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        log.warning(
                            "[%s] receiver: non-JSON: %r",
                            venue.venue_id, raw[:120],
                        )
                        continue

                    mtype = data.get("type")
                    if mtype == "config":
                        continue
                    if mtype == "ready_to_stop":
                        log.info("[%s] receiver: WLK ready_to_stop; reconnecting", venue.venue_id)
                        await venue.wlk.drop(ws)
                        break

                    await self._broadcast(data)

                log.info("[%s] receiver: WS closed by WLK; reconnecting", venue.venue_id)
                await venue.wlk.drop(ws)

            except websockets.ConnectionClosed as e:
                log.warning(
                    "[%s] receiver: WLK connection closed (%s); will reconnect",
                    venue.venue_id, e,
                )
                await venue.wlk.drop(ws)
            except Exception:
                log.exception("[%s] receiver error; will reconnect", venue.venue_id)
                await venue.wlk.drop(ws)
                if await sleep_or_stop(venue.stop_event, 1):
                    break

        log.info("[%s] receiver exiting after %d messages", venue.venue_id, msg_total)

    async def _broadcast(self, data: dict) -> None:
        """Apply WLK snapshot/diff to venue state and fan out to subscribers."""
        venue = self.venue
        msg_type = data.get("type")

        if msg_type == "snapshot":
            lines_data = data.get("lines") or []
            venue.wlk_lines = list(lines_data)
            venue.last_tentative = ""

            for line in lines_data:
                text = (line.get("text") or "").strip()
                if not text:
                    continue
                seq = venue.next_sequence
                venue.next_sequence += 1
                segment = self._make_segment(seq, text, line)
                await distribution_manager.broadcast(venue.venue_id, segment)
                await self._store(segment)

            log.info(
                "[%s] snapshot: %d lines, buffer=%r",
                venue.venue_id, len(lines_data),
                data.get("buffer_transcription", ""),
            )

        elif msg_type == "diff":
            n_pruned = data.get("lines_pruned", 0)
            if n_pruned > 0:
                venue.wlk_lines = venue.wlk_lines[n_pruned:]

            new_lines_data = data.get("new_lines") or []
            for line in new_lines_data:
                text = (line.get("text") or "").strip()
                if not text:
                    continue

                seq = venue.next_sequence
                venue.next_sequence += 1
                venue.wlk_lines.append(line)

                segment = self._make_segment(seq, text, line)
                await distribution_manager.broadcast(venue.venue_id, segment)
                await self._store(segment)

            # Verify sync with WLK's expected line count.
            n_lines = data.get("n_lines")
            if n_lines is not None and len(venue.wlk_lines) != n_lines:
                log.warning(
                    "[%s] diff sync error: local %d lines but WLK expects %d; "
                    "dropping connection to force re-sync via snapshot",
                    venue.venue_id, len(venue.wlk_lines), n_lines,
                )
                await venue.wlk.drop(venue.wlk.ws)
                return

            log.info(
                "[%s] diff: pruned %d, added %d, total %d, buffer=%r",
                venue.venue_id, n_pruned, len(new_lines_data), len(venue.wlk_lines),
                data.get("buffer_transcription", ""),
            )

        else:
            return

        buffer = (data.get("buffer_transcription") or "").strip()
        if buffer != venue.last_tentative:
            venue.last_tentative = buffer
            await distribution_manager.broadcast(
                venue.venue_id,
                {
                    "id": str(uuid.uuid4()),
                    "session_id": venue.session_id,
                    "venue_id": venue.venue_id,
                    "type": "tentative",
                    "text": buffer,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )

    def _make_segment(self, seq: int, text: str, line: dict) -> dict:
        return {
            "id": str(uuid.uuid4()),
            "session_id": self.venue.session_id,
            "venue_id": self.venue.venue_id,
            "sequence": seq,
            "type": "committed",
            "text": text,
            "start_time": _parse_timestamp(line.get("start")),
            "end_time": _parse_timestamp(line.get("end")),
            "timestamp": datetime.now(UTC).isoformat(),
        }

    async def _store(self, seg: dict) -> None:
        async with get_db_session() as db:
            db.add(
                Segment(
                    id=seg["id"],
                    session_id=seg["session_id"],
                    sequence=seg["sequence"],
                    segment_type=seg["type"],
                    text=seg["text"],
                    start_time=seg["start_time"],
                    end_time=seg["end_time"],
                )
            )

class TranscriptionManager:
    """Per-venue WLK bridge with independent send/receive loops."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.venues: dict[str, VenueSession] = {}

    async def start(self) -> None:
        pass  # WLK is external; nothing to start at manager level.

    async def stop(self) -> None:
        for venue_id in list(self.venues):
            await self.end_session(venue_id)

    async def start_session(self, venue_id: str, title: Optional[str] = None) -> str:
        if venue_id in self.venues:
            await self.end_session(venue_id)

        session_id = str(uuid.uuid4())
        async with get_db_session() as db:
            db.add(Session(id=session_id, venue_id=venue_id, title=title))

        venue = VenueSession(venue_id=venue_id, session_id=session_id)
        self.venues[venue_id] = venue
        venue.streamer = AudioStreamer(venue, self.settings)
        venue.processor = TranscriptionProcessor(venue, self.settings)
        await venue.streamer.start()
        await venue.processor.start()
        log.info("[%s] session %s started", venue_id, session_id)
        return session_id

    async def end_session(self, venue_id: str) -> None:
        venue = self.venues.pop(venue_id, None)
        if venue is None:
            return

        log.info("[%s] ending session %s", venue_id, venue.session_id)

        venue.stop_event.set()
        with contextlib.suppress(asyncio.QueueFull):
            venue.audio_queue.put_nowait(None)

        if venue.streamer:
            await venue.streamer.stop()
        if venue.processor:
            await venue.processor.stop()
            await venue.wlk.close()

        async with get_db_session() as db:
            await db.execute(
                update(Session)
                .where(Session.id == venue.session_id)
                .values(ended_at=datetime.now(UTC))
            )

        await distribution_manager.broadcast(
            venue_id,
            {
                "type": "session_end",
                "session_id": venue.session_id,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )

    async def process_audio(self, venue_id: str, audio: bytes) -> None:
        venue = self.venues.get(venue_id)
        if venue is None:
            raise ValueError(f"No active session for venue: {venue_id}")
        try:
            venue.audio_queue.put_nowait(audio)
        except asyncio.QueueFull:
            log.warning("[%s] audio queue full, dropping %d bytes", venue_id, len(audio))

    def has_active_session(self, venue_id: str) -> bool:
        return venue_id in self.venues

    def get_session_id(self, venue_id: str) -> Optional[str]:
        v = self.venues.get(venue_id)
        return v.session_id if v else None
