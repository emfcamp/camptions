# EMF Camptions v2

Live captioning system for EMF Camp using WhisperLiveKit.

## Overview

EMF Camptions provides real-time speech-to-text captioning for live events. Audio is captured from stage microphones via Raspberry Pi devices, streamed to a central server running WhisperLiveKit for transcription, and distributed to display screens and user devices.

## Features

- Real-time speech-to-text using WhisperLiveKit with SimulStreaming
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

# Run the server (WLK must be running separately)
CAMPTIONS_WLK_URL=ws://localhost:8000/asr \
CAMPTIONS_ADMIN_TOKEN=dev \
CAMPTIONS_INGEST_TOKEN=dev \
uvicorn camptions.main:app --reload
```

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Raspberry Pi   │     │  Central Server │     │    Displays     │
│  (Audio Capture)│────▶│  (WhisperLiveKit)────▶│  (WebSocket)    │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                              │
                              ▼
                        ┌───────────┐
                        │  SQLite   │
                        │  Database │
                        └───────────┘
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

### Admin 🔒 (all require `Authorization: Bearer <ADMIN_TOKEN>`)
- `GET /api/admin/stats` — System statistics
- `GET /api/admin/sessions` — List recent sessions
- `POST /api/admin/init-venues` — Initialize default venues
- `POST /api/admin/cleanup` — Clean up old data

## Configuration

Environment variables (prefix with `CAMPTIONS_`):

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `8000` | Server port |
| `DEBUG` | `false` | Enable debug logging and SQLAlchemy echo |
| `DATABASE_URL` | `sqlite+aiosqlite:///./camptions.db` | Database connection string |
| `WLK_URL` | `ws://wlk:8000/asr` | WhisperLiveKit WebSocket URL |
| `ADMIN_TOKEN` | *(required)* | Bearer token for admin and venue-write endpoints |
| `INGEST_TOKEN` | *(required)* | Token for Pi audio-ingest WebSocket (`?token=`) |
| `DEFAULT_VENUES` | `["stage-a", "stage-b", "stage-c"]` | Default venue IDs created by `init-venues` |
| `CAPTION_RETENTION_HOURS` | `72` | Hours to retain caption data |

Generate tokens with: `python3 -c "import secrets; print(secrets.token_hex(32))"`

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

- `/` - Mobile viewer with venue selection
- `/display?venue=stage-a` - Large screen display
- `/admin` - Admin interface

### Display URL Parameters

| Parameter | Values | Description |
|-----------|--------|-------------|
| `venue` | venue ID | Which venue to display |
| `mode` | `dark`, `light`, `high-contrast` | Color scheme |
| `fontSize` | CSS value | Font size (e.g., `4vw`, `48px`) |
| `maxLines` | number | Maximum lines to show |

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
├── tests/                  # Test suite
├── alembic/                # Database migrations
├── Dockerfile              # Container build
└── docker-compose.yml      # Container orchestration
```

## License

MIT License - see [LICENSE](LICENSE) for details.
