"""
SQLAlchemy database models for Family Calendar.

This module defines all database tables and relationships for persistent
event storage while maintaining backward compatibility with the ICS feed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, declarative_base, relationship

# Initialize SQLAlchemy (to be configured in Flask app)
db = SQLAlchemy()
Base = declarative_base()


class FamilyMember(db.Model):
    """Family member who owns calendar events."""

    __tablename__ = "family_members"

    id: Mapped[str] = db.Column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    
    # Human-readable name
    name: Mapped[str] = db.Column(String(255), nullable=False, unique=True)
    
    # URL-safe member ID (used in feed URL: /reuben/calendar.ics)
    member_id: Mapped[str] = db.Column(String(255), nullable=False, unique=True)
    
    # Hex color for UI display
    color: Mapped[str] = db.Column(String(7), nullable=False)  # e.g., "#0078d4"
    
    # Relationships
    subscriptions: Mapped[list[MemberCalendarSubscription]] = relationship(
        "MemberCalendarSubscription",
        back_populates="member",
        cascade="all, delete-orphan",
    )
    
    created_at: Mapped[datetime] = db.Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = db.Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "name": self.name,
            "member_id": self.member_id,
            "color": self.color,
            "created_at": self.created_at.isoformat(),
            "calendar_count": len(self.subscriptions),
        }

    def __repr__(self) -> str:
        return f"<FamilyMember {self.name} ({self.member_id})>"


class CalendarSource(db.Model):
    """WebCal or other calendar feed source."""

    __tablename__ = "calendar_sources"

    id: Mapped[str] = db.Column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    
    # User-friendly name
    name: Mapped[str] = db.Column(String(255), nullable=False)
    
    # Feed URL (unique to prevent duplicate sources)
    feed_url: Mapped[str] = db.Column(Text, nullable=False, unique=True)
    
    # Type: "ics" for standard ICS feeds, "caldav" for CalDAV
    source_type: Mapped[str] = db.Column(String(20), nullable=False, default="ics")
    
    # Privacy settings (can be overridden per member)
    show_details: Mapped[bool] = db.Column(Boolean, nullable=False, default=True)
    busy_text: Mapped[str] = db.Column(String(255), nullable=False, default="Busy")
    show_location: Mapped[bool] = db.Column(Boolean, nullable=False, default=False)
    
    # CalDAV credentials (encrypted in production)
    caldav_username: Mapped[Optional[str]] = db.Column(String(255))
    caldav_password: Mapped[Optional[str]] = db.Column(String(255))
    
    # Sync tracking
    last_sync_at: Mapped[Optional[datetime]] = db.Column(DateTime(timezone=True))
    last_sync_status: Mapped[str] = db.Column(
        String(20),
        nullable=False,
        default="pending",
    )  # pending, success, failed, partial
    last_sync_error: Mapped[Optional[str]] = db.Column(Text)
    
    # Relationships
    events: Mapped[list[Event]] = relationship(
        "Event",
        back_populates="source",
        cascade="all, delete-orphan",
    )
    subscriptions: Mapped[list[MemberCalendarSubscription]] = relationship(
        "MemberCalendarSubscription",
        back_populates="source",
        cascade="all, delete-orphan",
    )
    sync_logs: Mapped[list[SyncLog]] = relationship(
        "SyncLog",
        back_populates="source",
        cascade="all, delete-orphan",
    )
    
    created_at: Mapped[datetime] = db.Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = db.Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "name": self.name,
            "feed_url": self.feed_url,
            "source_type": self.source_type,
            "show_details": self.show_details,
            "show_location": self.show_location,
            "last_sync_at": self.last_sync_at.isoformat() if self.last_sync_at else None,
            "last_sync_status": self.last_sync_status,
            "event_count": len(self.events),
        }

    def __repr__(self) -> str:
        return f"<CalendarSource {self.name} ({self.feed_url[:50]})>"


class MemberCalendarSubscription(db.Model):
    """Member subscription to calendar source.
    
    Allows each member to customize privacy settings per source.
    """

    __tablename__ = "member_calendar_subscriptions"

    id: Mapped[str] = db.Column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    
    member_id: Mapped[str] = db.Column(
        UUID(as_uuid=False),
        ForeignKey("family_members.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_id: Mapped[str] = db.Column(
        UUID(as_uuid=False),
        ForeignKey("calendar_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    
    # Member-specific privacy overrides (None = use source default)
    show_details: Mapped[Optional[bool]] = db.Column(Boolean)
    busy_text: Mapped[Optional[str]] = db.Column(String(255))
    show_location: Mapped[Optional[bool]] = db.Column(Boolean)
    
    subscribed_at: Mapped[datetime] = db.Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    
    # Relationships
    member: Mapped[FamilyMember] = relationship(
        "FamilyMember",
        back_populates="subscriptions",
    )
    source: Mapped[CalendarSource] = relationship(
        "CalendarSource",
        back_populates="subscriptions",
    )
    
    __table_args__ = (
        UniqueConstraint("member_id", "source_id", name="uk_member_source"),
    )

    def get_effective_show_details(self) -> bool:
        """Get privacy setting: member override or source default."""
        return self.show_details if self.show_details is not None else self.source.show_details

    def get_effective_busy_text(self) -> str:
        """Get busy text: member override or source default."""
        return self.busy_text or self.source.busy_text or "Busy"

    def get_effective_show_location(self) -> bool:
        """Get location setting: member override or source default."""
        return self.show_location if self.show_location is not None else self.source.show_location

    def __repr__(self) -> str:
        return f"<Subscription {self.member.name} → {self.source.name}>"


class Event(db.Model):
    """Calendar event (normalized from ICS)."""

    __tablename__ = "events"

    id: Mapped[str] = db.Column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    
    # Foreign key to source
    source_id: Mapped[str] = db.Column(
        UUID(as_uuid=False),
        ForeignKey("calendar_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    
    # Deduplication keys
    # - external_event_id: UID from ICS (if present)
    # - external_event_hash: SHA256(summary+start+end+location) for UID-less events
    external_event_id: Mapped[Optional[str]] = db.Column(String(500))
    external_event_hash: Mapped[Optional[str]] = db.Column(String(64))
    
    # Event data
    title: Mapped[str] = db.Column(String(255), nullable=False)
    description: Mapped[Optional[str]] = db.Column(Text)
    location: Mapped[Optional[str]] = db.Column(String(500))
    
    # Timing
    start_time: Mapped[datetime] = db.Column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[Optional[datetime]] = db.Column(DateTime(timezone=True))
    all_day: Mapped[bool] = db.Column(Boolean, nullable=False, default=False)
    
    # ICS properties
    ics_uid: Mapped[Optional[str]] = db.Column(String(500))
    ics_transp: Mapped[str] = db.Column(String(20), nullable=False, default="OPAQUE")  # OPAQUE, TRANSPARENT
    ics_status: Mapped[str] = db.Column(String(20), nullable=False, default="CONFIRMED")  # CONFIRMED, TENTATIVE, CANCELLED
    
    # Store raw VEVENT for recovery/audit
    ics_raw: Mapped[Optional[bytes]] = db.Column(LargeBinary)
    
    # Sync tracking
    last_modified_in_source: Mapped[Optional[datetime]] = db.Column(DateTime(timezone=True))
    synced_at: Mapped[datetime] = db.Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    
    created_at: Mapped[datetime] = db.Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = db.Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    
    # Relationships
    source: Mapped[CalendarSource] = relationship("CalendarSource", back_populates="events")
    
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "external_event_id",
            name="uk_source_external_id",
        ),
        UniqueConstraint(
            "source_id",
            "external_event_hash",
            name="uk_source_external_hash",
        ),
        Index("idx_events_source_start_time", "source_id", "start_time"),
        Index("idx_events_start_time", "start_time"),
        Index("idx_events_end_time", "end_time"),
    )

    def to_dict(self, apply_privacy: bool = False, busy_text: str = "Busy", show_location: bool = False) -> dict:
        """Convert to dictionary for API/frontend responses.
        
        Args:
            apply_privacy: If True, hide details (show only busy_text for title)
            busy_text: Text to show when privacy is applied
            show_location: Whether to include location in response
        """
        result = {
            "id": self.id,
            "summary": busy_text if apply_privacy else self.title,
            "description": "" if apply_privacy else (self.description or ""),
            "location": (self.location if show_location else "") or "",
            "start": self.start_time.isoformat(),
            "end": self.end_time.isoformat() if self.end_time else None,
            "all_day": self.all_day,
            "availability": "tentative" if self.ics_status == "TENTATIVE" else ("cancelled" if self.ics_status == "CANCELLED" else "busy"),
        }
        return result

    def __repr__(self) -> str:
        return f"<Event {self.title} ({self.start_time.date()})>"


class SyncLog(db.Model):
    """Record of each sync operation for debugging and monitoring."""

    __tablename__ = "sync_logs"

    id: Mapped[str] = db.Column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    
    source_id: Mapped[str] = db.Column(
        UUID(as_uuid=False),
        ForeignKey("calendar_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    
    sync_started_at: Mapped[datetime] = db.Column(DateTime(timezone=True), nullable=False)
    sync_completed_at: Mapped[Optional[datetime]] = db.Column(DateTime(timezone=True))
    
    status: Mapped[str] = db.Column(String(20), nullable=False, default="in_progress")
    
    # Statistics
    http_status_code: Mapped[Optional[int]] = db.Column(Integer)
    events_found: Mapped[int] = db.Column(Integer, nullable=False, default=0)
    events_imported: Mapped[int] = db.Column(Integer, nullable=False, default=0)
    events_updated: Mapped[int] = db.Column(Integer, nullable=False, default=0)
    events_deleted: Mapped[int] = db.Column(Integer, nullable=False, default=0)
    duplicates_skipped: Mapped[int] = db.Column(Integer, nullable=False, default=0)
    parse_errors: Mapped[int] = db.Column(Integer, nullable=False, default=0)
    
    error_message: Mapped[Optional[str]] = db.Column(Text)
    error_details: Mapped[Optional[str]] = db.Column(Text)  # JSON array of errors
    
    # Debug
    fetched_bytes: Mapped[Optional[int]] = db.Column(Integer)
    duration_ms: Mapped[Optional[int]] = db.Column(Integer)
    
    # Relationships
    source: Mapped[CalendarSource] = relationship("CalendarSource", back_populates="sync_logs")
    
    created_at: Mapped[datetime] = db.Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    
    __table_args__ = (
        Index("idx_sync_logs_source_started", "source_id", "sync_started_at"),
    )

    def complete(self, status: str, duration_ms: int) -> None:
        """Mark sync as complete."""
        self.sync_completed_at = datetime.now(timezone.utc)
        self.status = status
        self.duration_ms = duration_ms

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "source_id": self.source_id,
            "sync_started_at": self.sync_started_at.isoformat(),
            "sync_completed_at": self.sync_completed_at.isoformat() if self.sync_completed_at else None,
            "status": self.status,
            "events_found": self.events_found,
            "events_imported": self.events_imported,
            "events_updated": self.events_updated,
            "duplicates_skipped": self.duplicates_skipped,
            "parse_errors": self.parse_errors,
            "error_message": self.error_message,
            "duration_ms": self.duration_ms,
        }

    def __repr__(self) -> str:
        return f"<SyncLog {self.source.name} {self.status} ({self.created_at})>"
