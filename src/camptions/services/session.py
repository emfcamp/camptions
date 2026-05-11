"""Shared session state and WLK connection management."""

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

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
    """Owns a single WebSocket to WLK; shared between send and receive loops."""

    def __init__(self) -> None:
        self._ws: Optional[Any] = None
        self._lock = asyncio.Lock()

    @property
    def ws(self) -> Optional[Any]:
        return self._ws

    async def ensure(self, url: str, stop_event: asyncio.Event) -> Optional[Any]:
        """Return the current WS, or connect (with backoff) until stop fires."""
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
                log.info("WLK connected at %s", url)
                return ws

            return None

    async def drop(self, ws: Any) -> None:
        """Discard `ws` if it's still the current connection and close it."""
        async with self._lock:
            if self._ws is ws:
                self._ws = None
                with contextlib.suppress(Exception):
                    await ws.close()

    async def close(self) -> None:
        """Unconditionally close the current connection."""
        async with self._lock:
            if self._ws is not None:
                ws, self._ws = self._ws, None
                with contextlib.suppress(Exception):
                    await ws.close()


@dataclass
class VenueSession:
    venue_id: str
    session_id: str
    audio_queue: asyncio.Queue = field(
        default_factory=lambda: asyncio.Queue(maxsize=_QUEUE_MAX)
    )
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    wlk: WLKConnection = field(default_factory=WLKConnection)

    # Monotonic per-session sequence counter — never resets across WLK reconnects.
    next_sequence: int = 1

    # WLK's current line buffer (resets on each WLK reconnect).
    wlk_lines: list = field(default_factory=list)

    # Last tentative text broadcast (resets on each WLK reconnect).
    last_tentative: str = ""


    # Task handles set by AudioStreamer / TranscriptionProcessor.
    send_task: Optional[asyncio.Task] = None
    recv_task: Optional[asyncio.Task] = None

    streamer: Optional[Any] = None
    processor: Optional[Any] = None
