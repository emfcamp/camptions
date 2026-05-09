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
from sqlalchemy import update

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
    sequence: int = 0
    # WLK sends snapshots: `lines` is the full accumulating list, where
    # entries can mutate (text grows, end shifts) until a newer line
    # appears past them. We track:
    #   - committed_count: how many lines we've finalised. Lines below
    #     this index are stable and have been broadcast as `committed`.
    #   - last_tentative: the last `tentative` payload we sent, so we
    #     don't spam identical updates. Tentative = text of lines[-1]
    #     plus buffer_transcription.
    # Both reset on every fresh WLK connection.
    committed_count: int = 0
    last_tentative: str = ""
    send_task: Optional[asyncio.Task] = None
    recv_task: Optional[asyncio.Task] = None


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
        venue.send_task = asyncio.create_task(
            self._send_loop(venue), name=f"send:{venue_id}"
        )
        venue.recv_task = asyncio.create_task(
            self._recv_loop(venue), name=f"recv:{venue_id}"
        )
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

        # 2. Wait for sender to drain its queue and emit b"" to WLK.
        await self._await_or_cancel(venue.send_task, "sender", venue.venue_id)

        # 3. Sender is gone, so close the WS to unblock the receiver
        #    (it might still be waiting on `async for raw in ws`).
        await self._close_ws(venue)

        # 4. Wait for receiver.
        await self._await_or_cancel(venue.recv_task, "receiver", venue.venue_id)

        # 5. Persist + announce.
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

    # ─── connection management ─────────────────────────────────────────

    async def _ensure_ws(self, venue: VenueSession) -> Optional[Any]:
        """Return a live WLK WebSocket, reconnecting if needed.

        Returns None if the venue is being shut down. Multiple callers may
        invoke this concurrently; the lock serialises connection creation
        so we only ever hold one socket per venue.
        """
        async with venue.ws_lock:
            if venue.ws is not None:
                return venue.ws

            delay = 1
            while not venue.stop_event.is_set():
                try:
                    ws = await websockets.connect(
                        self.settings.wlk_url,
                        max_size=None,
                        ping_interval=20,
                        open_timeout=10,
                    )
                except Exception as e:
                    log.warning(
                        "[%s] WLK unreachable (%s: %s); retrying in %ds",
                        venue.venue_id, type(e).__name__, e, delay,
                    )
                    if await self._sleep_or_stop(venue, delay):
                        return None
                    delay = min(delay * 2, _RECONNECT_MAX_DELAY)
                    continue

                venue.ws = ws
                venue.committed_count = 0  # fresh WLK session has no history
                venue.last_tentative = ""
                log.info("[%s] WLK connected at %s", venue.venue_id, self.settings.wlk_url)
                return ws

            return None

    async def _drop_ws(self, venue: VenueSession, ws: Any) -> None:
        """Mark the given socket as dead. Idempotent across send/recv races."""
        async with venue.ws_lock:
            if venue.ws is ws:
                venue.ws = None
                with contextlib.suppress(Exception):
                    await ws.close()

    async def _close_ws(self, venue: VenueSession) -> None:
        """Force-close the current WS, regardless of identity."""
        async with venue.ws_lock:
            if venue.ws is not None:
                ws, venue.ws = venue.ws, None
                with contextlib.suppress(Exception):
                    await ws.close()

    async def _sleep_or_stop(self, venue: VenueSession, seconds: float) -> bool:
        """Sleep, returning True if stop_event fires before timeout."""
        try:
            await asyncio.wait_for(venue.stop_event.wait(), timeout=seconds)
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

    # ─── send loop ─────────────────────────────────────────────────────

    async def _send_loop(self, venue: VenueSession) -> None:
        chunks_total = 0

        while not venue.stop_event.is_set():
            ws = await self._ensure_ws(venue)
            if ws is None:
                break

            try:
                while not venue.stop_event.is_set():
                    chunk = await venue.audio_queue.get()
                    if chunk is None:
                        await ws.send(b"")
                        log.info(
                            "[%s] sender: clean EOS after %d chunks",
                            venue.venue_id, chunks_total,
                        )
                        return

                    await ws.send(chunk)
                    chunks_total += 1
            except websockets.ConnectionClosed as e:
                log.warning(
                    "[%s] sender: WLK connection closed (%s); will reconnect",
                    venue.venue_id, e,
                )
                await self._drop_ws(venue, ws)
            except Exception:
                log.exception("[%s] sender error; will reconnect", venue.venue_id)
                await self._drop_ws(venue, ws)
                if await self._sleep_or_stop(venue, 1):
                    break

        log.info("[%s] sender exiting after %d chunks", venue.venue_id, chunks_total)

    # ─── receive loop ──────────────────────────────────────────────────

    async def _recv_loop(self, venue: VenueSession) -> None:
        msg_total = 0

        while not venue.stop_event.is_set():
            ws = await self._ensure_ws(venue)
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
                        log.info("[%s] receiver: WLK ready_to_stop", venue.venue_id)
                        return  # session is shutting down

                    await self._broadcast(venue, data)

                # async for ended without an exception → server closed gracefully.
                log.info("[%s] receiver: WS closed by WLK; reconnecting", venue.venue_id)
                await self._drop_ws(venue, ws)
            except websockets.ConnectionClosed as e:
                log.warning(
                    "[%s] receiver: WLK connection closed (%s); will reconnect",
                    venue.venue_id, e,
                )
                await self._drop_ws(venue, ws)
            except Exception:
                log.exception("[%s] receiver error; will reconnect", venue.venue_id)
                await self._drop_ws(venue, ws)
                if await self._sleep_or_stop(venue, 1):
                    break

        log.info("[%s] receiver exiting after %d messages", venue.venue_id, msg_total)

    # ─── result fan-out ────────────────────────────────────────────────

    async def _broadcast(self, venue: VenueSession, data: dict) -> None:
        """Emit committed/tentative captions from a WLK snapshot.

        WLK's `lines` is mutable: each entry's text and end-time can grow
        between snapshots, until a newer line appears past it (silence
        boundary, speaker change, punctuation). Strategy:

          - Lines [0..N-1] are stable now that line[N] exists → broadcast
            each as `committed` exactly once (then store in DB).
          - Line [N] is still being refined. Its text plus any
            `buffer_transcription` is the live tip → broadcast as
            `tentative`, but only when it actually changes.
          - With nothing in `lines`, just emit `buffer_transcription` as
            tentative.

        WLK reconnects reset `committed_count` and `last_tentative`, so
        the new session starts the cycle over.
        """

        lines = data.get("lines") or []
        print(lines)
        buffer_text = (data.get("buffer_transcription") or "").strip()
        last_idx = len(lines) - 1

        # Commit any lines that are now superseded.
        while venue.committed_count < last_idx:
            seg = lines[venue.committed_count]
            venue.committed_count += 1
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            venue.sequence += 1
            segment = {
                "id": str(uuid.uuid4()),
                "session_id": venue.session_id,
                "venue_id": venue.venue_id,
                "sequence": venue.sequence,
                "type": "committed",
                "text": text,
                "start_time": _parse_timestamp(seg.get("start")),
                "end_time": _parse_timestamp(seg.get("end")),
                "timestamp": datetime.now(UTC).isoformat(),
            }
            log.info("[%s] committed: %r", venue.venue_id, text)
            await distribution_manager.broadcast(venue.venue_id, segment)
            await self._store(segment)

        # Build the tentative tip = last line's text + buffer_transcription.
        live_parts: list[str] = []
        if last_idx >= 0:
            live_text = (lines[last_idx].get("text") or "").strip()
            if live_text:
                live_parts.append(live_text)
        if buffer_text:
            live_parts.append(buffer_text)
        live = " ".join(live_parts)

        if live and live != venue.last_tentative:
            venue.last_tentative = live
            venue.sequence += 1
            await distribution_manager.broadcast(
                venue.venue_id,
                {
                    "id": str(uuid.uuid4()),
                    "session_id": venue.session_id,
                    "venue_id": venue.venue_id,
                    "sequence": venue.sequence,
                    "type": "tentative",
                    "text": live,
                    "start_time": None,
                    "end_time": None,
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
                    end_time=seg.get("end_time"),
                )
            )
