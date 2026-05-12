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
