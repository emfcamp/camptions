"""Pydantic schemas for request/response validation."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class VenueBase(BaseModel):
    """Base schema for venue data."""

    name: str
    description: Optional[str] = None
    stream_url: Optional[str] = None


class VenueCreate(VenueBase):
    """Schema for creating a venue."""

    id: str


class VenueResponse(VenueBase):
    """Schema for venue response."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    is_active: bool
    transcription_enabled: bool
    created_at: datetime


class SessionBase(BaseModel):
    """Base schema for session data."""

    title: Optional[str] = None


class SessionCreate(SessionBase):
    """Schema for creating a session."""

    venue_id: str


class SessionResponse(SessionBase):
    """Schema for session response."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    venue_id: str
    started_at: datetime
    ended_at: Optional[datetime] = None


class SegmentBase(BaseModel):
    """Base schema for segment data."""

    text: str
    start_time: float
    end_time: Optional[float] = None


class SegmentCreate(SegmentBase):
    """Schema for creating a segment."""

    session_id: str
    sequence: int
    segment_type: Literal["tentative", "committed", "final"]


class SegmentResponse(SegmentBase):
    """Schema for segment response."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    sequence: int
    segment_type: str
    created_at: datetime


class CaptionHistoryResponse(BaseModel):
    """Schema for caption history response."""

    venue_id: str
    count: int
    segments: list[SegmentResponse]


# ── Public API response models ────────────────────────────────────────────────
# These shapes are part of the documented public contract — keep them stable.
# Internal admin responses can keep using ad-hoc dicts.


class PublicSegment(BaseModel):
    """A finalised caption segment for a venue.

    Segments are uniquely identified by `(session_id, sequence)` — `sequence`
    is monotonic within a session but resets when a new session starts (e.g.
    after a Pi reconnect or admin pause).
    """

    id: str = Field(..., description="Globally unique segment UUID.")
    session_id: str = Field(..., description="Transcription session this segment belongs to.")
    venue_id: str = Field(..., description="Venue (stage) this segment was captured at.")
    sequence: int = Field(..., description="Per-session monotonic counter, starting at 1.")
    text: str = Field(..., description="Transcribed text (post-trim, no trailing whitespace).")
    start_time: Optional[float] = Field(
        None,
        description="Seconds from the start of the session to the start of this segment's audio.",
    )
    end_time: Optional[float] = Field(
        None, description="Seconds from session start to the end of this segment's audio."
    )
    created_at: Optional[datetime] = Field(
        None, description="Server wall-clock time the segment was finalised."
    )


class PublicSegmentsResponse(BaseModel):
    """A page of caption segments."""

    venue_id: str
    count: int = Field(..., description="Number of segments in this response.")
    segments: list[PublicSegment]
    next_cursor: Optional[str] = Field(
        None,
        description=(
            "Opaque cursor for the next page. Absent when there are no more "
            "segments matching the query."
        ),
    )


class PublicSession(BaseModel):
    """A transcription session — usually one talk's worth of audio."""

    id: str = Field(..., description="Session UUID.")
    venue_id: str
    title: Optional[str] = Field(
        None, description="Optional human-readable label (often the talk title)."
    )
    started_at: Optional[datetime] = Field(None, description="When the Pi audio first connected.")
    ended_at: Optional[datetime] = Field(
        None,
        description="When the Pi audio disconnected. Absent while the session is active.",
    )


class PublicSessionsResponse(BaseModel):
    count: int
    sessions: list[PublicSession]
    next_cursor: Optional[str] = None


class ScheduleSlot(BaseModel):
    """A talk slot from the EMF Camp schedule."""

    title: str = ""
    speaker: str = ""
    start_time: str = ""
    end_time: str = ""
    description: str = ""
    link: str = ""


class NowAndNext(BaseModel):
    """Current and upcoming talk for a venue."""

    venue_id: str
    now: Optional[ScheduleSlot] = None
    next: Optional[ScheduleSlot] = None


class PublicVenueStatus(VenueResponse):
    """Venue metadata enriched with current live status and runtime metrics."""

    is_live: bool = Field(
        False,
        description=(
            "True when both Pi audio is streaming and WhisperLive is handshaked "
            "for this venue."
        ),
    )
    subscriber_count: int = Field(
        0,
        description="Number of caption viewers currently connected to this venue.",
    )
    audio_drops: int = Field(
        0,
        description=(
            "Audio chunks dropped from the ingest queue this session "
            "(non-zero indicates the transcription pipeline is falling behind)."
        ),
    )
    distribution_drops: int = Field(
        0,
        description=(
            "Caption messages that could not be delivered to subscribers since "
            "the server started (subscriber queue full or WS send failure)."
        ),
    )
