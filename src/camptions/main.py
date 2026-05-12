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
from .routers import admin, audio, captions, venues
from .routers import schedule as schedule_router
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

    # Store in app state for access if needed
    app.state.transcription_manager = transcription_manager

    yield

    # Shutdown
    await transcription_manager.stop()
    await schedule_service.stop()
    await close_db()


app = FastAPI(
    title="EMF Camptions",
    version="2.0.0",
    description="Live captioning system for EMF Camp using WhisperLiveKit",
    lifespan=lifespan,
)

# CORS middleware — public viewer endpoints; credentials not needed
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routers
app.include_router(audio.router, prefix="/api/audio", tags=["audio"])
app.include_router(captions.router, prefix="/api/captions", tags=["captions"])
app.include_router(venues.router, prefix="/api/venues", tags=["venues"])
app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
app.include_router(schedule_router.router, prefix="/api/schedule", tags=["schedule"])

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
