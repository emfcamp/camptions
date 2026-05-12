# EMF Camptions

Live captioning system for EMF Camp. Raspberry Pis capture audio from stage mics, stream it to a central server running WhisperLiveKit for real-time transcription, and distribute captions to display screens and audience devices via WebSocket.

## Architecture

- **Audio ingest**: Pi connects to `WebSocket /api/audio/ingest/{venue_id}` and streams raw PCM (16 kHz, 16-bit signed, mono). The Pi uses `arecord -D plughw:...` so ALSA's plug layer handles any rate conversion from the mic's native rate with proper anti-aliasing. Connection = live session. Disconnect = session ends automatically.
- **Transcription**: WhisperLiveKit (`whisperlivekit` pip package) runs as a separate Docker container (sidecar). For each active venue, camptions opens a WebSocket to `ws://wlk:8000/asr?mode=diff`, streams raw PCM bytes, and receives JSON messages in the snapshot/diff protocol (see below). The WLK URL is configured via `CAMPTIONS_WLK_URL`.
- **Distribution**: `DistributionManager` broadcasts caption segments and venue status events to all connected WebSocket/SSE caption subscribers.
- **Sessions**: Created automatically when audio connects, ended when it disconnects. No manual control. Segments are stored to SQLite under their session.
- **Schedule**: `ScheduleService` polls the EMF Camp now-and-next API every 60 s and broadcasts `schedule_update` messages to all venue subscribers.

## WhisperLiveKit protocol

WLK is started with `--pcm-input` (accepts raw int16 PCM) and `--log-level WARNING`. The camptions backend connects with `?mode=diff` and receives two message types:

| Type | Fields | Meaning |
|------|--------|---------|
| `config` | — | Initial handshake; ignored |
| `snapshot` | `lines[]`, `buffer_transcription` | Full state of current transcript; replace local line buffer |
| `diff` | `lines_pruned`, `new_lines[]`, `n_lines`, `buffer_transcription` | Incremental update; prune oldest N lines, append new ones |
| `ready_to_stop` | — | WLK is shutting down; reconnect |

Each `line` object has `text`, `start` (H:MM:SS.cc), `end` (H:MM:SS.cc). `buffer_transcription` is the in-progress partial text.

## Key WebSocket message types (server → caption viewer)

| Type | Meaning |
|------|---------|
| `connected` | Initial handshake; includes `is_live` bool |
| `venue_live` | Audio ingest just connected |
| `venue_offline` | Audio ingest just disconnected |
| `tentative` | In-progress transcription (may change) |
| `committed` | Final transcription segment |
| `keepalive` | 30 s ping to keep connection alive |
| `schedule_update` | Now/next talk info from EMF schedule API |
| `session_end` | Active session has ended |

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
│   ├── admin.py         # Stats, session list, cleanup, init-venues
│   └── schedule.py      # GET /api/schedule/now-and-next
└── services/
    ├── transcription.py # TranscriptionManager + TranscriptionProcessor
    ├── audio_streamer.py# AudioStreamer — sends PCM queue → WLK
    ├── session.py       # VenueSession dataclass, WLKConnection, helpers
    ├── distribution.py  # DistributionManager (broadcast to WS/SSE subscribers)
    └── schedule.py      # ScheduleService — polls EMF now-and-next API
static/
├── captions-client.js   # Shared WS client + renderer (used by viewer & display)
├── viewer.html          # Mobile viewer — venue tabs, live/offline/disconnected status
├── display.html         # Large screen display — URL params: venue, mode, fontSize
└── admin.html           # Admin dashboard — venue status, session history, maintenance
```

## TranscriptionManager wiring

`TranscriptionManager` is instantiated in `main.py` lifespan and injected into routers via `set_transcription_manager()` (avoids circular imports). `audio`, `admin`, and `captions` routers all use this pattern.

## Transcription pipeline per venue

```
Pi audio WS  →  process_audio()  →  audio_queue
                                          │
                                          ▼
                                 AudioStreamer._send_loop
                                          │   (shared WLKConnection)
                                          ▼
                                       WLK WS
                                          │
                                          ▼
                             TranscriptionProcessor._recv_loop
                                          │
                                          ▼
                                distribution + DB
```

Send and receive share one `WLKConnection`. WLK going away does not end the session — audio keeps queueing, both loops reconnect, transcription resumes. Session ends only when `end_session()` is called (Pi disconnect or shutdown).

## STATIC_DIR resolution

Static files are at `/app/static` in Docker (installed package, different `__file__` path) but at `src/../../../static` in local dev. `main.py` tries the source-relative path first and falls back to `/app/static`.

## Docker

Two services in `docker-compose.yml`:

- **`wlk`** — custom image from `Dockerfile.wlk`; installs `whisperlivekit` and starts with `--pcm-input`. Listens on port 8000 inside the compose network. Model and language are set via CMD args in the Dockerfile. Model weights are cached in the `whisper-cache` named volume (`/root/.cache/huggingface/hub`); subsequent starts are instant.
- **`camptions`** — our FastAPI app. Connects to `ws://wlk:8000/asr` per active venue (set via `CAMPTIONS_WLK_URL`). Dockerfile uses a two-step pip install: deps-only layer (invalidated only on `pyproject.toml` changes) then `pip install --no-deps .` after copying source.

`HF_TOKEN` env var is passed through to the `wlk` service for gated models.

## Running locally

```bash
pip install -e ".[dev]"
# Run WLK separately: docker run <image> wlk --host 0.0.0.0 --pcm-input
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
- **Connected · Source Offline** — WebSocket up, no audio
- **Disconnected** — WebSocket down (reconnects indefinitely with capped exponential backoff, max 30 s)

When switching venues in the viewer, `ws.onclose = null` is set before `ws.close()` to prevent the close event triggering a spurious reconnect loop.

## Authentication

- `/api/admin/*` and `POST/PATCH /api/venues` require `Authorization: Bearer <token>` matching `CAMPTIONS_ADMIN_TOKEN` env var.
- `WS /api/audio/ingest/{venue_id}` requires `?token=<token>` query param matching `CAMPTIONS_INGEST_TOKEN` env var.
- Caption viewer WebSocket/SSE and history endpoints are public.
- If tokens are not set in env, the protected endpoints return 500 at startup (fail-closed).
