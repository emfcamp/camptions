"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .database import close_db, init_db
from .routers import admin, audio, captions, sessions, venues
from .routers import schedule as schedule_router
from .services.rate_limit import RateLimitMiddleware, WSConnectionLimiter
from .services.schedule import schedule_service
from .services.transcription import TranscriptionManager

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# Path to static files — resolve relative to source tree or working directory (Docker)
_src_relative = Path(__file__).parent.parent.parent / "static"
STATIC_DIR = _src_relative if _src_relative.exists() else Path("/app/static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    await init_db()

    transcription_manager = TranscriptionManager(settings)
    await transcription_manager.start()

    await schedule_service.start()

    # Inject transcription manager into routers (avoiding circular imports)
    audio.set_transcription_manager(transcription_manager)
    admin.set_transcription_manager(transcription_manager)
    captions.set_transcription_manager(transcription_manager)
    venues.set_transcription_manager(transcription_manager)

    # Store in app state for access if needed
    app.state.transcription_manager = transcription_manager

    yield

    # Shutdown
    await transcription_manager.stop()
    await schedule_service.stop()
    await close_db()


_API_DESCRIPTION = """
Live captioning data from EMF Camp stages — open, unauthenticated, CORS-free.
Build viewers, archives, accessibility tooling, fun side projects.

### Quick start

Stream a venue's live captions over SSE (no client library needed):

```
curl -N https://captions.emf.camp/api/captions/stream/stage-a/sse
```

Or via WebSocket:

```js
new WebSocket('wss://captions.emf.camp/api/captions/stream/stage-a')
  .onmessage = e => console.log(JSON.parse(e.data));
```

Fetch the recent caption history for a venue:

```
curl 'https://captions.emf.camp/api/captions/history/stage-a?limit=100'
```

List venues and the current EMF schedule:

```
curl https://captions.emf.camp/api/venues
curl https://captions.emf.camp/api/schedule/now-and-next
```

### Streaming protocol

Both the WebSocket and SSE endpoints emit JSON messages with a `type` discriminator —
see the `/api/captions/stream/{{venue_id}}` endpoint description for the full message
catalogue.

### Rate limits

Please be considerate — this whole thing runs on some bodged hardware in the field.

| Limit | Value | Scope |
|-------|-------|-------|
| HTTP requests | **{rate_limit}** per **60 s** per client IP | All `/api/captions`, `/api/venues`, `/api/sessions`, `/api/schedule` endpoints (incl. SSE opens). |
| WebSocket connections | **{ws_limit}** simultaneous per client IP | `/api/captions/stream/{{venue_id}}`. Extras are closed with code `1013` (Try Again Later). |

When you exceed the HTTP limit you'll get a `429 Too Many Requests` with a
`Retry-After: <seconds>` header. Every successful response includes
`X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `X-RateLimit-Window` so
you can pace yourself without tripping the limiter.

`X-Forwarded-For` (first hop) is honoured when set by an upstream proxy.

### Notes for consumers

- Segments are keyed by `(session_id, sequence)`. `sequence` resets per session;
  use the pair to dedupe across reconnects.
- Only `committed` segments are persisted to history. `tentative` segments are
  in-progress text and only appear on the stream.
- WebSocket and SSE are the lowest-cost way to follow a venue — they're a
  single connection regardless of how much text flows. Prefer them over
  polling the history.
- This was mostly vibe coded with Claude. I'm on the fence about if doing it manually would have been faster and/or more fun.
""".format(rate_limit=settings.rate_limit_per_minute, ws_limit=settings.ws_connections_per_ip)

_OPENAPI_TAGS = [
    {
        "name": "captions",
        "description": "Live caption streams (WebSocket / SSE) and historical segment lookup.",
    },
    {"name": "venues", "description": "List and inspect the stages we caption."},
    {
        "name": "sessions",
        "description": "Per-venue transcription sessions — useful for correlating segments to talks.",
    },
    {
        "name": "schedule",
        "description": "Now-and-next data sourced from the EMF Camp schedule API.",
    },
]

app = FastAPI(
    title="EMF Camptions Public API",
    version="1.0",
    summary="Live captions for EMF Camp stages.",
    description=_API_DESCRIPTION,
    openapi_tags=_OPENAPI_TAGS,
    contact={"name": "EMF Camptions", "url": "https://github.com/emfcamp/captions"},
    license_info={"name": "MIT"},
    redoc_url=None,
    lifespan=lifespan,
)

# CORS middleware — public viewer endpoints; credentials not needed
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Per-IP rate limit on the public API. Internal routers (audio ingest,
# admin) are deliberately excluded — admin is token-gated and the Pi is on
# a known LAN. The limit is documented in the OpenAPI description.
_PUBLIC_RATE_LIMIT_PREFIXES = (
    "/api/captions",
    "/api/venues",
    "/api/sessions",
    "/api/schedule",
)
app.add_middleware(
    RateLimitMiddleware,
    limit=settings.rate_limit_per_minute,
    window_seconds=60.0,
    path_prefixes=_PUBLIC_RATE_LIMIT_PREFIXES,
)

# WebSocket connection limiter — shared instance used by the captions router.
ws_limiter = WSConnectionLimiter(settings.ws_connections_per_ip)
captions.set_ws_limiter(ws_limiter)

# Public API routers — surfaced in /docs.
app.include_router(captions.router, prefix="/api/captions", tags=["captions"])
app.include_router(venues.router, prefix="/api/venues", tags=["venues"])
app.include_router(sessions.router, prefix="/api/sessions", tags=["sessions"])
app.include_router(schedule_router.router, prefix="/api/schedule", tags=["schedule"])

# Internal routers — hidden from /docs. The Pi ingest WS and admin endpoints
# aren't part of the public API contract and may change at any time.
app.include_router(
    audio.router, prefix="/api/audio", tags=["audio"], include_in_schema=False
)
app.include_router(
    admin.router, prefix="/api/admin", tags=["admin"], include_in_schema=False
)

# Static files (only if directory exists)
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
async def viewer_root():
    return FileResponse(STATIC_DIR / "viewer.html")


@app.get("/v/{venue_id}", include_in_schema=False)
async def viewer_for_venue(venue_id: str):
    return FileResponse(STATIC_DIR / "viewer.html")


@app.get("/display", include_in_schema=False)
async def display_default():
    return FileResponse(STATIC_DIR / "display.html")


@app.get("/display/{venue_id}", include_in_schema=False)
async def display_for_venue(venue_id: str):
    return FileResponse(STATIC_DIR / "display.html")


@app.get("/admin", include_in_schema=False)
async def admin_page():
    return FileResponse(STATIC_DIR / "admin.html")


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}
