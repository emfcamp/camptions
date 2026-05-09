"""Transcription service — bridges Pi audio to a WhisperLive sidecar.

Per-venue lifecycle:

    Pi audio WS  ──►  process_audio()  ──►  audio_queue
                                                  │
                                                  ▼
                                         _send_loop ── owns its
                                                       reconnect to WL
                                                          │
                                                          ▼
                                                 ┌───── WL WS ─────┐
                                                          ▲
                                                          │
                                         _recv_loop ── owns its
                                                       reconnect to WL
                                                          │
                                                          ▼
                                              distribution + DB

Send and receive are fully decoupled. Each runs as a long-lived task that
manages its own connection state. WL going away does not end the session —
audio keeps queueing, the loops reconnect, and transcription resumes.

The send loop proactively reconnects every wl_reconnect_interval seconds (default
55 min) to stay well under WL's max_connection_time hard cap. On each new WL
connection, the last _RING_MAXCHUNKS of sent audio are replayed so Whisper has
context for the current sentence before live audio resumes.

committed_starts persists across WL reconnects (resets only on Pi disconnect)
so ring-buffer replays don't re-emit segments already broadcast in this session.

Session ends only when end_session() is called (Pi disconnect or shutdown).
"""

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections import deque
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

_QUEUE_MAX = 600          # ~60 s of 100 ms chunks; drop on overflow
_RING_MAXCHUNKS = 50      # ~5 s of audio replayed on each WL reconnect
_RECONNECT_MAX_DELAY = 15
_SHUTDOWN_TIMEOUT = 3.0


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
    ws_opened_at: float = 0.0
    sequence: int = 0
    committed_starts: set = field(default_factory=set)
    last_tentative: str = ""
    send_task: Optional[asyncio.Task] = None
    recv_task: Optional[asyncio.Task] = None


class TranscriptionManager:
    """Per-venue WhisperLive bridge with independent send/receive loops."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.venues: dict[str, VenueSession] = {}

    async def start(self) -> None:
        pass

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

        venue.stop_event.set()
        with contextlib.suppress(asyncio.QueueFull):
            venue.audio_queue.put_nowait(None)

        await self._await_or_cancel(venue.send_task, "sender", venue.venue_id)
        await self._close_ws(venue)
        await self._await_or_cancel(venue.recv_task, "receiver", venue.venue_id)

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

    # ─── connection management ─────────────────────────────────────────

    async def _ensure_ws(self, venue: VenueSession) -> Optional[Any]:
        """Return a live WL WebSocket, reconnecting if needed.

        Sends the WhisperLive handshake JSON and waits for SERVER_READY before
        returning so callers can start sending audio immediately. The lock
        serialises connection creation so send and recv loops share one socket.
        """
        async with venue.ws_lock:
            if venue.ws is not None:
                return venue.ws

            delay = 1
            while not venue.stop_event.is_set():
                try:
                    ws = await websockets.connect(
                        self.settings.wl_url,
                        max_size=None,
                        ping_interval=20,
                        open_timeout=10,
                    )
                except Exception as e:
                    log.warning(
                        "[%s] WL unreachable (%s: %s); retrying in %ds",
                        venue.venue_id, type(e).__name__, e, delay,
                    )
                    if await self._sleep_or_stop(venue, delay):
                        return None
                    delay = min(delay * 2, _RECONNECT_MAX_DELAY)
                    continue

                try:
                    await ws.send(json.dumps({
                        "uid": f"{venue.venue_id}-{venue.session_id}",
                        "language": self.settings.whisper_language,
                        "task": "transcribe",
                        "model": self.settings.whisper_model,
                        "use_vad": self.settings.whisper_use_vad,
                        "send_last_n_segments": 10,
                    }))
                    await asyncio.wait_for(self._wait_for_ready(ws, venue.venue_id), timeout=30)
                except Exception as e:
                    log.warning("[%s] WL handshake failed (%s); retrying", venue.venue_id, e)
                    with contextlib.suppress(Exception):
                        await ws.close()
                    if await self._sleep_or_stop(venue, delay):
                        return None
                    delay = min(delay * 2, _RECONNECT_MAX_DELAY)
                    continue

                venue.ws = ws
                venue.ws_opened_at = time.monotonic()
                venue.last_tentative = ""
                log.info("[%s] WL connected", venue.venue_id)
                return ws

        return None

    async def _wait_for_ready(self, ws: Any, venue_id: str) -> None:
        """Consume messages until SERVER_READY. Raises on WAIT/ERROR."""
        async for raw in ws:
            if isinstance(raw, bytes):
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            status = data.get("status")
            if status == "WAIT":
                raise RuntimeError(f"WL server full (wait {data.get('message', '?')} min)")
            if status == "ERROR":
                raise RuntimeError(f"WL error: {data.get('message')}")
            if data.get("message") == "SERVER_READY":
                log.info("[%s] WL SERVER_READY", venue_id)
                return

    async def _drop_ws(self, venue: VenueSession, ws: Any) -> None:
        async with venue.ws_lock:
            if venue.ws is ws:
                venue.ws = None
                with contextlib.suppress(Exception):
                    await ws.close()

    async def _close_ws(self, venue: VenueSession) -> None:
        async with venue.ws_lock:
            if venue.ws is not None:
                ws, venue.ws = venue.ws, None
                with contextlib.suppress(Exception):
                    await ws.close()

    async def _sleep_or_stop(self, venue: VenueSession, seconds: float) -> bool:
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
        ring: deque[bytes] = deque(maxlen=_RING_MAXCHUNKS)

        while not venue.stop_event.is_set():
            ws = await self._ensure_ws(venue)
            if ws is None:
                break

            # Replay recent audio so Whisper has sentence context (empty on first connect)
            ring_ok = True
            for chunk in list(ring):
                try:
                    await ws.send(chunk)
                except Exception:
                    await self._drop_ws(venue, ws)
                    ring_ok = False
                    break
            if not ring_ok:
                continue

            try:
                while not venue.stop_event.is_set():
                    remaining = (venue.ws_opened_at + self.settings.wl_reconnect_interval
                                 - time.monotonic())
                    if remaining <= 0:
                        log.info(
                            "[%s] proactive reconnect after %.0fs",
                            venue.venue_id, self.settings.wl_reconnect_interval,
                        )
                        await self._drop_ws(venue, ws)
                        break

                    try:
                        chunk = await asyncio.wait_for(
                            venue.audio_queue.get(),
                            timeout=min(remaining, 30.0),
                        )
                    except asyncio.TimeoutError:
                        continue

                    if chunk is None:
                        await ws.send("END_OF_AUDIO")
                        log.info("[%s] sender: EOS after %d chunks", venue.venue_id, chunks_total)
                        return

                    await ws.send(chunk)
                    ring.append(chunk)
                    chunks_total += 1

            except websockets.ConnectionClosed as e:
                log.warning("[%s] sender: WL closed (%s); reconnecting", venue.venue_id, e)
                await self._drop_ws(venue, ws)
            except Exception:
                log.exception("[%s] sender error; reconnecting", venue.venue_id)
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
                        log.warning("[%s] non-JSON: %r", venue.venue_id, raw[:120])
                        continue

                    if "status" in data or data.get("message") == "SERVER_READY":
                        continue
                    if "segments" not in data:
                        continue

                    await self._broadcast(venue, data)

                log.info("[%s] receiver: WS closed by WL; reconnecting", venue.venue_id)
                await self._drop_ws(venue, ws)
            except websockets.ConnectionClosed as e:
                log.warning("[%s] receiver: WL closed (%s); reconnecting", venue.venue_id, e)
                await self._drop_ws(venue, ws)
            except Exception:
                log.exception("[%s] receiver error; reconnecting", venue.venue_id)
                await self._drop_ws(venue, ws)
                if await self._sleep_or_stop(venue, 1):
                    break

        log.info("[%s] receiver exiting after %d messages", venue.venue_id, msg_total)

    # ─── result fan-out ────────────────────────────────────────────────

    async def _broadcast(self, venue: VenueSession, data: dict) -> None:
        """Emit committed/tentative captions from a WhisperLive segment response.

        WL sends up to send_last_n_segments entries per message. Segments with
        completed=true are stable; the last entry may be completed=false (tentative).
        committed_starts deduplicates across WL reconnects so ring-buffer replays
        don't re-emit segments already broadcast in this Pi session.
        """
        segments = data.get("segments") or []
        if not segments:
            return

        for seg in segments:
            if not seg.get("completed"):
                continue
            start = seg.get("start")
            if start in venue.committed_starts:
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
                "start_time": start,
                "end_time": seg.get("end"),
                "timestamp": datetime.now(UTC).isoformat(),
            }
            log.info("[%s] committed: %r", venue.venue_id, text)
            await distribution_manager.broadcast(venue.venue_id, segment)
            await self._store(segment)

        last = segments[-1]
        if not last.get("completed"):
            text = (last.get("text") or "").strip()
            if text and text != venue.last_tentative:
                venue.last_tentative = text
                venue.sequence += 1
                await distribution_manager.broadcast(
                    venue.venue_id,
                    {
                        "id": str(uuid.uuid4()),
                        "session_id": venue.session_id,
                        "venue_id": venue.venue_id,
                        "sequence": venue.sequence,
                        "type": "tentative",
                        "text": text,
                        "start_time": last.get("start"),
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
