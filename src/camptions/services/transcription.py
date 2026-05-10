"""Transcription service — bridges Pi audio to a WhisperLiveKit sidecar.

Per-venue lifecycle:

    Pi audio WS  ──►  process_audio()  ──►  audio_queue
                                                  │
                                                  ▼
                                         _send_loop ── owns its
                                                       reconnect to WLK
                                                          │
                                                          ▼
                                                 ┌───── WLK WS ─────┐
                                                          ▲
                                                          │
                                         _recv_loop ── owns its
                                                       reconnect to WLK
                                                          │
                                                          ▼
                                              distribution + DB

Send and receive are fully decoupled. Each runs as a long-lived task that
manages its own connection state. WLK going away does not end the session —
audio keeps queueing, the loops reconnect, and transcription resumes.

Session ends only when end_session() is called (Pi disconnect or shutdown).
"""

import asyncio
import contextlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Optional

import websockets
from sqlalchemy import delete, update

from ..config import Settings
from ..database import get_db_session
from ..models import Segment, Session
from .distribution import distribution_manager

log = logging.getLogger(__name__)

# Pi captures at 16 kHz S16_LE mono via ALSA's plughw layer, which is
# exactly what WLK wants in --pcm-input mode — no transcoding needed.
_QUEUE_MAX = 200          # ~10 s of 100 ms chunks; drop on overflow
_RECONNECT_MAX_DELAY = 15
_SHUTDOWN_TIMEOUT = 3.0


def _parse_timestamp(value: Any) -> Optional[float]:
    """WLK serialises segment times as 'H:MM:SS.cc' strings; convert to
    float seconds. Already-numeric values pass through unchanged."""
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


@dataclass
class VenueSession:
    venue_id: str
    session_id: str
    audio_queue: asyncio.Queue = field(
        default_factory=lambda: asyncio.Queue(maxsize=_QUEUE_MAX)
    )
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    ws: Optional[Any] = None
    ws_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    sequence: int = 0  # Not used anymore, kept for compatibility
    # Diff mode tracking:
    #   - lines: the current list of committed segments with their sequences
    #   - last_tentative: the last buffer_transcription we broadcast,
    #     to avoid spamming identical updates
    # Both reset on every fresh WLK connection (snapshot).
    lines: list[dict] = field(default_factory=list)
    last_tentative: str = ""
    streamer: Optional["AudioStreamer"] = None
    processor: Optional["TranscriptionProcessor"] = None


class AudioStreamer:
    """Handles sending audio to WLK and managing send connection."""

    def __init__(self, venue: VenueSession, settings: Settings) -> None:
        self.venue = venue
        self.settings = settings

    async def start(self) -> None:
        self.venue.send_task = asyncio.create_task(self._send_loop(), name=f"send:{self.venue.venue_id}")

    async def stop(self) -> None:
        if self.venue.send_task:
            await self._await_or_cancel(self.venue.send_task, "sender", self.venue.venue_id)

    async def _ensure_ws(self) -> Optional[Any]:
        async with self.venue.ws_lock:
            if self.venue.ws is not None:
                return self.venue.ws

            delay = 1
            while not self.venue.stop_event.is_set():
                try:
                    ws = await websockets.connect(
                        self.settings.wlk_url + "?mode=diff",
                        max_size=None,
                        ping_interval=20,
                        open_timeout=10,
                    )
                except Exception as e:
                    log.warning(
                        "[%s] WLK unreachable (%s: %s); retrying in %ds",
                        self.venue.venue_id, type(e).__name__, e, delay,
                    )
                    if await self._sleep_or_stop(delay):
                        return None
                    delay = min(delay * 2, _RECONNECT_MAX_DELAY)
                    continue

                self.venue.ws = ws
                self.venue.lines.clear()
                self.venue.last_tentative = ""
                log.info("[%s] WLK connected at %s", self.venue.venue_id, self.settings.wlk_url)
                return ws

            return None

    async def _drop_ws(self, ws: Any) -> None:
        async with self.venue.ws_lock:
            if self.venue.ws is ws:
                self.venue.ws = None
                with contextlib.suppress(Exception):
                    await ws.close()

    async def _sleep_or_stop(self, seconds: float) -> bool:
        try:
            await asyncio.wait_for(self.venue.stop_event.wait(), timeout=seconds)
            return True
        except asyncio.TimeoutError:
            return False

    async def _await_or_cancel(self, task: Optional[asyncio.Task], label: str, vid: str) -> None:
        if task is None or task.done():
            return
        try:
            await asyncio.wait_for(task, timeout=_SHUTDOWN_TIMEOUT)
        except asyncio.TimeoutError:
            log.warning("[%s] %s didn't exit cleanly, cancelling", vid, label)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def _send_loop(self) -> None:
        chunks_total = 0

        while not self.venue.stop_event.is_set():
            ws = await self._ensure_ws()
            if ws is None:
                break

            try:
                while not self.venue.stop_event.is_set():
                    chunk = await self.venue.audio_queue.get()
                    if chunk is None:
                        await ws.send(b"")
                        log.info(
                            "[%s] sender: clean EOS after %d chunks",
                            self.venue.venue_id, chunks_total,
                        )
                        return

                    await ws.send(chunk)
                    chunks_total += 1
            except websockets.ConnectionClosed as e:
                log.warning(
                    "[%s] sender: WLK connection closed (%s); will reconnect",
                    self.venue.venue_id, e,
                )
                await self._drop_ws(ws)
            except Exception:
                log.exception("[%s] sender error; will reconnect", self.venue.venue_id)
                await self._drop_ws(ws)
                if await self._sleep_or_stop(1):
                    break

        log.info("[%s] sender exiting after %d chunks", self.venue.venue_id, chunks_total)


class TranscriptionProcessor:
    """Handles receiving and processing transcription responses from WLK."""

    def __init__(self, venue: VenueSession, settings: Settings) -> None:
        self.venue = venue
        self.settings = settings

    async def start(self) -> None:
        self.venue.recv_task = asyncio.create_task(self._recv_loop(), name=f"recv:{self.venue.venue_id}")

    async def stop(self) -> None:
        if self.venue.recv_task:
            await self._await_or_cancel(self.venue.recv_task, "receiver", self.venue.venue_id)

    async def _ensure_ws(self) -> Optional[Any]:
        async with self.venue.ws_lock:
            if self.venue.ws is not None:
                return self.venue.ws

            delay = 1
            while not self.venue.stop_event.is_set():
                try:
                    ws = await websockets.connect(
                        self.settings.wlk_url + "?mode=diff",
                        max_size=None,
                        ping_interval=20,
                        open_timeout=10,
                    )
                except Exception as e:
                    log.warning(
                        "[%s] WLK unreachable (%s: %s); retrying in %ds",
                        self.venue.venue_id, type(e).__name__, e, delay,
                    )
                    if await self._sleep_or_stop(delay):
                        return None
                    delay = min(delay * 2, _RECONNECT_MAX_DELAY)
                    continue

                self.venue.ws = ws
                self.venue.lines.clear()
                self.venue.last_tentative = ""
                log.info("[%s] WLK connected at %s", self.venue.venue_id, self.settings.wlk_url)
                return ws

            return None

    async def _drop_ws(self, ws: Any) -> None:
        async with self.venue.ws_lock:
            if self.venue.ws is ws:
                self.venue.ws = None
                with contextlib.suppress(Exception):
                    await ws.close()

    async def _close_ws(self) -> None:
        async with self.venue.ws_lock:
            if self.venue.ws is not None:
                ws, self.venue.ws = self.venue.ws, None
                with contextlib.suppress(Exception):
                    await ws.close()

    async def _sleep_or_stop(self, seconds: float) -> bool:
        try:
            await asyncio.wait_for(self.venue.stop_event.wait(), timeout=seconds)
            return True
        except asyncio.TimeoutError:
            return False

    async def _await_or_cancel(self, task: Optional[asyncio.Task], label: str, vid: str) -> None:
        if task is None or task.done():
            return
        try:
            await asyncio.wait_for(task, timeout=_SHUTDOWN_TIMEOUT)
        except asyncio.TimeoutError:
            log.warning("[%s] %s didn't exit cleanly, cancelling", vid, label)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def _recv_loop(self) -> None:
        msg_total = 0

        while not self.venue.stop_event.is_set():
            ws = await self._ensure_ws()
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
                            self.venue.venue_id, raw[:120],
                        )
                        continue

                    mtype = data.get("type")
                    if mtype == "config":
                        continue
                    if mtype == "ready_to_stop":
                        log.info("[%s] receiver: WLK ready_to_stop", self.venue.venue_id)
                        return  # session is shutting down

                    await self._broadcast(data)

                # async for ended without an exception → server closed gracefully.
                log.info("[%s] receiver: WS closed by WLK; reconnecting", self.venue.venue_id)
                await self._drop_ws(ws)
            except websockets.ConnectionClosed as e:
                log.warning(
                    "[%s] receiver: WLK connection closed (%s); will reconnect",
                    self.venue.venue_id, e,
                )
                await self._drop_ws(ws)
            except Exception:
                log.exception("[%s] receiver error; will reconnect", self.venue.venue_id)
                await self._drop_ws(ws)
                if await self._sleep_or_stop(1):
                    break

        log.info("[%s] receiver exiting after %d messages", self.venue.venue_id, msg_total)

    async def _broadcast(self, data: dict) -> None:
        """Process snapshot or diff messages from WLK diff mode.

        Snapshot: initializes lines state. Diff: applies pruning
        (broadcasting removal) and new lines (broadcasting additions).
        buffer_transcription is always broadcast as tentative.
        """

        msg_type = data.get("type")

        # Handle snapshot: reset state with full lines list, assign sequences
        if msg_type == "snapshot":
            lines_data = data.get("lines") or []
            self.venue.lines = []
            for i, line in enumerate(lines_data):
                text = (line.get("text") or "").strip()
                if not text:
                    continue
                line_dict = {"sequence": i + 1, **line}
                self.venue.lines.append(line_dict)
                segment = {
                    "id": str(uuid.uuid4()),
                    "session_id": self.venue.session_id,
                    "venue_id": self.venue.venue_id,
                    "sequence": line_dict["sequence"],
                    "type": "committed",
                    "text": text,
                    "start_time": _parse_timestamp(line.get("start")),
                    "end_time": _parse_timestamp(line.get("end")),
                    "timestamp": datetime.now(UTC).isoformat(),
                }
                await distribution_manager.broadcast(self.venue.venue_id, segment)
                await self._store(segment)
            self.venue.last_tentative = ""
            log.info(
                "[%s] snapshot: %d lines, buffer=%r",
                self.venue.venue_id, len(self.venue.lines), data.get("buffer_transcription", ""),
            )

        # Handle diff: apply pruning and new lines
        elif msg_type == "diff":
            n_pruned = data.get("lines_pruned", 0)
            if n_pruned > 0:
                # Broadcast prune message to frontend
                await distribution_manager.broadcast(
                    self.venue.venue_id,
                    {
                        "type": "prune_segments",
                        "count": n_pruned,
                        "session_id": self.venue.session_id,
                        "timestamp": datetime.now(UTC).isoformat(),
                    },
                )
                self.venue.lines = self.venue.lines[n_pruned:]

            new_lines_data = data.get("new_lines") or []
            new_line_dicts = []
            for i, line in enumerate(new_lines_data):
                text = (line.get("text") or "").strip()
                if not text:
                    continue
                sequence = len(self.venue.lines) + len(new_line_dicts) + 1
                line_dict = {"sequence": sequence, **line}
                new_line_dicts.append(line_dict)
                segment = {
                    "id": str(uuid.uuid4()),
                    "session_id": self.venue.session_id,
                    "venue_id": self.venue.venue_id,
                    "sequence": sequence,
                    "type": "committed",
                    "text": text,
                    "start_time": _parse_timestamp(line.get("start")),
                    "end_time": _parse_timestamp(line.get("end")),
                    "timestamp": datetime.now(UTC).isoformat(),
                }
                await distribution_manager.broadcast(self.venue.venue_id, segment)
                await self._store(segment)

            self.venue.lines.extend(new_line_dicts)
            log.info(
                "[%s] diff: pruned %d, added %d, total %d, buffer=%r",
                self.venue.venue_id, n_pruned, len(new_line_dicts), len(self.venue.lines),
                data.get("buffer_transcription", ""),
            )

        else:
            # Silently skip unknown message types
            return

        # Broadcast buffer_transcription as tentative (if it changed)
        buffer = (data.get("buffer_transcription") or "").strip()
        if buffer and buffer != self.venue.last_tentative:
            self.venue.last_tentative = buffer
            self.venue.sequence += 1  # Still increment for tentative
            await distribution_manager.broadcast(
                self.venue.venue_id,
                {
                    "id": str(uuid.uuid4()),
                    "session_id": self.venue.session_id,
                    "venue_id": self.venue.venue_id,
                    "sequence": self.venue.sequence,
                    "type": "tentative",
                    "text": buffer,
                    "start_time": None,
                    "end_time": None,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )

    async def _store(self, seg: dict) -> None:
        async with get_db_session() as db:
            # Delete any existing segment with same session_id and sequence
            await db.execute(
                delete(Segment).where(
                    Segment.session_id == seg["session_id"],
                    Segment.sequence == seg["sequence"]
                )
            )
            # Add the new segment
            db.add(
                Segment(
                    id=seg["id"],
                    session_id=seg["session_id"],
                    sequence=seg["sequence"],
                    segment_type=seg["type"],
                    text=seg["text"],
                    start_time=seg["start_time"],
                    end_time=seg.get("end_time"),
                )
            )


class TranscriptionManager:
    """Per-venue WLK bridge with independent send/receive loops."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.venues: dict[str, VenueSession] = {}

    async def start(self) -> None:
        """No-op — WLK is external."""

    async def stop(self) -> None:
        for venue_id in list(self.venues):
            await self.end_session(venue_id)

    # ─── public API ────────────────────────────────────────────────────

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

        # 1. Tell loops to stop. Sentinel wakes the sender if it's blocked
        #    on an empty queue; stop_event aborts any reconnect backoff.
        venue.stop_event.set()
        with contextlib.suppress(asyncio.QueueFull):
            venue.audio_queue.put_nowait(None)

        # 2. Stop streamer and processor
        if venue.streamer:
            await venue.streamer.stop()
        if venue.processor:
            await venue.processor.stop()

        # 3. Close WS
        if venue.processor:
            await venue.processor._close_ws()

        # 4. Persist + announce.
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
        """Enqueue an audio chunk for this venue. Non-blocking."""
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