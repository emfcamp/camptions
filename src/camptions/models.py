"""SQLAlchemy database models."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    """Base class for all models."""

    pass


class Venue(Base):
    """Represents a physical venue/stage where captions are captured."""

    __tablename__ = "venues"

    id = Column(String(50), primary_key=True)
    name = Column(String(100), nullable=False)
    description = Column(Text)
    is_active = Column(Integer, default=1)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))

    sessions = relationship("Session", back_populates="venue")


class Session(Base):
    """Represents a transcription session for a venue."""

    __tablename__ = "sessions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    venue_id = Column(String(50), ForeignKey("venues.id"), nullable=False)
    title = Column(String(200))
    started_at = Column(DateTime, default=lambda: datetime.now(UTC))
    ended_at = Column(DateTime)

    venue = relationship("Venue", back_populates="sessions")
    segments = relationship("Segment", back_populates="session")


class Segment(Base):
    """Represents a single caption segment."""

    __tablename__ = "segments"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String(36), ForeignKey("sessions.id"), nullable=False)
    sequence = Column(Integer, nullable=False)
    segment_type = Column(String(20), nullable=False)  # tentative, committed, final
    text = Column(Text, nullable=False)
    start_time = Column(Float, nullable=False)
    end_time = Column(Float)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))

    session = relationship("Session", back_populates="segments")

    __table_args__ = (
        Index("idx_session_sequence", "session_id", "sequence"),
        Index("idx_session_type", "session_id", "segment_type"),
    )
