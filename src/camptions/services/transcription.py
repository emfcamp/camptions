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
        url = self.settings.wlk_url_full

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
                        self._reset_wlk_state()
                        break

                    await self._broadcast(data)

                log.info("[%s] receiver: WS closed by WLK; reconnecting", venue.venue_id)
                await venue.wlk.drop(ws)
                self._reset_wlk_state()

            except websockets.ConnectionClosed as e:
                log.warning(
                    "[%s] receiver: WLK connection closed (%s); will reconnect",
                    venue.venue_id, e,
                )
                await venue.wlk.drop(ws)
                self._reset_wlk_state()
            except Exception:
                log.exception("[%s] receiver error; will reconnect", venue.venue_id)
                await venue.wlk.drop(ws)
                self._reset_wlk_state()
                if await sleep_or_stop(venue.stop_event, 1):
                    break

        log.info("[%s] receiver exiting after %d messages", venue.venue_id, msg_total)

    def _reset_wlk_state(self) -> None:
        # WLK reconnect: the next message will be a fresh snapshot from a new
        # WLK session whose internal `start` timestamps restart from zero, so
        # clear the start→seq map to avoid colliding with prior-session keys.
        self.venue.seq_by_start.clear()
        self.venue.last_tentative = ""

    async def _broadcast(self, data: dict) -> None:
        """Apply a WLK diff-mode update and fan out committed/tentative events.

        Snapshot is treated as fresh state (clear the start→seq map). Diff's
        `new_lines` is everything after the common prefix — WLK's protocol
        re-sends a whole line when its text grows, so we identify lines by
        their `start` timestamp rather than by position. A line whose `start`
        we've already seen reuses its existing seq so the client updates the
        existing block in place; an unseen `start` gets a fresh seq.
        """
        venue = self.venue
        mtype = data.get("type")

        if mtype == "snapshot":
            venue.seq_by_start.clear()
            lines = data.get("lines") or []
        elif mtype == "diff":
            lines = data.get("new_lines") or []
        else:
            return

        for line in lines:
            await self._emit_line(line)

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

    async def _emit_line(self, line: dict) -> None:
        """Broadcast and persist one WLK line, keyed by `start` timestamp.

        Re-use the existing seq if we've seen this `start` before (line is
        growing in place) so the client updates its rendered block; otherwise
        assign a fresh monotonic seq. Empty/silence lines (text is None or
        blank) are skipped.
        """
        venue = self.venue
        text = (line.get("text") or "").strip()
        if not text:
            return
        start = line.get("start")
        prior = venue.seq_by_start.get(start) if start else None
        if prior is not None:
            segment = self._make_segment(prior, text, line)
            await distribution_manager.broadcast(venue.venue_id, segment)
            await self._update_segment(segment)
        else:
            seq = venue.next_sequence
            venue.next_sequence += 1
            if start:
                venue.seq_by_start[start] = seq
            segment = self._make_segment(seq, text, line)
            await distribution_manager.broadcast(venue.venue_id, segment)
            await self._store(segment)

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

    async def _update_segment(self, seg: dict) -> None:
        async with get_db_session() as db:
            await db.execute(
                update(Segment)
                .where(Segment.session_id == seg["session_id"])
                .where(Segment.sequence == seg["sequence"])
                .values(text=seg["text"], end_time=seg["end_time"])
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
        venue.wlk.on_state_change = lambda ready: self._on_wlk_state_change(venue, ready)
        venue.streamer = AudioStreamer(venue, self.settings)
        venue.processor = TranscriptionProcessor(venue, self.settings)
        await venue.streamer.start()
        await venue.processor.start()
        log.info("[%s] session %s started", venue_id, session_id)
        return session_id

    async def _on_wlk_state_change(self, venue: VenueSession, ready: bool) -> None:
        """React to WLK socket coming up or going down.

        Broadcasts venue_live / venue_offline so subscribers see "Live" only
        when both Pi and WLK are connected. Skipped if the session has
        already been ended (audio router will broadcast venue_offline itself).
        """
        venue.wlk_ready = ready
        if self.venues.get(venue.venue_id) is not venue:
            return
        if ready:
            await distribution_manager.broadcast(
                venue.venue_id,
                {
                    "type": "venue_live",
                    "venue_id": venue.venue_id,
                    "session_id": venue.session_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )
        else:
            await distribution_manager.broadcast(
                venue.venue_id,
                {
                    "type": "venue_offline",
                    "venue_id": venue.venue_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )

    def is_live(self, venue_id: str) -> bool:
        """True iff both Pi and WLK are currently connected for this venue."""
        v = self.venues.get(venue_id)
        return v is not None and v.wlk_ready

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
            venue.audio_drops += 1
            log.warning("[%s] audio queue full, dropping %d bytes", venue_id, len(audio))

    def has_active_session(self, venue_id: str) -> bool:
        return venue_id in self.venues

    def get_session_id(self, venue_id: str) -> Optional[str]:
        v = self.venues.get(venue_id)
        return v.session_id if v else None
