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

# Run the server
uvicorn camptions.main:app --reload

# Run tests
pytest
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
- `WebSocket /api/audio/ingest/{venue_id}` - Stream raw PCM audio (16kHz, 16-bit, mono)

### Caption Distribution
- `WebSocket /api/captions/stream/{venue_id}` - Real-time caption stream
- `GET /api/captions/stream/{venue_id}/sse` - Server-Sent Events stream
- `GET /api/captions/history/{venue_id}` - Historical captions

### Venues
- `GET /api/venues` - List all venues
- `GET /api/venues/{venue_id}` - Get venue details
- `POST /api/venues` - Create a venue

### Admin
- `POST /api/admin/sessions/{venue_id}/start` - Start a session
- `POST /api/admin/sessions/{venue_id}/stop` - Stop a session
- `GET /api/admin/stats` - System statistics
- `POST /api/admin/init-venues` - Initialize default venues
- `POST /api/admin/cleanup` - Clean up old data

## Configuration

Environment variables (prefix with `CAMPTIONS_`):

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `8000` | Server port |
| `DATABASE_URL` | `sqlite+aiosqlite:///./camptions.db` | Database connection string |
| `WHISPER_MODEL` | `medium` | Whisper model size |
| `WHISPER_LANGUAGE` | `en` | Transcription language |
| `WHISPER_BACKEND` | `auto` | Backend: auto, faster-whisper, whisper |
| `ENABLE_VAD` | `true` | Enable voice activity detection |
| `DEFAULT_VENUES` | `["stage-a", "stage-b", "stage-c", "workshop"]` | Default venue IDs |
| `CAPTION_RETENTION_HOURS` | `72` | Hours to retain caption data |

## Raspberry Pi Setup

### Audio Capture

```bash
cd raspberry-pi
sudo ./setup-audio-capture.sh
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
