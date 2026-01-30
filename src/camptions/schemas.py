"""Pydantic schemas for request/response validation."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


class VenueBase(BaseModel):
    """Base schema for venue data."""

    name: str
    description: Optional[str] = None


class VenueCreate(VenueBase):
    """Schema for creating a venue."""

    id: str


class VenueResponse(VenueBase):
    """Schema for venue response."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    is_active: bool
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
    speaker: Optional[str] = None
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


class SegmentBroadcast(BaseModel):
    """Schema for segment broadcast messages."""

    id: str
    session_id: str
    venue_id: str
    sequence: int
    type: Literal["tentative", "committed", "final"]
    text: str
    speaker: Optional[str] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    timestamp: str


class CaptionHistoryResponse(BaseModel):
    """Schema for caption history response."""

    venue_id: str
    count: int
    segments: list[SegmentResponse]


class WebSocketMessage(BaseModel):
    """Base schema for WebSocket messages."""

    type: str


class SessionStartedMessage(WebSocketMessage):
    """Message sent when a session starts."""

    type: Literal["session_started"] = "session_started"
    session_id: str
    venue_id: str


class SessionEndMessage(WebSocketMessage):
    """Message sent when a session ends."""

    type: Literal["session_end"] = "session_end"
    session_id: str
    timestamp: str


class ConnectedMessage(WebSocketMessage):
    """Message sent when a client connects."""

    type: Literal["connected"] = "connected"
    venue_id: str
    timestamp: str


class KeepaliveMessage(WebSocketMessage):
    """Keepalive message."""

    type: Literal["keepalive"] = "keepalive"
