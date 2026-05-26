# EMF Camptions v2

Live captioning system for EMF Camp using WhisperLive (Collabora).

## Overview

EMF Camptions provides real-time speech-to-text captioning for live events. Audio is captured from stage microphones via Raspberry Pi devices, streamed to a central server running WhisperLive for transcription, and distributed to display screens and user devices.

## Features

- Real-time speech-to-text using WhisperLive (faster-whisper backend)
- Multiple venue support with independent audio streams
- WebSocket and Server-Sent Events (SSE) for caption distribution
- Large screen display mode for venue monitors
- Mobile-friendly viewer with customizable font size and themes
- Admin interface for session management
- Raspberry Pi setup scripts for audio capture and display

## Quick Start

### Using Docker

```bash
# Start the server
docker compose up --build

# Initialize default venues
curl -X POST http://localhost:8000/api/admin/init-venues

# Open the viewer
open http://localhost:8000/
```

### Local Development

```bash
# Install dependencies
pip install -e ".[dev]"

# Run the server (WL must be running separately)
CAMPTIONS_WL_URL=ws://localhost:9090 \
CAMPTIONS_ADMIN_TOKEN=dev \
CAMPTIONS_INGEST_TOKEN=dev \
uvicorn camptions.main:app --reload
```

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Raspberry Pi   │     │  Central Server │     │    Displays     │
│  (Audio Capture)│────▶│  (WhisperLive)  │────▶│  (WebSocket)    │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                              │
                              ▼
                        ┌───────────┐
                        │  SQLite   │
                        │  Database │
                        └───────────┘

┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  OBS / Encoder  │────▶│    MediaMTX     │────▶│ Viewer (WHEP)   │
│  (WHIP publish) │     │ (WebRTC relay)  │     │  in-browser     │
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

## API Endpoints

### Audio Ingestion
- `WebSocket /api/audio/ingest/{venue_id}?token=<INGEST_TOKEN>` — Stream raw PCM audio (16 kHz, 16-bit, mono). 🔒 ingest token required.

### Caption Distribution (public)
- `WebSocket /api/captions/stream/{venue_id}` — Real-time caption stream
- `GET /api/captions/stream/{venue_id}/sse` — Server-Sent Events stream
- `GET /api/captions/history/{venue_id}` — Historical captions

### Venues
- `GET /api/venues` — List all venues (public)
- `GET /api/venues/{venue_id}` — Get venue details (public)
- `POST /api/venues` — Create a venue 🔒
- `PATCH /api/venues/{venue_id}` — Update a venue 🔒

### Schedule
- `GET /api/schedule/now-and-next` — Now/next talks for all venues
- `GET /api/schedule/now-and-next/{venue_id}` — Now/next for one venue

### Admin
- `GET /api/admin/stats` — System statistics (public)
- `GET /api/admin/sessions` — List recent sessions 🔒
- `POST /api/admin/init-venues` — Initialize default venues 🔒
- `POST /api/admin/cleanup` — Clean up old data 🔒

🔒 = requires `Authorization: Bearer <ADMIN_TOKEN>`

## Configuration

Environment variables (prefix with `CAMPTIONS_`):

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `8000` | Server port |
| `DEBUG` | `false` | Enable debug logging and SQLAlchemy echo |
| `DATABASE_URL` | `sqlite+aiosqlite:///./camptions.db` | Database connection string |
| `WL_URL` | `ws://wl:9090` | WhisperLive WebSocket URL |
| `WHISPER_MODEL` | `small.en` | Whisper model passed in the WL handshake |
| `WHISPER_LANGUAGE` | `en` | Whisper language passed in the WL handshake |
| `WHISPER_USE_VAD` | `false` | Enable WL's VAD filter |
| `WL_RECONNECT_INTERVAL` | `3300` | Seconds before the send loop drops & reconnects WL (must be < `--max_connection_time`) |
| `ADMIN_TOKEN` | *(required)* | Bearer token for admin and venue-write endpoints |
| `INGEST_TOKEN` | *(required)* | Token for Pi audio-ingest WebSocket (`?token=`) |
| `DEFAULT_VENUES` | `["stage-a", "stage-b", "stage-c"]` | Default venue IDs created by `init-venues` |
| `CAPTION_RETENTION_HOURS` | `72` | Hours to retain caption data |
| `RATE_LIMIT_PER_MINUTE` | `120` | Max HTTP requests per client IP per minute on public API endpoints (0 = disabled) |
| `WS_CONNECTIONS_PER_IP` | `10` | Max simultaneous WebSocket connections per client IP |

Generate camptions tokens with: `python3 -c "import secrets; print(secrets.token_hex(32))"`

### MediaMTX (WHIP/WHEP)

MediaMTX provides WebRTC ingest (WHIP) from OBS and egress (WHEP) to browsers.

| Variable | Default | Description |
|----------|---------|-------------|
| `WHIP_TOKEN` | *(required)* | Shared bearer password for OBS → MediaMTX WHIP publish. Generate with `openssl rand -hex 32`. |
| `MTX_WEBRTCADDITIONALHOSTS` | *(required)* | Public hostname/IP browsers can reach MediaMTX on — needed because MediaMTX can only see its own Docker/host interfaces. Set to `127.0.0.1` for local dev; the server's public DNS name in production. |

**OBS setup** — Service: **WHIP**, Server: `https://captions.emf.camp/stage-a/whip`, Bearer token: `publisher:<WHIP_TOKEN>`.

**WHEP stream URLs** (set per-venue in the admin under "Presentation Stream"):

| Stage | WHEP URL |
|-------|----------|
| Stage A | `https://captions.emf.camp/stage-a/whep` |
| Stage B | `https://captions.emf.camp/stage-b/whep` |
| Stage C | `https://captions.emf.camp/stage-c/whep` |

> **HTTPS requirement** — browsers block mixed-content WebRTC: if the viewer is served over HTTPS, the WHEP URL must also be HTTPS. The included `nginx.conf` already proxies `/stage-*/whip` and `/stage-*/whep` to MediaMTX at `localhost:8889`, so the public WHEP URLs above work as-is once nginx is deployed.

## Raspberry Pi Setup

### DietPi Base Image

We use [DietPi](https://dietpi.com/) as the base OS. The repo ships a [dietpi.txt](dietpi.txt) at the root with the first-boot automation settings we use for capture Pis (hostname `stage-pi-001`, ethernet enabled, SSH pubkey login only, automated install of ALSA / Git / Python 3 pip).

1. Download the DietPi image for your Pi from [dietpi.com/#downloadinfo](https://dietpi.com/#downloadinfo) and flash it to an SD card with [Raspberry Pi Imager](https://www.raspberrypi.com/software/) or [balenaEtcher](https://etcher.balena.io/).
2. Mount the `boot` partition on your workstation and copy [dietpi.txt](dietpi.txt) over the file already there:
   ```bash
   cp dietpi.txt /media/$USER/boot/dietpi.txt
   ```
3. Before booting, edit the copy on the SD card:
   - Set `AUTO_SETUP_NET_HOSTNAME` to a unique name per Pi (e.g. `stage-a-pi`, `stage-b-pi`).
   - Replace `AUTO_SETUP_GLOBAL_PASSWORD` with your own password.
   - Replace `AUTO_SETUP_SSH_PUBKEY` with your own public key, or add additional `AUTO_SETUP_SSH_PUBKEY=` lines.
   - If using WiFi, set `AUTO_SETUP_NET_WIFI_ENABLED=1` and edit `dietpi-wifi.txt` on the boot partition with your SSID/PSK.
4. Eject the SD card, boot the Pi on the venue network, and wait ~5–10 minutes for first-run automation to finish (the Pi will reboot itself a couple of times).
5. SSH in as `root` using your key:
   ```bash
   ssh root@stage-pi-001.local
   ```
6. Create an unprivileged user to run the capture service (the setup script refuses to install for `root`):
   ```bash
   adduser camptions
   usermod -aG sudo camptions
   mkdir -p /home/camptions/.ssh
   cp /root/.ssh/authorized_keys /home/camptions/.ssh/
   chown -R camptions:camptions /home/camptions/.ssh
   chmod 700 /home/camptions/.ssh
   ```
7. Copy this repo's `raspberry-pi/` directory onto the Pi and run the relevant setup script below as the `camptions` user.

### Audio Capture

```bash
cd raspberry-pi
sudo ./setup-audio-capture.sh
```

After the script finishes, edit `/opt/camptions/config.env` to set `CAMPTIONS_SERVER` and `CAMPTIONS_VENUE`, then enable the service:

```bash
sudo systemctl enable --now camptions-capture
```

### Display Kiosk

```bash
cd raspberry-pi
sudo ./setup-display.sh
```

### Combined Setup

```bash
cd raspberry-pi
sudo ./setup-full.sh
```

## Frontend Pages

| URL | Description |
|-----|-------------|
| `/` | Mobile viewer — venue tabs, live captions, embedded stream toggle |
| `/v/{venue_id}` | Viewer pre-selected to a specific venue |
| `/display/{venue_id}` | Large-screen caption display |
| `/admin` | Admin interface — venue controls, stream URL config |
| `/status` | Public status board — venue live/offline state, subscriber counts, now/next schedule. Pass `?token=<ADMIN_TOKEN>` to also show segment totals from the admin stats API. |

### Display URL Parameters

| Parameter | Values | Description |
|-----------|--------|-------------|
| `venue` | venue ID | Which venue to display |
| `mode` | `dark`, `light`, `high-contrast` | Color scheme |
| `fontSize` | CSS value | Font size (e.g., `4vw`, `48px`) |

## Project Structure

```
camptions/
├── src/camptions/          # Backend Python code
│   ├── main.py             # FastAPI application
│   ├── config.py           # Configuration
│   ├── models.py           # Database models
│   ├── routers/            # API endpoints
│   └── services/           # Business logic
├── static/                 # Frontend HTML/CSS/JS
├── raspberry-pi/           # Pi setup scripts
├── Dockerfile              # Container build
└── docker-compose.yml      # Container orchestration
```

## License

MIT License - see [LICENSE](LICENSE) for details.
