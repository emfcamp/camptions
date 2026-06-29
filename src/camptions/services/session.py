"""Shared session state and WhisperLive connection management."""

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

import websockets

log = logging.getLogger(__name__)

_RECONNECT_MAX_DELAY = 15
_SHUTDOWN_TIMEOUT = 3.0
_QUEUE_MAX = 600  # ~60 s of 100 ms chunks; drop on overflow


async def sleep_or_stop(stop_event: asyncio.Event, seconds: float) -> bool:
    """Sleep for `seconds`, or return True immediately if stop_event fires."""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
        return True
    except asyncio.TimeoutError:
        return False


async def await_or_cancel(
    task: Optional[asyncio.Task],
    label: str,
    venue_id: str,
    timeout: float = _SHUTDOWN_TIMEOUT,
) -> None:
    if task is None or task.done():
        return
    try:
        await asyncio.wait_for(task, timeout=timeout)
    except asyncio.TimeoutError:
        log.warning("[%s] %s didn't exit cleanly, cancelling", venue_id, label)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


HandshakeFn = Callable[[Any], Awaitable[None]]


class WLConnection:
    """Owns a single WebSocket to WhisperLive; shared between send and recv loops.

    `ensure()` connects, runs the optional handshake (e.g. send config JSON +
    wait for SERVER_READY), and only then fires `on_state_change(True)` — so
    "ready" means "WL is fully handshaked and can transcribe."
    """

    def __init__(self) -> None:
        self._ws: Optional[Any] = None
        self._lock = asyncio.Lock()
        self.on_state_change: Optional[Callable[[bool], Awaitable[None]]] = None
        # Set once at session start. Both send and recv loops call ensure();
        # whichever wins the connection race runs this handshake before the
        # other loop sees the ws as ready.
        self.handshake: Optional[HandshakeFn] = None

    @property
    def ws(self) -> Optional[Any]:
        return self._ws

    async def _fire(self, ready: bool) -> None:
        cb = self.on_state_change
        if cb is None:
            return
        try:
            await cb(ready)
        except Exception:
            log.exception("WL state-change callback failed")

    async def ensure(self, url: str, stop_event: asyncio.Event) -> Optional[Any]:
        """Return the current WS, or connect (+ handshake, with backoff) until stop fires."""
        connected_ws: Optional[Any] = None
        async with self._lock:
            if self._ws is not None:
                return self._ws

            delay = 1
            while not stop_event.is_set():
                try:
                    ws = await websockets.connect(
                        url,
                        max_size=None,
                        ping_interval=20,
                        open_timeout=10,
                    )
                except Exception as e:
                    log.warning(
                        "WL unreachable (%s: %s); retrying in %ds",
                        type(e).__name__, e, delay,
                    )
                    if await sleep_or_stop(stop_event, delay):
                        return None
                    delay = min(delay * 2, _RECONNECT_MAX_DELAY)
                    continue

                if self.handshake is not None:
                    try:
                        await self.handshake(ws)
                    except Exception as e:
                        log.warning(
                            "WL handshake failed (%s: %s); retrying in %ds",
                            type(e).__name__, e, delay,
                        )
                        with contextlib.suppress(Exception):
                            await ws.close()
                        if await sleep_or_stop(stop_event, delay):
                            return None
                        delay = min(delay * 2, _RECONNECT_MAX_DELAY)
                        continue

                self._ws = ws
                connected_ws = ws
                log.info("WL connected at %s", url)
                break

        if connected_ws is None:
            return None
        await self._fire(True)
        return connected_ws

    async def drop(self, ws: Any) -> None:
        """Discard `ws` if it's still the current connection and close it."""
        dropped = False
        async with self._lock:
            if self._ws is ws:
                self._ws = None
                dropped = True
                with contextlib.suppress(Exception):
                    await ws.close()
        if dropped:
            await self._fire(False)

    async def close(self) -> None:
        """Unconditionally close the current connection."""
        closed = False
        async with self._lock:
            if self._ws is not None:
                ws, self._ws = self._ws, None
                closed = True
                with contextlib.suppress(Exception):
                    await ws.close()
        if closed:
            await self._fire(False)


@dataclass
class VenueSession:
    venue_id: str
    session_id: str
    audio_queue: asyncio.Queue = field(
        default_factory=lambda: asyncio.Queue(maxsize=_QUEUE_MAX)
    )
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    wl: WLConnection = field(default_factory=WLConnection)

    # Audio chunks dropped because the queue was full.
    audio_drops: int = 0

    # True while the WL WebSocket is up AND handshaked. Combined with
    # `Pi connected` (this session existing in the manager) to compute
    # effective live state.
    wl_ready: bool = False

    # time.monotonic() value when the current WL ws opened — used by the
    # send loop to decide when to proactively reconnect (WL has a
    # --max_connection_time cap, default 1 h).
    ws_opened_at: float = 0.0

    # Monotonic per-session sequence counter for caption segments.
    sequence: int = 0

    # Deduplicates committed segments across WL reconnects within one Pi
    # session: WL re-emits recent segments when it restarts (because we
    # replay the audio ring), and we use the segment's `start` timestamp
    # to skip re-broadcasting ones we've already finalised.
    committed_starts: set[float] = field(default_factory=set)

    # Last tentative text broadcast.
    last_tentative: str = ""

    # Task handles set by AudioStreamer / TranscriptionProcessor.
    send_task: Optional[asyncio.Task] = None
    recv_task: Optional[asyncio.Task] = None

    streamer: Optional[Any] = None
    processor: Optional[Any] = None
