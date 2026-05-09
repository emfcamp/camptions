# EMF Camptions

Live captioning system for EMF Camp. Raspberry Pis capture audio from stage mics, stream it to a central server running WhisperLiveKit for real-time transcription, and distribute captions to display screens and audience devices via WebSocket.

## Architecture

- **Audio ingest**: Pi connects to `WebSocket /api/audio/ingest/{venue_id}` and streams raw PCM (16 kHz, 16-bit signed, mono). The Pi uses `arecord -D plughw:...` so ALSA's plug layer handles any rate conversion from the mic's native rate with proper anti-aliasing. Connection = live session. Disconnect = session ends automatically.
- **Transcription**: WhisperLiveKit runs as a separate Docker container (sidecar) with `--pcm-input` so ffmpeg is bypassed. For each active venue, camptions opens a WebSocket to `ws://wlk:8000/asr` and forwards audio bytes through unchanged. JSON results stream back. Model/language/backend are configured on the WLK container via CLI flags.
- **Distribution**: `DistributionManager` broadcasts caption segments and venue status events to all connected WebSocket/SSE caption subscribers.
- **Sessions**: Created automatically when audio connects, ended when it disconnects. No manual control. Segments are stored to SQLite under their session.

## Key WebSocket message types

| Type | Direction | Meaning |
|------|-----------|---------|
| `connected` | server→viewer | Initial handshake; includes `is_live` bool |
| `venue_live` | server→viewer | Audio ingest just connected |
| `venue_offline` | server→viewer | Audio ingest just disconnected |
| `tentative` | server→viewer | In-progress transcription (may change) |
| `committed` | server→viewer | Final transcription segment |
| `keepalive` | server→viewer | 30s ping to keep connection alive |

## Source layout

```
src/camptions/
├── main.py              # FastAPI app, lifespan, static files, route wiring
├── config.py            # Pydantic settings (CAMPTIONS_ env prefix)
├── models.py            # SQLAlchemy models: Venue, Session, Segment
├── database.py          # Async SQLite engine, session factory
├── schemas.py           # Pydantic request/response schemas
├── routers/
│   ├── audio.py         # WS /api/audio/ingest — Pi audio ingest
│   ├── captions.py      # WS /api/captions/stream, SSE, history
│   ├── venues.py        # CRUD for venues
│   └── admin.py         # Stats, session list, cleanup, init-venues
└── services/
    ├── transcription.py # TranscriptionManager + VenueTranscriber
    └── distribution.py  # DistributionManager (broadcast to WS/SSE subscribers)
static/
├── viewer.html          # Mobile viewer — venue tabs, live/offline/disconnected status
├── display.html         # Large screen display — URL params: venue, mode, fontSize, maxLines
└── admin.html           # Admin dashboard — venue status, session history, maintenance
```

## TranscriptionManager wiring

`TranscriptionManager` is instantiated in `main.py` lifespan and injected into routers via `set_transcription_manager()` (avoids circular imports). All three routers that need it (`audio`, `admin`, `captions`) use this pattern.

## STATIC_DIR resolution

Static files are at `/app/static` in Docker (installed package, different `__file__` path) but at `src/../../../static` in local dev. `main.py` tries the source-relative path first and falls back to `/app/static`.

## Docker

Two services:

- **`wlk`** — built from our own minimal `Dockerfile.wlk` (`python:3.12-slim` + `pip install whisperlivekit python-multipart`). Started with `wlk --host 0.0.0.0 --pcm-input --model small.en --language en`. Listens on port 8000 inside the compose network only (no host port exposed). The model is downloaded on first start into the `whisper-cache` named volume; subsequent starts are instant. `--pcm-input` makes WLK skip ffmpeg entirely — bytes go straight into Whisper, so the input must already be 16 kHz s16le mono (camptions handles the resample). We pip-install `python-multipart` explicitly because it's missing from upstream WLK's `pyproject.toml` and its FastAPI form endpoints fail without it.
- **`camptions`** — our FastAPI app. Connects to `ws://wlk:8000/asr` per venue. Camptions' Dockerfile uses a two-step pip install: deps-only layer (invalidated only on `pyproject.toml` changes) then `pip install --no-deps .` after copying source.

`HF_TOKEN` env var is passed through to the `wlk` service for gated models.

## Running locally

```bash
pip install -e ".[dev]"
# Run WLK separately: `pip install whisperlivekit && wlk --pcm-input --model small.en`
CAMPTIONS_WLK_URL=ws://localhost:8000/asr uvicorn camptions.main:app --reload --port 8001
curl -X POST http://localhost:8001/api/admin/init-venues
```

## Running with Docker

```bash
export HF_TOKEN=hf_...          # optional, needed for gated models
docker compose up --build
curl -X POST http://localhost:8000/api/admin/init-venues
```

## Frontend status states

Both `viewer.html` and `display.html` show three states:
- **Connected · Live** — WebSocket up, audio streaming
- **Connected · Offline** — WebSocket up, no audio
- **Disconnected** — WebSocket down (reconnects indefinitely with capped exponential backoff, max 30s)

When switching venues in the viewer, `ws.onclose = null` is set before `ws.close()` to prevent the close event triggering a spurious reconnect loop.

## No admin authentication

The `/api/admin/*` and `POST /api/venues` endpoints have no authentication. Acceptable for a private event network; do not expose publicly without adding an `Authorization: Bearer` check.
