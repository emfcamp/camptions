#!/usr/bin/env python3
"""
EMF Camptions - Raspberry Pi Audio Capture Client

Captures audio from USB microphone/audio interface and streams
to the central camptions server via WebSocket.

Audio format: 16kHz, 16-bit signed, mono (s16le)
"""

import argparse
import asyncio
import json
import signal
import sys

try:
    import pyaudio
except ImportError:
    print("PyAudio not installed. Run: sudo apt install python3-pyaudio")
    sys.exit(1)

try:
    import websockets
except ImportError:
    print("websockets not installed. Run: pip3 install websockets")
    sys.exit(1)


# Audio configuration matching WhisperLiveKit expectations
SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_DURATION_MS = 100  # Send audio every 100ms
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_DURATION_MS / 1000)
FORMAT = pyaudio.paInt16


class AudioCapture:
    """Captures audio from the system's audio input device."""

    def __init__(self, device_index: int = None):
        self.device_index = device_index
        self.audio = pyaudio.PyAudio()
        self.stream = None

    def list_devices(self):
        """List available audio input devices."""
        print("\nAvailable audio input devices:")
        print("-" * 50)

        for i in range(self.audio.get_device_count()):
            info = self.audio.get_device_info_by_index(i)
            if info["maxInputChannels"] > 0:
                print(f"  [{i}] {info['name']}")
                print(
                    f"      Channels: {info['maxInputChannels']}, "
                    f"Rate: {int(info['defaultSampleRate'])}Hz"
                )
        print()

    def start(self):
        """Start audio capture stream."""
        self.stream = self.audio.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            input=True,
            input_device_index=self.device_index,
            frames_per_buffer=CHUNK_SIZE,
        )
        print(f"Audio capture started (device: {self.device_index or 'default'})")

    def read(self) -> bytes:
        """Read a chunk of audio data."""
        if self.stream is None:
            raise RuntimeError("Audio capture not started")
        return self.stream.read(CHUNK_SIZE, exception_on_overflow=False)

    def stop(self):
        """Stop audio capture."""
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None
        self.audio.terminate()
        print("Audio capture stopped")


class CaptionClient:
    """WebSocket client for streaming audio to camptions server."""

    def __init__(
        self,
        server_url: str,
        venue_id: str,
        session_title: str = None,
    ):
        self.server_url = server_url
        self.venue_id = venue_id
        self.session_title = session_title
        self.ws = None

    async def connect(self):
        """Establish WebSocket connection to server."""
        url = f"{self.server_url}/api/audio/ingest/{self.venue_id}"
        if self.session_title:
            url += f"?session_title={self.session_title}"

        print(f"Connecting to {url}...")

        self.ws = await websockets.connect(
            url,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5,
        )

        # Wait for session confirmation
        response = await self.ws.recv()
        data = json.loads(response)

        if data.get("type") == "session_started":
            print(f"Session started: {data.get('session_id')}")
            print(f"Venue: {data.get('venue_id')}")
            return True
        else:
            print(f"Unexpected response: {data}")
            return False

    async def send_audio(self, audio_data: bytes):
        """Send audio chunk to server."""
        if self.ws:
            await self.ws.send(audio_data)

    async def close(self):
        """Close WebSocket connection."""
        if self.ws:
            await self.ws.close()
            self.ws = None


async def run_capture(
    server_url: str,
    venue_id: str,
    device_index: int = None,
    session_title: str = None,
):
    """Main capture loop."""

    audio = AudioCapture(device_index)
    client = CaptionClient(server_url, venue_id, session_title)

    # Handle graceful shutdown
    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()

    def signal_handler():
        print("\nShutting down...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    reconnect_delay = 1
    max_reconnect_delay = 60

    while not stop_event.is_set():
        try:
            # Connect to server
            if not await client.connect():
                raise Exception("Failed to start session")

            reconnect_delay = 1  # Reset on successful connection

            # Start audio capture
            audio.start()

            # Stream audio
            while not stop_event.is_set():
                audio_data = audio.read()
                await client.send_audio(audio_data)

        except websockets.ConnectionClosed:
            print("Connection closed by server")
        except Exception as e:
            print(f"Error: {e}")
        finally:
            audio.stop()
            await client.close()

        # Reconnect with backoff
        if not stop_event.is_set():
            print(f"Reconnecting in {reconnect_delay}s...")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)

    print("Capture client stopped")


def main():
    parser = argparse.ArgumentParser(description="EMF Camptions Audio Capture Client")
    parser.add_argument(
        "--server",
        "-s",
        default="ws://localhost:8000",
        help="Camptions server URL (default: ws://localhost:8000)",
    )
    parser.add_argument(
        "--venue",
        "-v",
        required=True,
        help="Venue ID (e.g., stage-a, stage-b)",
    )
    parser.add_argument(
        "--device",
        "-d",
        type=int,
        default=None,
        help="Audio input device index (default: system default)",
    )
    parser.add_argument(
        "--title",
        "-t",
        default=None,
        help="Session title (optional)",
    )
    parser.add_argument(
        "--list-devices",
        "-l",
        action="store_true",
        help="List available audio devices and exit",
    )

    args = parser.parse_args()

    if args.list_devices:
        audio = AudioCapture()
        audio.list_devices()
        audio.stop()
        return

    print("=" * 50)
    print("EMF Camptions Audio Capture")
    print("=" * 50)
    print(f"Server: {args.server}")
    print(f"Venue: {args.venue}")
    print(f"Device: {args.device or 'default'}")
    print("=" * 50)
    print("Press Ctrl+C to stop")
    print()

    asyncio.run(
        run_capture(
            server_url=args.server,
            venue_id=args.venue,
            device_index=args.device,
            session_title=args.title,
        )
    )


if __name__ == "__main__":
    main()
