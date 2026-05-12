"""Shared session state and WLK connection management."""

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

import websockets

log = logging.getLogger(__name__)

_RECONNECT_MAX_DELAY = 15
_SHUTDOWN_TIMEOUT = 3.0
_QUEUE_MAX = 200  # ~10 s of 100 ms chunks


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


class WLKConnection:
    """Owns a single WebSocket to WLK; shared between send and receive loops.

    Optional `on_state_change(ready)` callback fires when the underlying WS
    transitions to/from connected, so the owning session can reflect WLK
    availability to caption subscribers.
    """

    def __init__(self) -> None:
        self._ws: Optional[Any] = None
        self._lock = asyncio.Lock()
        self.on_state_change: Optional[Callable[[bool], Awaitable[None]]] = None

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
            log.exception("WLK state-change callback failed")

    async def ensure(self, url: str, stop_event: asyncio.Event) -> Optional[Any]:
        """Return the current WS, or connect (with backoff) until stop fires."""
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
                        "WLK unreachable (%s: %s); retrying in %ds",
                        type(e).__name__, e, delay,
                    )
                    if await sleep_or_stop(stop_event, delay):
                        return None
                    delay = min(delay * 2, _RECONNECT_MAX_DELAY)
                    continue

                self._ws = ws
                connected_ws = ws
                log.info("WLK connected at %s", url)
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
    wlk: WLKConnection = field(default_factory=WLKConnection)

    # Audio chunks dropped because the queue was full.
    audio_drops: int = 0

    # True while the WLK WebSocket is up. Combined with `Pi connected` (i.e.
    # this session existing in the manager) to compute effective live state.
    wlk_ready: bool = False

    # Monotonic per-session sequence counter — never resets across WLK reconnects.
    next_sequence: int = 1

    # Maps WLK line `start` timestamp → camptions sequence. WLK's diff
    # protocol re-sends a whole line in `new_lines` when its text grows
    # (SimulStreaming) and reports it as "pruned + new" rather than an
    # in-place update, so positional tracking can't distinguish growth from
    # a genuinely new line. Keying by `start` (stable for a line's lifetime)
    # lets us reuse the same seq and have the client update the existing
    # block instead of rendering a new one.
    seq_by_start: dict[str, int] = field(default_factory=dict)

    # Last tentative text broadcast.
    last_tentative: str = ""


    # Task handles set by AudioStreamer / TranscriptionProcessor.
    send_task: Optional[asyncio.Task] = None
    recv_task: Optional[asyncio.Task] = None

    streamer: Optional[Any] = None
    processor: Optional[Any] = None
