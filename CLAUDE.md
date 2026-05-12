# EMF Camptions

Live captioning system for EMF Camp. Raspberry Pis capture audio from stage mics, stream it to a central server running WhisperLive (Collabora) for real-time transcription, and distribute captions to display screens and audience devices via WebSocket.

## Architecture

- **Audio ingest**: Pi connects to `WebSocket /api/audio/ingest/{venue_id}` and streams raw PCM (16 kHz, 16-bit signed, mono). The Pi uses `arecord -D plughw:...` so ALSA's plug layer handles any rate conversion from the mic's native rate with proper anti-aliasing. Connection = live session. Disconnect = session ends automatically.
- **Transcription**: WhisperLive (`ghcr.io/collabora/whisperlive-cpu`) runs as a separate Docker container (sidecar) with `--raw_pcm_input`. For each active venue, camptions opens a WebSocket to `ws://wl:9090`, sends a JSON handshake, waits for `SERVER_READY`, then forwards raw PCM bytes. The server returns `{segments: [{text, start, end, completed}]}` messages; `completed: true` = final segment. The WL URL is configured via `CAMPTIONS_WL_URL`.
- **Distribution**: `DistributionManager` broadcasts caption segments and venue status events to all connected WebSocket/SSE caption subscribers.
- **Sessions**: Created automatically when audio connects, ended when it disconnects. No manual control. Segments are stored to SQLite under their session.
- **Schedule**: `ScheduleService` polls the EMF Camp now-and-next API every 60 s and broadcasts `schedule_update` messages to all venue subscribers.

## WhisperLive protocol

WL is launched with `--backend faster_whisper --raw_pcm_input --max_connection_time 3600`. The camptions backend's `WLConnection.ensure()` performs the handshake on every fresh socket:

1. Send JSON: `{uid, language, task: "transcribe", model, use_vad, send_last_n_segments: 10}` (uid = `<venue_id>-<session_id>`).
2. Read messages until `{"message": "SERVER_READY"}` arrives. `{"status": "WAIT"}` (server full) and `{"status": "ERROR"}` raise so backoff applies.
3. Once `SERVER_READY` is observed, `on_state_change(True)` fires — viewers flip to "Live".

Transcription messages from WL look like:

```json
{
  "segments": [
    {"text": "Hello world", "start": 1.23, "end": 2.45, "completed": true},
    {"text": "and we are…", "start": 2.45, "end": 3.10, "completed": false}
  ]
}
```

`completed: true` segments are stable and committed. The last entry may be `completed: false` — that's the in-progress tentative. The backend dedupes committed segments by `start` timestamp so audio-ring replays after a reconnect don't re-emit segments we've already broadcast.

The send loop proactively reconnects every `CAMPTIONS_WL_RECONNECT_INTERVAL` seconds (default 3300 s, comfortably under WL's 1 h `--max_connection_time` cap). On each new WL connection, the last ~5 s of audio (50 × 100 ms chunks) is replayed so Whisper re-enters with sentence context. End-of-stream is the text frame `"END_OF_AUDIO"`.

## Key WebSocket message types (server → caption viewer)

| Type | Meaning |
|------|---------|
| `connected` | Initial handshake; includes `is_live` bool |
| `venue_live` | Pi audio is streaming AND WL is handshaked |
| `venue_offline` | Pi or WL is not currently available |
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
    ├── audio_streamer.py# AudioStreamer — sends PCM queue → WL, with replay ring
    ├── session.py       # VenueSession dataclass, WLConnection, helpers
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
                                 AudioStreamer._send_loop  (ring buffer)
                                          │   (shared WLConnection)
                                          ▼
                                        WL WS
                                          │
                                          ▼
                             TranscriptionProcessor._recv_loop
                                          │
                                          ▼
                                distribution + DB
```

Send and receive share one `WLConnection`. WL going away (either WL's max-connection-time cap firing, or a network glitch, or our proactive reconnect timer) does not end the session — audio keeps queueing, both loops reconnect via `ensure(handshake=...)`, the 5 s replay ring re-seeds context, and transcription resumes. Session ends only when `end_session()` is called (Pi disconnect or shutdown). `committed_starts` survives WL reconnects within one Pi session so replays don't double-emit segments.

## STATIC_DIR resolution

Static files are at `/app/static` in Docker (installed package, different `__file__` path) but at `src/../../../static` in local dev. `main.py` tries the source-relative path first and falls back to `/app/static`.

## Docker

Two services in `docker-compose.yml`:

- **`wl`** — `ghcr.io/collabora/whisperlive-cpu:latest`, started with `--port 9090 --backend faster_whisper --raw_pcm_input --max_connection_time 3600`. Model weights are cached in the `whisper-cache` named volume (`/root/.cache`); subsequent starts are fast.
- **`camptions`** — our FastAPI app. Connects to `ws://wl:9090` per active venue (set via `CAMPTIONS_WL_URL`). Dockerfile uses a two-step pip install: deps-only layer (invalidated only on `pyproject.toml` changes) then `pip install --no-deps .` after copying source.

## Running locally

```bash
pip install -e ".[dev]"
# Run WL separately (CPU):
docker run --rm -p 9090:9090 ghcr.io/collabora/whisperlive-cpu:latest \
  --port 9090 --backend faster_whisper --raw_pcm_input --max_connection_time 3600
CAMPTIONS_WL_URL=ws://localhost:9090 uvicorn camptions.main:app --reload --port 8001
curl -X POST http://localhost:8001/api/admin/init-venues
```

## Running with Docker

```bash
docker compose up --build
curl -X POST http://localhost:8000/api/admin/init-venues
```

## Frontend status states

Both `viewer.html` and `display.html` show three states:
- **Connected · Live** — WebSocket up, Pi audio streaming, WL handshaked
- **Connected · Source Offline** — WebSocket up, but either no Pi audio or WL is not reachable
- **Disconnected** — WebSocket down (reconnects indefinitely with capped exponential backoff, max 30 s)

When switching venues in the viewer, `ws.onclose = null` is set before `ws.close()` to prevent the close event triggering a spurious reconnect loop.

## Authentication

- `/api/admin/*` and `POST/PATCH /api/venues` require `Authorization: Bearer <token>` matching `CAMPTIONS_ADMIN_TOKEN` env var.
- `WS /api/audio/ingest/{venue_id}` requires `?token=<token>` query param matching `CAMPTIONS_INGEST_TOKEN` env var.
- Caption viewer WebSocket/SSE and history endpoints are public.
- If tokens are not set in env, the protected endpoints return 500 at startup (fail-closed).
