"""Transcription service — bridges Pi audio to a WhisperLive sidecar.

Per-venue lifecycle:

    Pi audio WS  ──►  process_audio()  ──►  audio_queue
                                                  │
                                                  ▼
                                         AudioStreamer._send_loop
                                                  │   (shared WLConnection)
                                                  ▼
                                              WL WS
                                                  │
                                                  ▼
                                    TranscriptionProcessor._recv_loop
                                                  │
                                                  ▼
                                       distribution + DB

Send and receive share one WLConnection. WL going away does not end the
session — audio keeps queueing, both loops reconnect, transcription resumes.
Session ends only when end_session() is called (Pi disconnect or shutdown).
"""

import asyncio
import contextlib
import json
import logging
import time
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

_HANDSHAKE_TIMEOUT = 30.0


class TranscriptionProcessor:
    """Receives and processes transcription responses from WhisperLive."""

    def __init__(self, venue: VenueSession, settings: Settings) -> None:
        self.venue = venue
        self.settings = settings

    async def start(self) -> None:
        self.venue.recv_task = asyncio.create_task(
            self._recv_loop(), name=f"recv:{self.venue.venue_id}"
        )

    async def stop(self) -> None:
        await await_or_cancel(self.venue.recv_task, "receiver", self.venue.venue_id)

    async def _handshake(self, ws: Any) -> None:
        """Perform the WL JSON handshake on a fresh WS.

        WL expects the client to send a config JSON immediately after connect
        and then to wait for `{"message": "SERVER_READY"}` before streaming
        audio. WAIT/ERROR statuses raise so the connection backoff applies.
        """
        venue = self.venue
        settings = self.settings
        await ws.send(json.dumps({
            "uid": f"{venue.venue_id}-{venue.session_id}",
            "language": settings.whisper_language,
            "task": "transcribe",
            "model": settings.whisper_model,
            "use_vad": settings.whisper_use_vad,
            "send_last_n_segments": 10,
        }))
        await asyncio.wait_for(
            self._wait_for_ready(ws), timeout=_HANDSHAKE_TIMEOUT,
        )

    async def _wait_for_ready(self, ws: Any) -> None:
        async for raw in ws:
            if isinstance(raw, bytes):
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            status = data.get("status")
            if status == "WAIT":
                raise RuntimeError(
                    f"WL server full (wait {data.get('message', '?')} min)"
                )
            if status == "ERROR":
                raise RuntimeError(f"WL error: {data.get('message')}")
            if data.get("message") == "SERVER_READY":
                log.info("[%s] WL SERVER_READY", self.venue.venue_id)
                return

    async def _recv_loop(self) -> None:
        msg_total = 0
        venue = self.venue
        url = self.settings.wl_url

        while not venue.stop_event.is_set():
            ws = await venue.wl.ensure(url, venue.stop_event)
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

                    if "status" in data or data.get("message") == "SERVER_READY":
                        continue
                    if "segments" not in data:
                        continue

                    await self._broadcast(data)

                log.info("[%s] receiver: WS closed by WL; reconnecting", venue.venue_id)
                await venue.wl.drop(ws)
                self._reset_wl_state()

            except websockets.ConnectionClosed as e:
                log.warning(
                    "[%s] receiver: WL connection closed (%s); will reconnect",
                    venue.venue_id, e,
                )
                await venue.wl.drop(ws)
                self._reset_wl_state()
            except Exception:
                log.exception("[%s] receiver error; will reconnect", venue.venue_id)
                await venue.wl.drop(ws)
                self._reset_wl_state()
                if await sleep_or_stop(venue.stop_event, 1):
                    break

        log.info("[%s] receiver exiting after %d messages", venue.venue_id, msg_total)

    def _reset_wl_state(self) -> None:
        # `committed_starts` is preserved across WL reconnects within a Pi
        # session so the ring-buffer audio replay doesn't re-emit finalised
        # segments. Only the in-progress tentative is reset.
        self.venue.last_tentative = ""

    async def _broadcast(self, data: dict) -> None:
        """Emit committed/tentative captions from a WL segment response.

        WL sends up to send_last_n_segments per message. Entries with
        `completed: true` are stable; the final entry may be `completed: false`
        which is the in-progress tentative. `committed_starts` dedupes across
        WL reconnects so audio-ring replays don't double-emit.
        """
        venue = self.venue
        segments = data.get("segments") or []
        if not segments:
            return

        for seg in segments:
            if not seg.get("completed"):
                continue
            start = seg.get("start")
            if start is None or start in venue.committed_starts:
                continue
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            venue.committed_starts.add(start)
            venue.sequence += 1
            segment = {
                "id": str(uuid.uuid4()),
                "session_id": venue.session_id,
                "venue_id": venue.venue_id,
                "sequence": venue.sequence,
                "type": "committed",
                "text": text,
                "start_time": _as_float(start),
                "end_time": _as_float(seg.get("end")),
                "timestamp": datetime.now(UTC).isoformat(),
            }
            await distribution_manager.broadcast(venue.venue_id, segment)
            await self._store(segment)

        last = segments[-1]
        if not last.get("completed"):
            text = (last.get("text") or "").strip()
            if text and text != venue.last_tentative:
                venue.last_tentative = text
                await distribution_manager.broadcast(
                    venue.venue_id,
                    {
                        "id": str(uuid.uuid4()),
                        "session_id": venue.session_id,
                        "venue_id": venue.venue_id,
                        "type": "tentative",
                        "text": text,
                        "timestamp": datetime.now(UTC).isoformat(),
                    },
                )

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


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class TranscriptionManager:
    """Per-venue WL bridge with independent send/receive loops."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.venues: dict[str, VenueSession] = {}

    async def start(self) -> None:
        pass  # WL is external; nothing to start at manager level.

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
        venue.wl.on_state_change = lambda ready: self._on_wl_state_change(venue, ready)
        venue.streamer = AudioStreamer(venue, self.settings)
        venue.processor = TranscriptionProcessor(venue, self.settings)
        venue.wl.handshake = venue.processor._handshake
        await venue.streamer.start()
        await venue.processor.start()
        log.info("[%s] session %s started", venue_id, session_id)
        return session_id

    async def _on_wl_state_change(self, venue: VenueSession, ready: bool) -> None:
        """React to WL socket coming up (handshaked) or going down.

        Broadcasts venue_live / venue_offline so subscribers see "Live" only
        when both Pi and WL are connected and handshaked. Skipped if the
        session has already been ended (audio router broadcasts venue_offline
        itself in that case).
        """
        venue.wl_ready = ready
        if ready:
            venue.ws_opened_at = time.monotonic()
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
        """True iff both Pi and WL are currently connected for this venue."""
        v = self.venues.get(venue_id)
        return v is not None and v.wl_ready

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
            await venue.wl.close()

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
            # No active session: silently discard. Happens transiently while
            # admin toggles transcription off mid-stream, or if the Pi sends
            # a chunk after end_session(). Not an error.
            return
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
