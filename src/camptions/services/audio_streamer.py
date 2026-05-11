"""Audio send loop — streams PCM chunks from the Pi queue to WLK."""

import asyncio
import logging

from ..config import Settings
from .session import VenueSession, await_or_cancel, sleep_or_stop

log = logging.getLogger(__name__)


class AudioStreamer:
    """Reads from venue.audio_queue and forwards chunks to WLK over WebSocket."""

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
        url = self.settings.wlk_url + "?mode=diff"

        while not venue.stop_event.is_set():
            ws = await venue.wlk.ensure(url, venue.stop_event)
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

            except Exception as e:
                log.warning(
                    "[%s] sender: WLK connection lost (%s); will reconnect",
                    venue.venue_id, e,
                )
                await venue.wlk.drop(ws)
                if await sleep_or_stop(venue.stop_event, 1):
                    break

        log.info("[%s] sender exiting after %d chunks", venue.venue_id, chunks_total)
