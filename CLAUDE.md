# EMF Camptions

Live captioning system for EMF Camp. Raspberry Pis capture audio from stage mics, stream it to a central server running WhisperLiveKit for real-time transcription, and distribute captions to display screens and audience devices via WebSocket.

## Architecture

- **Audio ingest**: Pi connects to `WebSocket /api/audio/ingest/{venue_id}` and streams raw PCM (16kHz, 16-bit, mono). Connection = live session. Disconnect = session ends automatically.
- **Transcription**: WhisperLiveKit with SimulStreaming (faster-whisper backend). One `TranscriptionEngine` shared across all venues; one `AudioProcessor` per active venue.
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

- Whisper model is pre-downloaded at **build time** (`ARG WHISPER_MODEL`) so the server starts instantly and works offline at the venue.
- `HF_TOKEN` is passed as a **BuildKit secret** (`--mount=type=secret,id=hf_token`) — never stored in an image layer.
- `whisper-cache` named volume mounts over `/root/.cache/huggingface` — any runtime model files persist across container recreations.
- Dockerfile uses a two-step pip install: deps-only layer (invalidated only on `pyproject.toml` changes) then `pip install --no-deps .` after copying source (fast, no network).

## Running locally

```bash
pip install -e ".[dev]"
uvicorn camptions.main:app --reload
curl -X POST http://localhost:8000/api/admin/init-venues
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
