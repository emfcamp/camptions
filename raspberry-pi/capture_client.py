#!/usr/bin/env python3
"""
EMF Camptions - Raspberry Pi Audio Capture Client

Captures audio from USB microphone/audio interface and streams
to the central camptions server via WebSocket.

Uses `arecord` directly (alsa-utils) rather than PyAudio/PortAudio.
PortAudio's enumeration breaks badly on Pi images that ship with stale
ALSA aliases (HDMI / modem / phoneline definitions that don't resolve)
and on systems without JACK installed. arecord talks ALSA directly and
is available on every Pi out of the box.

We capture at 16 kHz mono S16_LE — what WhisperLive's --raw_pcm_input mode
wants directly. The `plughw:` ALSA device prefix lets the kernel resample
from the mic's native rate (typically 44.1/48 kHz) with proper anti-alias
filtering, so the server can forward bytes through unchanged.
"""

import argparse
import asyncio
import contextlib
import json
import re
import shutil
import signal
import sys
from typing import Optional

try:
    import websockets
except ImportError:
    print("websockets not installed. Run: pip3 install websockets")
    sys.exit(1)


CAPTURE_RATE = 16000  # what Whisper wants; ALSA's plug layer handles conversion
CHANNELS = 1
SAMPLE_WIDTH_BYTES = 2
CHUNK_DURATION_MS = 100
SAMPLES_PER_CHUNK = int(CAPTURE_RATE * CHUNK_DURATION_MS / 1000)
CHUNK_BYTES = SAMPLES_PER_CHUNK * SAMPLE_WIDTH_BYTES

# If a single read takes longer than this, assume the USB device has hung
# and bounce the capture pipeline. ~10× the chunk duration.
READ_TIMEOUT_S = 1.0


def _arecord_path() -> str:
    path = shutil.which("arecord")
    if path is None:
        print("arecord not found. Install with: sudo apt install alsa-utils")
        sys.exit(1)
    return path


async def _detect_capture_device() -> Optional[str]:
    """Parse `arecord -l` and return the first capture device as a `plughw:CARD,DEVICE` string.

    `plug` rather than raw `hw` lets ALSA do format/rate conversion if the
    physical device's native format doesn't exactly match what we ask for.
    """
    proc = await asyncio.create_subprocess_exec(
        _arecord_path(), "-l",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    text = stdout.decode("utf-8", errors="replace")
    # Lines look like: "card 1: Device [USB PnP Sound Device], device 0: USB Audio [USB Audio]"
    pattern = re.compile(r"^card\s+(\d+):\s+\S+\s+\[([^\]]+)\],\s+device\s+(\d+):", re.MULTILINE)
    match = pattern.search(text)
    if not match:
        return None
    card, name, device = match.groups()
    print(f"Auto-detected capture device: card {card} '{name.strip()}' device {device}")
    return f"plughw:{card},{device}"


class AudioCapture:
    """Spawns `arecord` and exposes a chunked async read interface."""

    def __init__(self, device: Optional[str] = None):
        self.device_arg = device  # ALSA name, e.g. "plughw:1,0", or None to auto-detect
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.actual_device: Optional[str] = None

    async def start(self) -> None:
        """Spawn arecord. Raises OSError if the device can't be opened."""
        device = self.device_arg or await _detect_capture_device()
        if device is None:
            raise OSError("no ALSA capture device found (is the USB mic plugged in?)")

        cmd = [
            _arecord_path(),
            "-D", device,
            "-f", "S16_LE",
            "-c", str(CHANNELS),
            "-r", str(CAPTURE_RATE),
            "-t", "raw",
            "--quiet",
        ]
        self.proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # arecord exits immediately if the device is wrong; give it a moment
        # to fail fast so we can surface a useful error rather than blocking
        # forever on stdout.
        await asyncio.sleep(0.15)
        if self.proc.returncode is not None:
            err = (await self.proc.stderr.read()).decode("utf-8", errors="replace").strip()
            self.proc = None
            raise OSError(f"arecord failed (rc={self.proc.returncode if self.proc else '?'}): {err}")

        self.actual_device = device
        print(f"Audio capture started: arecord -D {device} S16_LE {CAPTURE_RATE}Hz mono")

    async def read(self) -> bytes:
        """Read one chunk worth of PCM. Raises EOFError if arecord died."""
        if self.proc is None or self.proc.stdout is None:
            raise RuntimeError("Audio capture not started")
        try:
            return await self.proc.stdout.readexactly(CHUNK_BYTES)
        except asyncio.IncompleteReadError as e:
            err = ""
            if self.proc.stderr is not None:
                with contextlib.suppress(Exception):
                    err = (await self.proc.stderr.read()).decode("utf-8", errors="replace").strip()
            raise EOFError(
                f"arecord closed stdout after {len(e.partial)} bytes; stderr: {err or '(empty)'}"
            )

    async def stop(self) -> None:
        """Terminate arecord. Safe to call before start() or repeatedly."""
        if self.proc is None:
            return
        if self.proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    self.proc.kill()
                await self.proc.wait()
        self.proc = None
        print("Audio capture stopped")

    @staticmethod
    async def list_devices() -> None:
        """Print the output of `arecord -l`."""
        proc = await asyncio.create_subprocess_exec(
            _arecord_path(), "-l",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        print(stdout.decode("utf-8", errors="replace"))


class CaptionClient:
    """WebSocket client for streaming audio to camptions server."""

    def __init__(
        self,
        server_url: str,
        venue_id: str,
        token: Optional[str] = None,
        session_title: Optional[str] = None,
    ):
        self.server_url = server_url
        self.venue_id = venue_id
        self.token = token
        self.session_title = session_title
        self.ws = None

    async def connect(self) -> bool:
        params = []
        if self.token:
            params.append(f"token={self.token}")
        if self.session_title:
            params.append(f"session_title={self.session_title}")
        url = f"{self.server_url}/api/audio/ingest/{self.venue_id}"
        if params:
            url += "?" + "&".join(params)

        print(f"Connecting to {url}...")

        self.ws = await websockets.connect(
            url,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5,
        )

        response = await self.ws.recv()
        data = json.loads(response)

        if data.get("type") == "session_started":
            print(f"Session started: {data.get('session_id')}")
            print(f"Venue: {data.get('venue_id')}")
            return True
        print(f"Unexpected response: {data}")
        return False

    async def send_audio(self, audio_data: bytes) -> None:
        if self.ws:
            await self.ws.send(audio_data)

    async def close(self) -> None:
        if self.ws:
            await self.ws.close()
            self.ws = None


async def _wait_for_audio(audio: AudioCapture, stop_event: asyncio.Event) -> bool:
    """Block until arecord opens a device (or shutdown is requested)."""
    delay = 1
    max_delay = 15
    while not stop_event.is_set():
        try:
            await audio.start()
            return True
        except Exception as e:
            print(f"Audio device unavailable: {e}. Retrying in {delay}s...")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
                return False
            except asyncio.TimeoutError:
                pass
            delay = min(delay * 2, max_delay)
    return False


async def _watch_stdin(stop_event: asyncio.Event, request_stop) -> None:
    """Trigger stop on Ctrl-D (EOF) or `q`/`quit`/`exit` on stdin.

    No-op when stdin isn't a TTY (e.g. running under systemd, where stdin is
    `/dev/null`): epoll can't watch a device fd, so connect_read_pipe() fails
    with EPERM, and there's no interactive input to read on a service anyway.
    """
    if not sys.stdin.isatty():
        return
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    try:
        await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), sys.stdin
        )
    except Exception:
        return
    while not stop_event.is_set():
        line = await reader.readline()
        if not line:
            request_stop("stdin EOF (Ctrl-D)")
            return
        if line.strip().lower() in (b"q", b"quit", b"exit"):
            request_stop("user requested quit")
            return


async def run_capture(
    server_url: str,
    venue_id: str,
    device: Optional[str] = None,
    token: Optional[str] = None,
    session_title: Optional[str] = None,
):
    """Main capture loop.

    Lifecycle per iteration:
      1. Acquire the audio device (retries forever).
      2. Open the WebSocket and stream until something fails.
      3. Tear both down and start over.
    """
    audio = AudioCapture(device)
    client = CaptionClient(server_url, venue_id, token=token, session_title=session_title)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def request_stop(reason: str):
        if not stop_event.is_set():
            print(f"\n{reason}, shutting down... (press Ctrl-C again to force exit)")
            stop_event.set()
        else:
            print(f"\n{reason} — forcing exit")
            sys.exit(130)

    for sig, label in ((signal.SIGINT, "Ctrl-C"), (signal.SIGTERM, "SIGTERM")):
        loop.add_signal_handler(sig, request_stop, label)

    stdin_task = asyncio.create_task(_watch_stdin(stop_event, request_stop))

    ws_delay = 1
    ws_max_delay = 15

    try:
        while not stop_event.is_set():
            if not await _wait_for_audio(audio, stop_event):
                break

            try:
                if not await client.connect():
                    raise RuntimeError("Server did not return session_started")

                ws_delay = 1

                while not stop_event.is_set():
                    try:
                        audio_data = await asyncio.wait_for(
                            audio.read(), timeout=READ_TIMEOUT_S
                        )
                    except asyncio.TimeoutError:
                        raise TimeoutError(
                            f"arecord read exceeded {READ_TIMEOUT_S}s — device wedged?"
                        )

                    await client.send_audio(audio_data)

            except websockets.ConnectionClosed as e:
                print(f"WebSocket closed: code={e.code} reason={e.reason!r}")
            except TimeoutError as e:
                print(f"Audio capture wedged: {e}")
            except EOFError as e:
                print(f"Capture process died: {e}")
            except OSError as e:
                print(f"Audio read failed: {e}")
            except Exception as e:
                print(f"Stream error: {e!r}")
            finally:
                await audio.stop()
                await client.close()

            if not stop_event.is_set():
                print(f"Reconnecting in {ws_delay}s...")
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=ws_delay)
                except asyncio.TimeoutError:
                    pass
                ws_delay = min(ws_delay * 2, ws_max_delay)
    finally:
        stdin_task.cancel()

    print("Capture client stopped")


def main():
    # stdout is fully block-buffered when not a TTY (e.g. under systemd), so
    # without this, every print() — the only diagnostics this client has for
    # what the reconnect loop is doing — can sit unflushed indefinitely and
    # never reach `journalctl`.
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser(description="EMF Camptions Audio Capture Client")
    parser.add_argument(
        "--server", "-s",
        default="ws://localhost:8000",
        help="Camptions server URL (default: ws://localhost:8000)",
    )
    parser.add_argument(
        "--venue", "-v",
        help="Venue ID (e.g., stage-a, stage-b) — required unless --list-devices",
    )
    parser.add_argument(
        "--device", "-d",
        default=None,
        help="ALSA capture device, e.g. 'plughw:1,0' (default: auto-detect first USB mic)",
    )
    parser.add_argument(
        "--token", "-k",
        default=None,
        help="Ingest authentication token (CAMPTIONS_INGEST_TOKEN on the server)",
    )
    parser.add_argument(
        "--title", "-t",
        default=None,
        help="Session title (optional)",
    )
    parser.add_argument(
        "--list-devices", "-l",
        action="store_true",
        help="List available audio devices (runs `arecord -l`) and exit",
    )

    args = parser.parse_args()

    if args.list_devices:
        asyncio.run(AudioCapture.list_devices())
        return

    if not args.venue:
        parser.error("--venue/-v is required (omit only with --list-devices)")

    print("=" * 50)
    print("EMF Camptions Audio Capture")
    print("=" * 50)
    print(f"Server: {args.server}")
    print(f"Venue:  {args.venue}")
    print(f"Device: {args.device or 'auto-detect'}")
    print("=" * 50)
    print("Press Ctrl-C, Ctrl-D, or type 'q' + Enter to stop")
    print()

    asyncio.run(
        run_capture(
            server_url=args.server,
            venue_id=args.venue,
            device=args.device,
            token=args.token,
            session_title=args.title,
        )
    )


if __name__ == "__main__":
    main()
