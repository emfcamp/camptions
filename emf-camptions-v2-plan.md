# EMF Camptions v2 - Implementation Plan

**Version:** 1.0  
**Date:** January 2026  
**Purpose:** Complete rewrite specification for live captioning system using WhisperLiveKit

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [System Architecture](#2-system-architecture)
3. [Backend Implementation](#3-backend-implementation)
4. [Frontend Implementation](#4-frontend-implementation)
5. [Raspberry Pi Setup](#5-raspberry-pi-setup)
6. [Deployment Configuration](#6-deployment-configuration)
7. [File Structure](#7-file-structure)
8. [Implementation Phases](#8-implementation-phases)

---

## 1. Executive Summary

### Problem Statement

The original camptions system suffered from transcript segment timing variability, causing:
- Segments with inconsistent start/end times
- Backend unable to correctly order/deduplicate segments
- Frontend displaying missed or duplicated content
- No unique identifiers for segment tracking

### Solution

A complete rewrite using **WhisperLiveKit** with its SimulStreaming backend, which provides:
- **AlignAtt policy** for consistent, stable incremental transcription output
- **Built-in segment management** with stable timestamps
- **VAD (Voice Activity Detection)** to reduce spurious segments
- **Single Python stack** for simplified deployment

### Key Technologies

| Component | Technology | Rationale |
|-----------|------------|-----------|
| Transcription Engine | WhisperLiveKit (SimulStreaming) | SOTA 2025, stable timestamps |
| Backend Framework | FastAPI | Async, WebSocket native, Python ecosystem |
| Database | SQLite (production: PostgreSQL) | Simple, reliable, easy backup |
| Frontend | Vanilla HTML/CSS/JS | No build step, on-site debugging |
| Edge Audio Capture | Python + PyAudio | Direct integration with WhisperLiveKit |
| Display Screens | Chromium Kiosk | Reliable, well-supported |

---

## 2. System Architecture

### High-Level Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              EMF NETWORK                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐                │
│  │   Stage A    │     │   Stage B    │     │   Stage C    │                │
│  │  Raspberry   │     │  Raspberry   │     │  Raspberry   │                │
│  │  Pi (Audio)  │     │  Pi (Audio)  │     │  Pi (Audio)  │                │
│  └──────┬───────┘     └──────┬───────┘     └──────┬───────┘                │
│         │                    │                    │                         │
│         │ WebSocket          │ WebSocket          │ WebSocket               │
│         │ (raw audio)        │ (raw audio)        │ (raw audio)             │
│         │                    │                    │                         │
│         └────────────────────┼────────────────────┘                         │
│                              │                                              │
│                              ▼                                              │
│                    ┌─────────────────────┐                                  │
│                    │   Central Server    │                                  │
│                    │  ┌───────────────┐  │                                  │
│                    │  │WhisperLiveKit │  │                                  │
│                    │  │  (GPU/CPU)    │  │                                  │
│                    │  └───────┬───────┘  │                                  │
│                    │          │          │                                  │
│                    │  ┌───────▼───────┐  │                                  │
│                    │  │ Caption       │  │                                  │
│                    │  │ Distributor   │  │                                  │
│                    │  └───────┬───────┘  │                                  │
│                    │          │          │                                  │
│                    │  ┌───────▼───────┐  │                                  │
│                    │  │  Database     │  │                                  │
│                    │  │  (SQLite)     │  │                                  │
│                    │  └───────────────┘  │                                  │
│                    └─────────┬───────────┘                                  │
│                              │                                              │
│         ┌────────────────────┼────────────────────┐                         │
│         │                    │                    │                         │
│         ▼                    ▼                    ▼                         │
│  ┌─────────────┐      ┌─────────────┐      ┌─────────────┐                 │
│  │Large Display│      │Large Display│      │ User Phones │                 │
│  │  (Stage A)  │      │  (Stage B)  │      │ (Browser)   │                 │
│  └─────────────┘      └─────────────┘      └─────────────┘                 │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Data Flow

```
Audio Input ──► WhisperLiveKit ──► Caption Segments ──► Distribution ──► Displays
   │                 │                    │                  │              │
   │                 │                    │                  │              │
   ▼                 ▼                    ▼                  ▼              ▼
16kHz PCM      SimulStreaming       JSON with UUID      WebSocket       Render
s16le mono     AlignAtt policy      + timestamps          + SSE         captions
```

### Segment Data Model

Each caption segment has a unique identifier and stable timestamps:

```json
{
  "id": "uuid-v4",
  "venue_id": "stage-a",
  "session_id": "uuid-v4",
  "type": "committed",
  "text": "Welcome to EMF camp",
  "speaker": "Speaker 1",
  "start_time": 1706540400.123,
  "end_time": 1706540402.456,
  "sequence": 42,
  "created_at": "2026-01-29T14:00:00.123Z"
}
```

**Segment Types:**
- `tentative`: Still being refined by WhisperLiveKit (may change)
- `committed`: Finalised transcription (will not change)
- `final`: End of utterance marker

---

## 3. Backend Implementation

### 3.1 Project Structure

```
camptions-server/
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── alembic.ini
├── alembic/
│   └── versions/
├── src/
│   └── camptions/
│       ├── __init__.py
│       ├── main.py              # FastAPI application
│       ├── config.py            # Configuration management
│       ├── models.py            # SQLAlchemy models
│       ├── schemas.py           # Pydantic schemas
│       ├── database.py          # Database connection
│       ├── routers/
│       │   ├── __init__.py
│       │   ├── audio.py         # Audio ingestion WebSocket
│       │   ├── captions.py      # Caption distribution
│       │   ├── venues.py        # Venue management
│       │   └── admin.py         # Admin endpoints
│       ├── services/
│       │   ├── __init__.py
│       │   ├── transcription.py # WhisperLiveKit integration
│       │   ├── distribution.py  # Caption broadcasting
│       │   └── storage.py       # Persistence layer
│       └── core/
│           ├── __init__.py
│           ├── events.py        # Event bus for pub/sub
│           └── middleware.py    # Logging, CORS, etc.
├── static/
│   ├── display.html             # Large screen view
│   ├── viewer.html              # User device view
│   ├── admin.html               # Admin interface
│   ├── css/
│   │   └── captions.css
│   └── js/
│       ├── caption-client.js
│       └── admin.js
└── tests/
    ├── conftest.py
    ├── test_transcription.py
    └── test_distribution.py
```

### 3.2 Core Dependencies

```toml
# pyproject.toml
[project]
name = "camptions-server"
version = "2.0.0"
requires-python = ">=3.10"
dependencies = [
    "fastapi>=0.109.0",
    "uvicorn[standard]>=0.27.0",
    "whisperlivekit>=0.2.17",
    "sqlalchemy>=2.0.0",
    "alembic>=1.13.0",
    "aiosqlite>=0.19.0",
    "python-dotenv>=1.0.0",
    "pydantic>=2.5.0",
    "pydantic-settings>=2.1.0",
    "websockets>=12.0",
    "httpx>=0.26.0",
]

[project.optional-dependencies]
gpu = [
    "faster-whisper>=1.0.0",
]
dev = [
    "pytest>=7.4.0",
    "pytest-asyncio>=0.23.0",
    "ruff>=0.1.0",
]
```

### 3.3 Configuration

```python
# src/camptions/config.py
from pydantic_settings import BaseSettings
from typing import Literal

class Settings(BaseSettings):
    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False
    
    # Database
    database_url: str = "sqlite+aiosqlite:///./camptions.db"
    
    # WhisperLiveKit
    whisper_model: str = "medium"
    whisper_language: str = "en"
    whisper_backend: Literal["auto", "faster-whisper", "whisper"] = "auto"
    whisper_backend_policy: Literal["simulstreaming", "localagreement"] = "simulstreaming"
    enable_diarization: bool = False
    enable_vad: bool = True
    
    # Venues
    default_venues: list[str] = ["stage-a", "stage-b", "stage-c", "workshop"]
    
    # Retention
    caption_retention_hours: int = 72
    
    class Config:
        env_file = ".env"
        env_prefix = "CAMPTIONS_"
```

### 3.4 Database Models

```python
# src/camptions/models.py
from datetime import datetime
from sqlalchemy import Column, String, Float, Integer, DateTime, Text, ForeignKey, Index
from sqlalchemy.orm import relationship, DeclarativeBase
import uuid

class Base(DeclarativeBase):
    pass

class Venue(Base):
    __tablename__ = "venues"
    
    id = Column(String(50), primary_key=True)
    name = Column(String(100), nullable=False)
    description = Column(Text)
    is_active = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    sessions = relationship("Session", back_populates="venue")

class Session(Base):
    __tablename__ = "sessions"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    venue_id = Column(String(50), ForeignKey("venues.id"), nullable=False)
    title = Column(String(200))
    started_at = Column(DateTime, default=datetime.utcnow)
    ended_at = Column(DateTime)
    
    venue = relationship("Venue", back_populates="sessions")
    segments = relationship("Segment", back_populates="session")

class Segment(Base):
    __tablename__ = "segments"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String(36), ForeignKey("sessions.id"), nullable=False)
    sequence = Column(Integer, nullable=False)
    segment_type = Column(String(20), nullable=False)  # tentative, committed, final
    text = Column(Text, nullable=False)
    speaker = Column(String(50))
    start_time = Column(Float, nullable=False)
    end_time = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    session = relationship("Session", back_populates="segments")
    
    __table_args__ = (
        Index("idx_session_sequence", "session_id", "sequence"),
        Index("idx_session_type", "session_id", "segment_type"),
    )
```

### 3.5 Main Application

```python
# src/camptions/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from .config import Settings
from .database import init_db, close_db
from .services.transcription import TranscriptionManager
from .routers import audio, captions, venues, admin

settings = Settings()
transcription_manager: TranscriptionManager = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global transcription_manager
    
    # Startup
    await init_db()
    transcription_manager = TranscriptionManager(settings)
    await transcription_manager.start()
    
    yield
    
    # Shutdown
    await transcription_manager.stop()
    await close_db()

app = FastAPI(
    title="EMF Camptions",
    version="2.0.0",
    lifespan=lifespan
)

# CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(audio.router, prefix="/api/audio", tags=["audio"])
app.include_router(captions.router, prefix="/api/captions", tags=["captions"])
app.include_router(venues.router, prefix="/api/venues", tags=["venues"])
app.include_router(admin.router, prefix="/api/admin", tags=["admin"])

# Static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Convenience redirects
@app.get("/")
async def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/static/viewer.html")

@app.get("/display")
async def display():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/static/display.html")
```

### 3.6 Transcription Service

```python
# src/camptions/services/transcription.py
import asyncio
import uuid
from datetime import datetime
from typing import AsyncGenerator, Optional
from dataclasses import dataclass, field

from whisperlivekit import TranscriptionEngine, AudioProcessor

from ..config import Settings
from ..schemas import SegmentCreate
from .distribution import DistributionManager

@dataclass
class VenueTranscriber:
    """Manages transcription for a single venue."""
    venue_id: str
    session_id: str
    audio_processor: AudioProcessor
    sequence: int = 0
    
class TranscriptionManager:
    """Manages WhisperLiveKit and per-venue transcription."""
    
    def __init__(self, settings: Settings):
        self.settings = settings
        self.engine: Optional[TranscriptionEngine] = None
        self.venues: dict[str, VenueTranscriber] = {}
        self.distribution: Optional[DistributionManager] = None
        
    async def start(self):
        """Initialize the shared transcription engine."""
        self.engine = TranscriptionEngine(
            model=self.settings.whisper_model,
            lan=self.settings.whisper_language,
            backend=self.settings.whisper_backend,
            backend_policy=self.settings.whisper_backend_policy,
            diarization=self.settings.enable_diarization,
            vad=self.settings.enable_vad,
        )
        self.distribution = DistributionManager()
        
    async def stop(self):
        """Cleanup resources."""
        for venue in self.venues.values():
            await self.end_session(venue.venue_id)
        self.venues.clear()
        
    async def start_session(self, venue_id: str, title: str = None) -> str:
        """Start a new transcription session for a venue."""
        if venue_id in self.venues:
            await self.end_session(venue_id)
            
        session_id = str(uuid.uuid4())
        audio_processor = AudioProcessor(transcription_engine=self.engine)
        
        self.venues[venue_id] = VenueTranscriber(
            venue_id=venue_id,
            session_id=session_id,
            audio_processor=audio_processor,
        )
        
        # Start processing task
        asyncio.create_task(self._process_results(venue_id))
        
        return session_id
        
    async def end_session(self, venue_id: str):
        """End transcription session for a venue."""
        if venue_id not in self.venues:
            return
            
        venue = self.venues.pop(venue_id)
        # Signal end of session to distribution
        await self.distribution.broadcast(venue_id, {
            "type": "session_end",
            "session_id": venue.session_id,
            "timestamp": datetime.utcnow().isoformat(),
        })
        
    async def process_audio(self, venue_id: str, audio_data: bytes):
        """Process incoming audio for a venue."""
        if venue_id not in self.venues:
            raise ValueError(f"No active session for venue: {venue_id}")
            
        venue = self.venues[venue_id]
        await venue.audio_processor.process_audio(audio_data)
        
    async def _process_results(self, venue_id: str):
        """Process transcription results and distribute them."""
        if venue_id not in self.venues:
            return
            
        venue = self.venues[venue_id]
        results_generator = await venue.audio_processor.create_tasks()
        
        try:
            async for result in results_generator:
                if venue_id not in self.venues:
                    break
                    
                venue.sequence += 1
                
                # Map WhisperLiveKit output to our segment format
                segment = {
                    "id": str(uuid.uuid4()),
                    "session_id": venue.session_id,
                    "venue_id": venue_id,
                    "sequence": venue.sequence,
                    "type": self._map_segment_type(result.get("type", "partial")),
                    "text": result.get("text", ""),
                    "speaker": result.get("speaker"),
                    "start_time": result.get("start"),
                    "end_time": result.get("end"),
                    "timestamp": datetime.utcnow().isoformat(),
                }
                
                # Broadcast to connected clients
                await self.distribution.broadcast(venue_id, segment)
                
                # Store committed segments
                if segment["type"] == "committed":
                    await self._store_segment(segment)
                    
        except Exception as e:
            print(f"Error processing results for {venue_id}: {e}")
            
    def _map_segment_type(self, wlk_type: str) -> str:
        """Map WhisperLiveKit types to our types."""
        mapping = {
            "partial": "tentative",
            "complete": "committed",
            "final": "final",
        }
        return mapping.get(wlk_type, "tentative")
        
    async def _store_segment(self, segment: dict):
        """Persist committed segment to database."""
        from ..database import get_db
        from ..models import Segment
        
        async for db in get_db():
            db_segment = Segment(
                id=segment["id"],
                session_id=segment["session_id"],
                sequence=segment["sequence"],
                segment_type=segment["type"],
                text=segment["text"],
                speaker=segment.get("speaker"),
                start_time=segment["start_time"],
                end_time=segment.get("end_time"),
            )
            db.add(db_segment)
            await db.commit()
```

### 3.7 Distribution Service

```python
# src/camptions/services/distribution.py
import asyncio
import json
from typing import Dict, Set
from fastapi import WebSocket
from dataclasses import dataclass, field

@dataclass
class VenueSubscribers:
    """Track subscribers for a venue."""
    websockets: Set[WebSocket] = field(default_factory=set)
    
class DistributionManager:
    """Manages real-time caption distribution to clients."""
    
    def __init__(self):
        self.venues: Dict[str, VenueSubscribers] = {}
        self._lock = asyncio.Lock()
        
    async def subscribe(self, venue_id: str, websocket: WebSocket):
        """Subscribe a client to venue captions."""
        async with self._lock:
            if venue_id not in self.venues:
                self.venues[venue_id] = VenueSubscribers()
            self.venues[venue_id].websockets.add(websocket)
            
    async def unsubscribe(self, venue_id: str, websocket: WebSocket):
        """Unsubscribe a client from venue captions."""
        async with self._lock:
            if venue_id in self.venues:
                self.venues[venue_id].websockets.discard(websocket)
                
    async def broadcast(self, venue_id: str, message: dict):
        """Broadcast a message to all subscribers of a venue."""
        if venue_id not in self.venues:
            return
            
        dead_sockets = set()
        message_json = json.dumps(message)
        
        for websocket in self.venues[venue_id].websockets:
            try:
                await websocket.send_text(message_json)
            except Exception:
                dead_sockets.add(websocket)
                
        # Cleanup dead connections
        if dead_sockets:
            async with self._lock:
                self.venues[venue_id].websockets -= dead_sockets
```

### 3.8 Audio Ingestion Router

```python
# src/camptions/routers/audio.py
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from ..main import transcription_manager

router = APIRouter()

@router.websocket("/ingest/{venue_id}")
async def audio_ingest(
    websocket: WebSocket,
    venue_id: str,
    session_title: str = Query(None),
):
    """
    WebSocket endpoint for audio ingestion from Raspberry Pi.
    
    Expects raw PCM audio: 16kHz, 16-bit signed, mono (s16le)
    """
    await websocket.accept()
    
    # Start transcription session
    session_id = await transcription_manager.start_session(venue_id, session_title)
    
    try:
        await websocket.send_json({
            "type": "session_started",
            "session_id": session_id,
            "venue_id": venue_id,
        })
        
        while True:
            # Receive raw audio bytes
            audio_data = await websocket.receive_bytes()
            await transcription_manager.process_audio(venue_id, audio_data)
            
    except WebSocketDisconnect:
        print(f"Audio source disconnected: {venue_id}")
    except Exception as e:
        print(f"Audio ingestion error for {venue_id}: {e}")
    finally:
        await transcription_manager.end_session(venue_id)
```

### 3.9 Caption Distribution Router

```python
# src/camptions/routers/captions.py
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from datetime import datetime, timedelta
import json
import asyncio

from ..main import transcription_manager
from ..database import get_db
from ..models import Segment, Session

router = APIRouter()

@router.websocket("/stream/{venue_id}")
async def caption_stream(websocket: WebSocket, venue_id: str):
    """
    WebSocket endpoint for receiving live captions.
    
    Clients connect here to receive real-time caption updates.
    """
    await websocket.accept()
    
    distribution = transcription_manager.distribution
    await distribution.subscribe(venue_id, websocket)
    
    try:
        # Send connection confirmation
        await websocket.send_json({
            "type": "connected",
            "venue_id": venue_id,
            "timestamp": datetime.utcnow().isoformat(),
        })
        
        # Keep connection alive
        while True:
            try:
                # Handle any client messages (e.g., ping/pong)
                message = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=30.0
                )
                if message == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                # Send keepalive
                await websocket.send_json({"type": "keepalive"})
                
    except WebSocketDisconnect:
        pass
    finally:
        await distribution.unsubscribe(venue_id, websocket)

@router.get("/stream/{venue_id}/sse")
async def caption_stream_sse(venue_id: str):
    """
    Server-Sent Events endpoint for caption streaming.
    
    Alternative to WebSocket for simpler client implementations.
    """
    async def event_generator():
        queue = asyncio.Queue()
        
        # Custom subscriber that puts messages in queue
        # (simplified - real implementation would integrate with distribution manager)
        
        while True:
            try:
                data = await asyncio.wait_for(queue.get(), timeout=15.0)
                yield f"data: {json.dumps(data)}\n\n"
            except asyncio.TimeoutError:
                yield f": keepalive\n\n"
                
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )

@router.get("/history/{venue_id}")
async def get_caption_history(
    venue_id: str,
    limit: int = Query(100, ge=1, le=1000),
    since: datetime = Query(None),
):
    """Get historical captions for a venue."""
    async for db in get_db():
        query = (
            select(Segment)
            .join(Session)
            .where(Session.venue_id == venue_id)
            .where(Segment.segment_type == "committed")
            .order_by(Segment.created_at.desc())
            .limit(limit)
        )
        
        if since:
            query = query.where(Segment.created_at > since)
            
        result = await db.execute(query)
        segments = result.scalars().all()
        
        return {
            "venue_id": venue_id,
            "count": len(segments),
            "segments": [
                {
                    "id": s.id,
                    "text": s.text,
                    "speaker": s.speaker,
                    "start_time": s.start_time,
                    "end_time": s.end_time,
                    "created_at": s.created_at.isoformat(),
                }
                for s in reversed(segments)
            ]
        }
```

---

## 4. Frontend Implementation

### 4.1 Display Page (Large Screens)

```html
<!-- static/display.html -->
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>EMF Captions</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        :root {
            --bg-color: #000000;
            --text-color: #ffffff;
            --speaker-color: #ffcc00;
            --tentative-opacity: 0.6;
            --font-size: 4vw;
            --line-height: 1.4;
            --padding: 2rem;
        }
        
        body {
            background: var(--bg-color);
            color: var(--text-color);
            font-family: 'Segoe UI', system-ui, sans-serif;
            font-size: var(--font-size);
            line-height: var(--line-height);
            min-height: 100vh;
            padding: var(--padding);
            overflow: hidden;
        }
        
        /* High contrast mode */
        body.high-contrast {
            --bg-color: #000000;
            --text-color: #ffff00;
            --speaker-color: #00ffff;
        }
        
        /* Light mode */
        body.light {
            --bg-color: #ffffff;
            --text-color: #000000;
            --speaker-color: #0066cc;
        }
        
        #status {
            position: fixed;
            top: 1rem;
            right: 1rem;
            padding: 0.5rem 1rem;
            border-radius: 0.5rem;
            font-size: 1rem;
            z-index: 100;
        }
        
        #status.connected {
            background: #22c55e;
            color: #000;
        }
        
        #status.disconnected {
            background: #ef4444;
            color: #fff;
        }
        
        #status.reconnecting {
            background: #f59e0b;
            color: #000;
            animation: pulse 1s infinite;
        }
        
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        
        #captions {
            display: flex;
            flex-direction: column;
            justify-content: flex-end;
            min-height: calc(100vh - 4rem);
        }
        
        .caption-line {
            margin-bottom: 0.5em;
            animation: fadeIn 0.3s ease-out;
        }
        
        @keyframes fadeIn {
            from {
                opacity: 0;
                transform: translateY(10px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }
        
        .speaker {
            color: var(--speaker-color);
            font-weight: 600;
        }
        
        .tentative {
            opacity: var(--tentative-opacity);
            font-style: italic;
        }
        
        .committed {
            opacity: 1;
        }
        
        /* Cursor for ongoing speech */
        .tentative::after {
            content: '▋';
            animation: blink 1s infinite;
        }
        
        @keyframes blink {
            0%, 100% { opacity: 1; }
            50% { opacity: 0; }
        }
        
        /* Scrollbar styling */
        ::-webkit-scrollbar {
            width: 8px;
        }
        
        ::-webkit-scrollbar-track {
            background: transparent;
        }
        
        ::-webkit-scrollbar-thumb {
            background: rgba(255, 255, 255, 0.3);
            border-radius: 4px;
        }
    </style>
</head>
<body>
    <div id="status" class="disconnected">Connecting...</div>
    <div id="captions"></div>
    
    <script>
        // Configuration from URL params
        const params = new URLSearchParams(window.location.search);
        const venue = params.get('venue') || 'stage-a';
        const mode = params.get('mode') || 'dark';
        const fontSize = params.get('fontSize');
        const maxLines = parseInt(params.get('maxLines')) || 8;
        
        // Apply mode
        if (mode === 'light') document.body.classList.add('light');
        if (mode === 'high-contrast') document.body.classList.add('high-contrast');
        if (fontSize) document.documentElement.style.setProperty('--font-size', fontSize);
        
        // State
        let ws = null;
        let reconnectAttempts = 0;
        const maxReconnectAttempts = 10;
        const baseReconnectDelay = 1000;
        
        // Track current tentative segment for updates
        let currentTentativeId = null;
        
        const captionsEl = document.getElementById('captions');
        const statusEl = document.getElementById('status');
        
        function setStatus(status, text) {
            statusEl.className = status;
            statusEl.textContent = text;
        }
        
        function connect() {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${protocol}//${window.location.host}/api/captions/stream/${venue}`;
            
            setStatus('reconnecting', 'Connecting...');
            
            ws = new WebSocket(wsUrl);
            
            ws.onopen = () => {
                setStatus('connected', 'Live');
                reconnectAttempts = 0;
            };
            
            ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    handleMessage(data);
                } catch (e) {
                    console.error('Failed to parse message:', e);
                }
            };
            
            ws.onclose = () => {
                setStatus('disconnected', 'Disconnected');
                scheduleReconnect();
            };
            
            ws.onerror = (error) => {
                console.error('WebSocket error:', error);
            };
        }
        
        function scheduleReconnect() {
            if (reconnectAttempts >= maxReconnectAttempts) {
                setStatus('disconnected', 'Connection failed');
                return;
            }
            
            reconnectAttempts++;
            const delay = Math.min(
                baseReconnectDelay * Math.pow(2, reconnectAttempts - 1),
                30000
            );
            
            setStatus('reconnecting', `Reconnecting in ${Math.round(delay/1000)}s...`);
            setTimeout(connect, delay);
        }
        
        function handleMessage(data) {
            switch (data.type) {
                case 'connected':
                case 'keepalive':
                    // Ignore meta messages
                    break;
                    
                case 'tentative':
                    updateTentative(data);
                    break;
                    
                case 'committed':
                    commitSegment(data);
                    break;
                    
                case 'final':
                    finishUtterance(data);
                    break;
                    
                case 'session_end':
                    addSystemMessage('Session ended');
                    break;
                    
                default:
                    console.log('Unknown message type:', data.type);
            }
        }
        
        function updateTentative(data) {
            let el = document.getElementById('tentative-segment');
            
            if (!el) {
                el = document.createElement('div');
                el.id = 'tentative-segment';
                el.className = 'caption-line tentative';
                captionsEl.appendChild(el);
            }
            
            el.innerHTML = formatSegment(data);
            currentTentativeId = data.id;
            scrollToBottom();
        }
        
        function commitSegment(data) {
            // Remove tentative if it matches
            const tentativeEl = document.getElementById('tentative-segment');
            if (tentativeEl) {
                tentativeEl.remove();
            }
            
            // Add committed segment
            const el = document.createElement('div');
            el.className = 'caption-line committed';
            el.dataset.id = data.id;
            el.innerHTML = formatSegment(data);
            captionsEl.appendChild(el);
            
            // Trim old lines
            trimLines();
            scrollToBottom();
        }
        
        function finishUtterance(data) {
            // Mark end of speaker turn - could add visual separator
            const tentativeEl = document.getElementById('tentative-segment');
            if (tentativeEl) {
                tentativeEl.remove();
            }
        }
        
        function formatSegment(data) {
            let html = '';
            
            if (data.speaker) {
                html += `<span class="speaker">[${escapeHtml(data.speaker)}]</span> `;
            }
            
            html += escapeHtml(data.text);
            
            return html;
        }
        
        function addSystemMessage(text) {
            const el = document.createElement('div');
            el.className = 'caption-line system';
            el.style.opacity = '0.5';
            el.style.fontSize = '0.7em';
            el.textContent = `— ${text} —`;
            captionsEl.appendChild(el);
            scrollToBottom();
        }
        
        function trimLines() {
            while (captionsEl.children.length > maxLines) {
                captionsEl.removeChild(captionsEl.firstChild);
            }
        }
        
        function scrollToBottom() {
            captionsEl.scrollTop = captionsEl.scrollHeight;
        }
        
        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }
        
        // Start connection
        connect();
        
        // Handle visibility changes (reconnect when tab becomes visible)
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible' && (!ws || ws.readyState !== WebSocket.OPEN)) {
                reconnectAttempts = 0;
                connect();
            }
        });
        
        // Fullscreen on click (useful for kiosk setup)
        document.body.addEventListener('dblclick', () => {
            if (!document.fullscreenElement) {
                document.documentElement.requestFullscreen();
            } else {
                document.exitFullscreen();
            }
        });
    </script>
</body>
</html>
```

### 4.2 Viewer Page (User Devices)

```html
<!-- static/viewer.html -->
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=yes">
    <meta name="theme-color" content="#1a1a2e">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <title>EMF Captions</title>
    <link rel="manifest" href="/static/manifest.json">
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        :root {
            --bg-color: #1a1a2e;
            --surface-color: #16213e;
            --text-color: #eaeaea;
            --text-muted: #a0a0a0;
            --accent-color: #e94560;
            --speaker-color: #0f4c75;
            --success-color: #22c55e;
            --warning-color: #f59e0b;
            --error-color: #ef4444;
        }
        
        body {
            background: var(--bg-color);
            color: var(--text-color);
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
        }
        
        body.light {
            --bg-color: #f5f5f5;
            --surface-color: #ffffff;
            --text-color: #1a1a1a;
            --text-muted: #666666;
        }
        
        header {
            background: var(--surface-color);
            padding: 1rem;
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 1px solid rgba(255,255,255,0.1);
            position: sticky;
            top: 0;
            z-index: 100;
        }
        
        .logo {
            font-weight: 700;
            font-size: 1.25rem;
        }
        
        .status-indicator {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            font-size: 0.875rem;
        }
        
        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--error-color);
        }
        
        .status-dot.connected {
            background: var(--success-color);
        }
        
        .status-dot.reconnecting {
            background: var(--warning-color);
            animation: pulse 1s infinite;
        }
        
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.4; }
        }
        
        .venue-selector {
            background: var(--surface-color);
            padding: 1rem;
            border-bottom: 1px solid rgba(255,255,255,0.1);
        }
        
        .venue-tabs {
            display: flex;
            gap: 0.5rem;
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
            scrollbar-width: none;
        }
        
        .venue-tabs::-webkit-scrollbar {
            display: none;
        }
        
        .venue-tab {
            padding: 0.5rem 1rem;
            border-radius: 2rem;
            background: transparent;
            border: 1px solid var(--text-muted);
            color: var(--text-muted);
            font-size: 0.875rem;
            white-space: nowrap;
            cursor: pointer;
            transition: all 0.2s;
        }
        
        .venue-tab:hover {
            border-color: var(--text-color);
            color: var(--text-color);
        }
        
        .venue-tab.active {
            background: var(--accent-color);
            border-color: var(--accent-color);
            color: white;
        }
        
        main {
            flex: 1;
            padding: 1rem;
            overflow-y: auto;
        }
        
        #captions {
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
        }
        
        .caption-line {
            padding: 0.75rem;
            background: var(--surface-color);
            border-radius: 0.5rem;
            animation: slideIn 0.2s ease-out;
        }
        
        @keyframes slideIn {
            from {
                opacity: 0;
                transform: translateY(10px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }
        
        .caption-line.tentative {
            opacity: 0.7;
            border-left: 3px solid var(--warning-color);
        }
        
        .caption-line.committed {
            border-left: 3px solid var(--success-color);
        }
        
        .speaker-name {
            font-size: 0.75rem;
            color: var(--accent-color);
            margin-bottom: 0.25rem;
            font-weight: 600;
        }
        
        .caption-text {
            font-size: var(--caption-font-size, 1rem);
            line-height: 1.5;
        }
        
        .caption-time {
            font-size: 0.7rem;
            color: var(--text-muted);
            margin-top: 0.25rem;
        }
        
        footer {
            background: var(--surface-color);
            padding: 1rem;
            border-top: 1px solid rgba(255,255,255,0.1);
        }
        
        .settings {
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        
        .font-controls {
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }
        
        .font-btn {
            width: 2.5rem;
            height: 2.5rem;
            border-radius: 0.5rem;
            background: var(--bg-color);
            border: 1px solid var(--text-muted);
            color: var(--text-color);
            font-size: 1.25rem;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        
        .font-btn:hover {
            border-color: var(--text-color);
        }
        
        .theme-toggle {
            padding: 0.5rem 1rem;
            border-radius: 0.5rem;
            background: var(--bg-color);
            border: 1px solid var(--text-muted);
            color: var(--text-color);
            cursor: pointer;
        }
        
        /* Empty state */
        .empty-state {
            text-align: center;
            padding: 3rem 1rem;
            color: var(--text-muted);
        }
        
        .empty-state-icon {
            font-size: 3rem;
            margin-bottom: 1rem;
        }
    </style>
</head>
<body>
    <header>
        <div class="logo">EMF Captions</div>
        <div class="status-indicator">
            <span class="status-dot" id="statusDot"></span>
            <span id="statusText">Connecting...</span>
        </div>
    </header>
    
    <nav class="venue-selector">
        <div class="venue-tabs" id="venueTabs">
            <!-- Populated dynamically -->
        </div>
    </nav>
    
    <main>
        <div id="captions">
            <div class="empty-state" id="emptyState">
                <div class="empty-state-icon">🎤</div>
                <p>Waiting for captions...</p>
                <p style="font-size: 0.875rem; margin-top: 0.5rem;">
                    Select a venue above to see live transcription
                </p>
            </div>
        </div>
    </main>
    
    <footer>
        <div class="settings">
            <div class="font-controls">
                <button class="font-btn" id="fontDecrease">A-</button>
                <button class="font-btn" id="fontIncrease">A+</button>
            </div>
            <button class="theme-toggle" id="themeToggle">🌙</button>
        </div>
    </footer>
    
    <script>
        // Configuration
        const venues = [
            { id: 'stage-a', name: 'Stage A' },
            { id: 'stage-b', name: 'Stage B' },
            { id: 'stage-c', name: 'Stage C' },
            { id: 'workshop', name: 'Workshop' },
        ];
        
        // State
        let currentVenue = localStorage.getItem('venue') || 'stage-a';
        let ws = null;
        let fontSize = parseFloat(localStorage.getItem('fontSize')) || 1;
        let isDarkMode = localStorage.getItem('theme') !== 'light';
        let reconnectAttempts = 0;
        const maxCaptions = 50;
        
        // Elements
        const venueTabsEl = document.getElementById('venueTabs');
        const captionsEl = document.getElementById('captions');
        const emptyStateEl = document.getElementById('emptyState');
        const statusDotEl = document.getElementById('statusDot');
        const statusTextEl = document.getElementById('statusText');
        
        // Initialize venues
        function initVenues() {
            venueTabsEl.innerHTML = venues.map(v => `
                <button class="venue-tab ${v.id === currentVenue ? 'active' : ''}" 
                        data-venue="${v.id}">
                    ${v.name}
                </button>
            `).join('');
            
            venueTabsEl.addEventListener('click', (e) => {
                if (e.target.classList.contains('venue-tab')) {
                    selectVenue(e.target.dataset.venue);
                }
            });
        }
        
        function selectVenue(venueId) {
            if (venueId === currentVenue) return;
            
            currentVenue = venueId;
            localStorage.setItem('venue', venueId);
            
            // Update UI
            document.querySelectorAll('.venue-tab').forEach(tab => {
                tab.classList.toggle('active', tab.dataset.venue === venueId);
            });
            
            // Clear captions and reconnect
            clearCaptions();
            connect();
        }
        
        function clearCaptions() {
            captionsEl.innerHTML = '';
            captionsEl.appendChild(emptyStateEl);
            emptyStateEl.style.display = 'block';
        }
        
        function setStatus(status) {
            statusDotEl.className = 'status-dot ' + status;
            const statusMessages = {
                'connected': 'Live',
                'disconnected': 'Disconnected',
                'reconnecting': 'Reconnecting...',
            };
            statusTextEl.textContent = statusMessages[status] || status;
        }
        
        function connect() {
            if (ws) {
                ws.close();
            }
            
            setStatus('reconnecting');
            
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${protocol}//${window.location.host}/api/captions/stream/${currentVenue}`;
            
            ws = new WebSocket(wsUrl);
            
            ws.onopen = () => {
                setStatus('connected');
                reconnectAttempts = 0;
            };
            
            ws.onmessage = (event) => {
                try {
                    handleMessage(JSON.parse(event.data));
                } catch (e) {
                    console.error('Parse error:', e);
                }
            };
            
            ws.onclose = () => {
                setStatus('disconnected');
                scheduleReconnect();
            };
            
            ws.onerror = console.error;
        }
        
        function scheduleReconnect() {
            if (reconnectAttempts >= 10) return;
            reconnectAttempts++;
            const delay = Math.min(1000 * Math.pow(2, reconnectAttempts - 1), 30000);
            setTimeout(connect, delay);
        }
        
        function handleMessage(data) {
            if (data.type === 'keepalive' || data.type === 'connected') return;
            
            emptyStateEl.style.display = 'none';
            
            if (data.type === 'tentative') {
                updateTentative(data);
            } else if (data.type === 'committed') {
                commitSegment(data);
            }
        }
        
        function updateTentative(data) {
            let el = document.getElementById('tentative-segment');
            if (!el) {
                el = createCaptionElement(data, 'tentative');
                el.id = 'tentative-segment';
                captionsEl.appendChild(el);
            } else {
                el.querySelector('.caption-text').textContent = data.text;
            }
            scrollToBottom();
        }
        
        function commitSegment(data) {
            const tentative = document.getElementById('tentative-segment');
            if (tentative) tentative.remove();
            
            const el = createCaptionElement(data, 'committed');
            captionsEl.appendChild(el);
            
            trimCaptions();
            scrollToBottom();
        }
        
        function createCaptionElement(data, type) {
            const el = document.createElement('div');
            el.className = `caption-line ${type}`;
            
            const time = new Date().toLocaleTimeString([], { 
                hour: '2-digit', 
                minute: '2-digit' 
            });
            
            el.innerHTML = `
                ${data.speaker ? `<div class="speaker-name">${escapeHtml(data.speaker)}</div>` : ''}
                <div class="caption-text">${escapeHtml(data.text)}</div>
                <div class="caption-time">${time}</div>
            `;
            
            return el;
        }
        
        function trimCaptions() {
            const lines = captionsEl.querySelectorAll('.caption-line');
            while (lines.length > maxCaptions) {
                lines[0].remove();
            }
        }
        
        function scrollToBottom() {
            window.scrollTo(0, document.body.scrollHeight);
        }
        
        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }
        
        // Font size controls
        function updateFontSize(delta) {
            fontSize = Math.max(0.75, Math.min(2, fontSize + delta));
            localStorage.setItem('fontSize', fontSize);
            document.documentElement.style.setProperty('--caption-font-size', `${fontSize}rem`);
        }
        
        document.getElementById('fontIncrease').addEventListener('click', () => updateFontSize(0.125));
        document.getElementById('fontDecrease').addEventListener('click', () => updateFontSize(-0.125));
        
        // Theme toggle
        function toggleTheme() {
            isDarkMode = !isDarkMode;
            document.body.classList.toggle('light', !isDarkMode);
            localStorage.setItem('theme', isDarkMode ? 'dark' : 'light');
            document.getElementById('themeToggle').textContent = isDarkMode ? '🌙' : '☀️';
        }
        
        document.getElementById('themeToggle').addEventListener('click', toggleTheme);
        
        // Initialize
        initVenues();
        document.body.classList.toggle('light', !isDarkMode);
        document.documentElement.style.setProperty('--caption-font-size', `${fontSize}rem`);
        document.getElementById('themeToggle').textContent = isDarkMode ? '🌙' : '☀️';
        connect();
        
        // Handle visibility
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible' && (!ws || ws.readyState !== WebSocket.OPEN)) {
                reconnectAttempts = 0;
                connect();
            }
        });
    </script>
</body>
</html>
```

### 4.3 PWA Manifest

```json
// static/manifest.json
{
    "name": "EMF Captions",
    "short_name": "Captions",
    "description": "Live captioning for EMF Camp",
    "start_url": "/",
    "display": "standalone",
    "background_color": "#1a1a2e",
    "theme_color": "#1a1a2e",
    "icons": [
        {
            "src": "/static/icon-192.png",
            "sizes": "192x192",
            "type": "image/png"
        },
        {
            "src": "/static/icon-512.png",
            "sizes": "512x512",
            "type": "image/png"
        }
    ]
}
```

---

## 5. Raspberry Pi Setup

### 5.1 Audio Capture Client

```python
#!/usr/bin/env python3
"""
EMF Camptions - Raspberry Pi Audio Capture Client

Captures audio from USB microphone/audio interface and streams
to the central camptions server via WebSocket.

Audio format: 16kHz, 16-bit signed, mono (s16le)
"""

import asyncio
import argparse
import json
import signal
import sys
from datetime import datetime
from pathlib import Path

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
            if info['maxInputChannels'] > 0:
                print(f"  [{i}] {info['name']}")
                print(f"      Channels: {info['maxInputChannels']}, "
                      f"Rate: {int(info['defaultSampleRate'])}Hz")
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
        self.running = False
        
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
        
        if data.get('type') == 'session_started':
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
    parser = argparse.ArgumentParser(
        description="EMF Camptions Audio Capture Client"
    )
    parser.add_argument(
        '--server', '-s',
        default='ws://localhost:8000',
        help='Camptions server URL (default: ws://localhost:8000)'
    )
    parser.add_argument(
        '--venue', '-v',
        required=True,
        help='Venue ID (e.g., stage-a, stage-b)'
    )
    parser.add_argument(
        '--device', '-d',
        type=int,
        default=None,
        help='Audio input device index (default: system default)'
    )
    parser.add_argument(
        '--title', '-t',
        default=None,
        help='Session title (optional)'
    )
    parser.add_argument(
        '--list-devices', '-l',
        action='store_true',
        help='List available audio devices and exit'
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
    
    asyncio.run(run_capture(
        server_url=args.server,
        venue_id=args.venue,
        device_index=args.device,
        session_title=args.title,
    ))


if __name__ == '__main__':
    main()
```

### 5.2 Raspberry Pi Audio Capture Setup Script

```bash
#!/bin/bash
#
# EMF Camptions - Raspberry Pi Audio Capture Setup
#
# This script sets up a Raspberry Pi to capture audio and stream
# it to the central camptions server.
#
# Usage: sudo ./setup-audio-capture.sh
#

set -e

# Configuration
CAMPTIONS_USER="camptions"
CAMPTIONS_DIR="/opt/camptions"
CAMPTIONS_SERVER="${CAMPTIONS_SERVER:-ws://captions.emf.camp}"
CAMPTIONS_VENUE="${CAMPTIONS_VENUE:-stage-a}"

echo "========================================"
echo "EMF Camptions - Audio Capture Setup"
echo "========================================"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root: sudo $0"
    exit 1
fi

echo "[1/8] Updating system packages..."
apt-get update
apt-get upgrade -y

echo "[2/8] Installing dependencies..."
apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    python3-pyaudio \
    portaudio19-dev \
    alsa-utils \
    git

echo "[3/8] Creating camptions user..."
if ! id "$CAMPTIONS_USER" &>/dev/null; then
    useradd --system --create-home --shell /bin/bash "$CAMPTIONS_USER"
fi

# Add user to audio group
usermod -a -G audio "$CAMPTIONS_USER"

echo "[4/8] Setting up application directory..."
mkdir -p "$CAMPTIONS_DIR"
chown "$CAMPTIONS_USER:$CAMPTIONS_USER" "$CAMPTIONS_DIR"

echo "[5/8] Creating Python virtual environment..."
sudo -u "$CAMPTIONS_USER" python3 -m venv "$CAMPTIONS_DIR/venv"
sudo -u "$CAMPTIONS_USER" "$CAMPTIONS_DIR/venv/bin/pip" install --upgrade pip
sudo -u "$CAMPTIONS_USER" "$CAMPTIONS_DIR/venv/bin/pip" install \
    pyaudio \
    websockets

echo "[6/8] Installing capture client..."
# Copy the capture script (assuming it's in the same directory)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cat > "$CAMPTIONS_DIR/capture_client.py" << 'CAPTURE_SCRIPT'
# Insert the full capture_client.py content here during deployment
# For now, we'll create a placeholder that gets replaced
CAPTURE_SCRIPT

# If capture_client.py exists in script directory, copy it
if [ -f "$SCRIPT_DIR/capture_client.py" ]; then
    cp "$SCRIPT_DIR/capture_client.py" "$CAMPTIONS_DIR/capture_client.py"
fi

chown "$CAMPTIONS_USER:$CAMPTIONS_USER" "$CAMPTIONS_DIR/capture_client.py"
chmod +x "$CAMPTIONS_DIR/capture_client.py"

echo "[7/8] Creating systemd service..."
cat > /etc/systemd/system/camptions-capture.service << EOF
[Unit]
Description=EMF Camptions Audio Capture
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
User=$CAMPTIONS_USER
Group=$CAMPTIONS_USER
WorkingDirectory=$CAMPTIONS_DIR
Environment="PATH=$CAMPTIONS_DIR/venv/bin:/usr/bin"
ExecStart=$CAMPTIONS_DIR/venv/bin/python3 $CAMPTIONS_DIR/capture_client.py \\
    --server "$CAMPTIONS_SERVER" \\
    --venue "$CAMPTIONS_VENUE"
Restart=always
RestartSec=10

# Hardening
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=$CAMPTIONS_DIR

[Install]
WantedBy=multi-user.target
EOF

echo "[8/8] Creating configuration file..."
cat > "$CAMPTIONS_DIR/config.env" << EOF
# EMF Camptions Audio Capture Configuration
# Edit this file and restart the service to apply changes

# Server URL (WebSocket)
CAMPTIONS_SERVER=$CAMPTIONS_SERVER

# Venue ID
CAMPTIONS_VENUE=$CAMPTIONS_VENUE

# Audio device index (leave empty for default)
# Run 'camptions-list-devices' to see available devices
CAMPTIONS_DEVICE=
EOF

chown "$CAMPTIONS_USER:$CAMPTIONS_USER" "$CAMPTIONS_DIR/config.env"

# Create helper script for listing audio devices
cat > /usr/local/bin/camptions-list-devices << EOF
#!/bin/bash
sudo -u $CAMPTIONS_USER $CAMPTIONS_DIR/venv/bin/python3 $CAMPTIONS_DIR/capture_client.py --list-devices
EOF
chmod +x /usr/local/bin/camptions-list-devices

# Create helper script for testing
cat > /usr/local/bin/camptions-test << EOF
#!/bin/bash
source $CAMPTIONS_DIR/config.env
sudo -u $CAMPTIONS_USER $CAMPTIONS_DIR/venv/bin/python3 $CAMPTIONS_DIR/capture_client.py \\
    --server "\$CAMPTIONS_SERVER" \\
    --venue "\$CAMPTIONS_VENUE" \\
    \${CAMPTIONS_DEVICE:+--device "\$CAMPTIONS_DEVICE"}
EOF
chmod +x /usr/local/bin/camptions-test

# Reload systemd
systemctl daemon-reload

echo ""
echo "========================================"
echo "Setup complete!"
echo "========================================"
echo ""
echo "Next steps:"
echo ""
echo "1. Connect your USB audio device"
echo ""
echo "2. List available audio devices:"
echo "   camptions-list-devices"
echo ""
echo "3. Edit configuration:"
echo "   sudo nano $CAMPTIONS_DIR/config.env"
echo ""
echo "4. Test the capture (Ctrl+C to stop):"
echo "   camptions-test"
echo ""
echo "5. Enable and start the service:"
echo "   sudo systemctl enable camptions-capture"
echo "   sudo systemctl start camptions-capture"
echo ""
echo "6. Check service status:"
echo "   sudo systemctl status camptions-capture"
echo "   sudo journalctl -u camptions-capture -f"
echo ""
```

### 5.3 Raspberry Pi Display Screen Setup Script

```bash
#!/bin/bash
#
# EMF Camptions - Raspberry Pi Display Setup
#
# This script sets up a Raspberry Pi to display captions on a connected
# screen in kiosk mode using Chromium.
#
# Usage: sudo ./setup-display.sh
#

set -e

# Configuration
CAMPTIONS_URL="${CAMPTIONS_URL:-https://captions.emf.camp/display}"
CAMPTIONS_VENUE="${CAMPTIONS_VENUE:-stage-a}"
DISPLAY_USER="camptions"
DISPLAY_ROTATION="${DISPLAY_ROTATION:-normal}"  # normal, left, right, inverted

echo "========================================"
echo "EMF Camptions - Display Setup"
echo "========================================"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root: sudo $0"
    exit 1
fi

echo "[1/9] Updating system packages..."
apt-get update
apt-get upgrade -y

echo "[2/9] Installing display dependencies..."
apt-get install -y \
    chromium-browser \
    xserver-xorg \
    x11-xserver-utils \
    xinit \
    openbox \
    unclutter \
    lightdm

echo "[3/9] Creating display user..."
if ! id "$DISPLAY_USER" &>/dev/null; then
    useradd --create-home --shell /bin/bash "$DISPLAY_USER"
fi

# Add user to required groups
usermod -a -G video,audio,input,tty "$DISPLAY_USER"

echo "[4/9] Configuring autologin..."
mkdir -p /etc/lightdm/lightdm.conf.d

cat > /etc/lightdm/lightdm.conf.d/autologin.conf << EOF
[Seat:*]
autologin-user=$DISPLAY_USER
autologin-user-timeout=0
user-session=openbox
EOF

echo "[5/9] Configuring display rotation..."
# Set rotation in xorg config
if [ "$DISPLAY_ROTATION" != "normal" ]; then
    mkdir -p /etc/X11/xorg.conf.d
    
    case "$DISPLAY_ROTATION" in
        left)
            ROTATION_OPTION="left"
            ;;
        right)
            ROTATION_OPTION="right"
            ;;
        inverted)
            ROTATION_OPTION="inverted"
            ;;
        *)
            ROTATION_OPTION="normal"
            ;;
    esac
    
    cat > /etc/X11/xorg.conf.d/10-monitor.conf << EOF
Section "Monitor"
    Identifier "HDMI-1"
    Option "Rotate" "$ROTATION_OPTION"
EndSection
EOF
fi

echo "[6/9] Creating kiosk startup script..."
mkdir -p "/home/$DISPLAY_USER/.config/openbox"

cat > "/home/$DISPLAY_USER/.config/openbox/autostart" << EOF
#!/bin/bash

# Disable screen blanking and power management
xset s off
xset s noblank
xset -dpms

# Hide mouse cursor after 0.5 seconds of inactivity
unclutter -idle 0.5 -root &

# Wait for network
sleep 5

# Build the caption display URL
CAPTION_URL="$CAMPTIONS_URL?venue=$CAMPTIONS_VENUE&mode=dark"

# Start Chromium in kiosk mode
chromium-browser \\
    --kiosk \\
    --noerrdialogs \\
    --disable-infobars \\
    --disable-session-crashed-bubble \\
    --disable-restore-session-state \\
    --no-first-run \\
    --start-fullscreen \\
    --autoplay-policy=no-user-gesture-required \\
    --disable-features=TranslateUI \\
    --check-for-update-interval=31536000 \\
    --disable-background-networking \\
    --disable-component-update \\
    --disable-default-apps \\
    --disable-extensions \\
    --disable-sync \\
    --incognito \\
    "\$CAPTION_URL" &
EOF

chown -R "$DISPLAY_USER:$DISPLAY_USER" "/home/$DISPLAY_USER/.config"
chmod +x "/home/$DISPLAY_USER/.config/openbox/autostart"

echo "[7/9] Creating configuration file..."
cat > "/home/$DISPLAY_USER/camptions-display.conf" << EOF
# EMF Camptions Display Configuration
# Edit this file and reboot to apply changes

# Caption server URL
CAMPTIONS_URL=$CAMPTIONS_URL

# Venue ID
CAMPTIONS_VENUE=$CAMPTIONS_VENUE

# Display rotation: normal, left, right, inverted
DISPLAY_ROTATION=$DISPLAY_ROTATION

# Display mode: dark, light, high-contrast
DISPLAY_MODE=dark

# Font size (CSS value, e.g., 4vw, 48px)
FONT_SIZE=4vw

# Maximum lines to display
MAX_LINES=8
EOF

chown "$DISPLAY_USER:$DISPLAY_USER" "/home/$DISPLAY_USER/camptions-display.conf"

echo "[8/9] Creating management scripts..."

# Script to reload display
cat > /usr/local/bin/camptions-display-reload << 'EOF'
#!/bin/bash
# Reload the caption display by restarting Chromium
pkill -f chromium
sleep 2
sudo -u camptions openbox --replace &
EOF
chmod +x /usr/local/bin/camptions-display-reload

# Script to show display status
cat > /usr/local/bin/camptions-display-status << 'EOF'
#!/bin/bash
echo "Camptions Display Status"
echo "========================"
echo ""
echo "Chromium process:"
pgrep -a chromium || echo "  Not running"
echo ""
echo "Display:"
DISPLAY=:0 xrandr 2>/dev/null | head -5 || echo "  Cannot query display"
echo ""
echo "Configuration:"
cat /home/camptions/camptions-display.conf
EOF
chmod +x /usr/local/bin/camptions-display-status

# Script to set venue
cat > /usr/local/bin/camptions-set-venue << 'EOF'
#!/bin/bash
if [ -z "$1" ]; then
    echo "Usage: camptions-set-venue <venue-id>"
    echo "Example: camptions-set-venue stage-b"
    exit 1
fi
sed -i "s/^CAMPTIONS_VENUE=.*/CAMPTIONS_VENUE=$1/" /home/camptions/camptions-display.conf
echo "Venue set to: $1"
echo "Reloading display..."
camptions-display-reload
EOF
chmod +x /usr/local/bin/camptions-set-venue

echo "[9/9] Configuring boot options..."
# Disable splash screen for faster boot
if ! grep -q "consoleblank=0" /boot/cmdline.txt 2>/dev/null; then
    sed -i 's/$/ consoleblank=0/' /boot/cmdline.txt 2>/dev/null || true
fi

# Disable overscan (better display utilization)
if [ -f /boot/config.txt ]; then
    if ! grep -q "disable_overscan=1" /boot/config.txt; then
        echo "disable_overscan=1" >> /boot/config.txt
    fi
fi

echo ""
echo "========================================"
echo "Setup complete!"
echo "========================================"
echo ""
echo "Configuration file:"
echo "  /home/$DISPLAY_USER/camptions-display.conf"
echo ""
echo "Management commands:"
echo "  camptions-display-status  - Show current status"
echo "  camptions-display-reload  - Reload the display"
echo "  camptions-set-venue <id>  - Change venue and reload"
echo ""
echo "The display will start automatically on next boot."
echo ""
echo "To test now, reboot the Pi:"
echo "  sudo reboot"
echo ""
```

### 5.4 Combined Raspberry Pi Setup (Audio + Display)

```bash
#!/bin/bash
#
# EMF Camptions - Full Raspberry Pi Setup
#
# This script sets up a Raspberry Pi for BOTH audio capture AND display.
# Useful for a self-contained stage setup.
#
# Usage: sudo ./setup-full.sh
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "========================================"
echo "EMF Camptions - Full Pi Setup"
echo "========================================"
echo ""
echo "This will set up both audio capture and display."
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root: sudo $0"
    exit 1
fi

# Get configuration
read -p "Enter venue ID (e.g., stage-a): " VENUE_ID
read -p "Enter server URL [https://captions.emf.camp]: " SERVER_URL
SERVER_URL="${SERVER_URL:-https://captions.emf.camp}"

export CAMPTIONS_VENUE="$VENUE_ID"
export CAMPTIONS_SERVER="${SERVER_URL/https:/ws:}"
export CAMPTIONS_URL="$SERVER_URL/display"

echo ""
echo "Configuration:"
echo "  Venue: $CAMPTIONS_VENUE"
echo "  Server: $CAMPTIONS_SERVER"
echo "  Display URL: $CAMPTIONS_URL"
echo ""
read -p "Continue? [y/N] " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    exit 1
fi

# Run both setup scripts
echo ""
echo "Setting up audio capture..."
echo "========================================"
"$SCRIPT_DIR/setup-audio-capture.sh"

echo ""
echo "Setting up display..."
echo "========================================"
"$SCRIPT_DIR/setup-display.sh"

echo ""
echo "========================================"
echo "Full setup complete!"
echo "========================================"
echo ""
echo "Both audio capture and display are configured."
echo ""
echo "On reboot:"
echo "  - Audio will be captured and sent to the server"
echo "  - Display will show captions from the server"
echo ""
echo "To start now:"
echo "  sudo reboot"
echo ""
```

---

## 6. Deployment Configuration

### 6.1 Docker Compose

```yaml
# docker-compose.yml
version: '3.8'

services:
  camptions:
    build: .
    ports:
      - "8000:8000"
    environment:
      - CAMPTIONS_HOST=0.0.0.0
      - CAMPTIONS_PORT=8000
      - CAMPTIONS_DATABASE_URL=sqlite+aiosqlite:///./data/camptions.db
      - CAMPTIONS_WHISPER_MODEL=medium
      - CAMPTIONS_WHISPER_LANGUAGE=en
      - CAMPTIONS_WHISPER_BACKEND=auto
    volumes:
      - ./data:/app/data
      - whisper-cache:/root/.cache/huggingface
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/venues"]
      interval: 30s
      timeout: 10s
      retries: 3

  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./certs:/etc/nginx/certs:ro
    depends_on:
      - camptions
    restart: unless-stopped

volumes:
  whisper-cache:
```

### 6.2 Dockerfile

```dockerfile
# Dockerfile
FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsndfile1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[gpu]"

# Copy application code
COPY src/ src/
COPY static/ static/
COPY alembic/ alembic/
COPY alembic.ini .

# Create data directory
RUN mkdir -p /app/data

# Expose port
EXPOSE 8000

# Run the application
CMD ["uvicorn", "camptions.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 6.3 Nginx Configuration

```nginx
# nginx.conf
events {
    worker_connections 1024;
}

http {
    upstream camptions {
        server camptions:8000;
    }

    # Redirect HTTP to HTTPS
    server {
        listen 80;
        server_name captions.emf.camp;
        return 301 https://$server_name$request_uri;
    }

    # Main server
    server {
        listen 443 ssl http2;
        server_name captions.emf.camp;

        ssl_certificate /etc/nginx/certs/fullchain.pem;
        ssl_certificate_key /etc/nginx/certs/privkey.pem;

        # SSL configuration
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_prefer_server_ciphers on;
        ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256;

        # WebSocket support
        location /api/audio/ {
            proxy_pass http://camptions;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_read_timeout 86400;
        }

        location /api/captions/stream/ {
            proxy_pass http://camptions;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_read_timeout 86400;
        }

        # API and static files
        location / {
            proxy_pass http://camptions;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }

        # Static file caching
        location /static/ {
            proxy_pass http://camptions;
            proxy_cache_valid 200 1h;
            add_header Cache-Control "public, max-age=3600";
        }
    }
}
```

---

## 7. File Structure

```
emf-camptions-v2/
├── README.md
├── LICENSE
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
├── nginx.conf
├── alembic.ini
├── .env.example
├── .gitignore
│
├── alembic/
│   ├── env.py
│   └── versions/
│       └── 001_initial.py
│
├── src/
│   └── camptions/
│       ├── __init__.py
│       ├── main.py
│       ├── config.py
│       ├── models.py
│       ├── schemas.py
│       ├── database.py
│       │
│       ├── routers/
│       │   ├── __init__.py
│       │   ├── audio.py
│       │   ├── captions.py
│       │   ├── venues.py
│       │   └── admin.py
│       │
│       ├── services/
│       │   ├── __init__.py
│       │   ├── transcription.py
│       │   ├── distribution.py
│       │   └── storage.py
│       │
│       └── core/
│           ├── __init__.py
│           ├── events.py
│           └── middleware.py
│
├── static/
│   ├── display.html
│   ├── viewer.html
│   ├── admin.html
│   ├── manifest.json
│   ├── icon-192.png
│   ├── icon-512.png
│   │
│   ├── css/
│   │   └── captions.css
│   │
│   └── js/
│       ├── caption-client.js
│       └── admin.js
│
├── raspberry-pi/
│   ├── capture_client.py
│   ├── setup-audio-capture.sh
│   ├── setup-display.sh
│   └── setup-full.sh
│
└── tests/
    ├── conftest.py
    ├── test_transcription.py
    ├── test_distribution.py
    └── test_api.py
```

---

## 8. Implementation Phases

### Phase 1: Core Backend (Week 1)
- [ ] Project scaffolding with pyproject.toml
- [ ] FastAPI application structure
- [ ] Database models and migrations
- [ ] WhisperLiveKit integration
- [ ] Basic audio ingestion endpoint
- [ ] Caption distribution service

### Phase 2: Frontend (Week 1-2)
- [ ] Display page for large screens
- [ ] Viewer page for user devices
- [ ] WebSocket client implementation
- [ ] Reconnection handling
- [ ] Basic styling and theming

### Phase 3: Raspberry Pi (Week 2)
- [ ] Audio capture client
- [ ] Setup scripts for audio capture
- [ ] Setup scripts for display
- [ ] Combined setup script
- [ ] Testing on real hardware

### Phase 4: Deployment (Week 2-3)
- [ ] Docker configuration
- [ ] Nginx reverse proxy
- [ ] SSL certificate setup
- [ ] Production deployment guide
- [ ] Monitoring and logging

### Phase 5: Polish (Week 3)
- [ ] Admin interface
- [ ] Session management
- [ ] Historical caption lookup
- [ ] Performance optimization
- [ ] Documentation

---

## Appendix A: API Reference

### Audio Ingestion

```
WebSocket /api/audio/ingest/{venue_id}?session_title={title}

Client → Server: Raw PCM audio (16kHz, 16-bit signed, mono)
Server → Client: JSON messages
  - { "type": "session_started", "session_id": "...", "venue_id": "..." }
```

### Caption Streaming

```
WebSocket /api/captions/stream/{venue_id}

Server → Client: JSON messages
  - { "type": "connected", "venue_id": "...", "timestamp": "..." }
  - { "type": "tentative", "id": "...", "text": "...", "speaker": "...", ... }
  - { "type": "committed", "id": "...", "text": "...", "speaker": "...", ... }
  - { "type": "session_end", "session_id": "...", "timestamp": "..." }
  - { "type": "keepalive" }
```

### REST Endpoints

```
GET /api/venues
  List all venues

GET /api/venues/{venue_id}
  Get venue details

GET /api/captions/history/{venue_id}?limit=100&since={datetime}
  Get historical captions

POST /api/admin/sessions/{venue_id}/start
  Manually start a session

POST /api/admin/sessions/{venue_id}/stop
  Manually stop a session
```

---

## Appendix B: Troubleshooting

### Audio Capture Issues

1. **No audio devices found**
   ```bash
   # Check ALSA devices
   arecord -l
   
   # Check PyAudio devices
   camptions-list-devices
   ```

2. **Permission denied on audio device**
   ```bash
   # Add user to audio group
   sudo usermod -a -G audio camptions
   
   # Logout and login again
   ```

3. **Audio quality issues**
   ```bash
   # Test recording
   arecord -D plughw:1,0 -f S16_LE -r 16000 -c 1 test.wav
   aplay test.wav
   ```

### Display Issues

1. **Black screen after boot**
   ```bash
   # Check X server logs
   cat /var/log/Xorg.0.log
   
   # Check lightdm status
   systemctl status lightdm
   ```

2. **Wrong resolution**
   ```bash
   # Force resolution in config.txt
   echo "hdmi_group=2" >> /boot/config.txt
   echo "hdmi_mode=82" >> /boot/config.txt  # 1920x1080@60Hz
   ```

3. **Display rotation not working**
   ```bash
   # Check xrandr
   DISPLAY=:0 xrandr
   
   # Manual rotation
   DISPLAY=:0 xrandr --output HDMI-1 --rotate left
   ```

### Network Issues

1. **WebSocket connection failing**
   ```bash
   # Test connectivity
   curl -v http://server:8000/api/venues
   
   # Test WebSocket
   websocat ws://server:8000/api/captions/stream/stage-a
   ```

2. **Frequent disconnections**
   - Check network stability
   - Increase WebSocket timeout in nginx
   - Check server logs for errors
