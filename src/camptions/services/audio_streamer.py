"""Audio send loop — streams PCM chunks from the Pi queue to WhisperLive.

Maintains a small ring buffer of recent audio chunks and replays it on every
new WL connection so Whisper re-enters with sentence context after a
proactive reconnect (WL's max_connection_time cap).
"""

import asyncio
import logging
import time
from collections import deque

from ..config import Settings
from .session import VenueSession, await_or_cancel, sleep_or_stop

log = logging.getLogger(__name__)

# ~5 s of audio at 100 ms/chunk; replayed on each WL reconnect so the model
# has acoustic + linguistic context for the in-progress utterance.
_RING_MAXCHUNKS = 50


class AudioStreamer:
    """Reads from venue.audio_queue and forwards chunks to WhisperLive."""

    def __init__(self, venue: VenueSession, settings: Settings) -> None:
        self.venue = venue
        self.settings = settings

    async def start(self) -> None:
        self.venue.send_task = asyncio.create_task(
            self._send_loop(), name=f"send:{self.venue.venue_id}"
        )

    async def stop(self) -> None:
        await await_or_cancel(self.venue.send_task, "sender", self.venue.venue_id)

    async def _send_loop(self) -> None:
        chunks_total = 0
        venue = self.venue
        url = self.settings.wl_url
        reconnect_interval = self.settings.wl_reconnect_interval
        ring: deque[bytes] = deque(maxlen=_RING_MAXCHUNKS)

        while not venue.stop_event.is_set():
            ws = await venue.wl.ensure(url, venue.stop_event)
            if ws is None:
                break

            # Replay recent audio so Whisper has sentence context. Empty on
            # the very first connect; populated after any reconnect.
            ring_ok = True
            for chunk in list(ring):
                try:
                    await ws.send(chunk)
                except Exception as e:
                    log.warning(
                        "[%s] sender: replay failed (%s); reconnecting",
                        venue.venue_id, e,
                    )
                    await venue.wl.drop(ws)
                    ring_ok = False
                    break
            if not ring_ok:
                continue

            try:
                while not venue.stop_event.is_set():
                    remaining = venue.ws_opened_at + reconnect_interval - time.monotonic()
                    if remaining <= 0:
                        log.info(
                            "[%s] sender: proactive reconnect after %ds",
                            venue.venue_id, reconnect_interval,
                        )
                        await venue.wl.drop(ws)
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
                        log.info(
                            "[%s] sender: clean EOS after %d chunks",
                            venue.venue_id, chunks_total,
                        )
                        return

                    await ws.send(chunk)
                    ring.append(chunk)
                    chunks_total += 1

            except Exception as e:
                log.warning(
                    "[%s] sender: WL connection lost (%s); will reconnect",
                    venue.venue_id, e,
                )
                await venue.wl.drop(ws)
                if await sleep_or_stop(venue.stop_event, 1):
                    break

        log.info("[%s] sender exiting after %d chunks", venue.venue_id, chunks_total)
