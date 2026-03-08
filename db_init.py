"""
Database initialization and configuration for Family Calendar.

Provides setup, migrations, and helper functions for database operations.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from flask import Flask
from sqlalchemy import Engine, event, text
from sqlalchemy.pool import Pool

from db_models import (
    CalendarSource,
    Event,
    FamilyMember,
    MemberCalendarSubscription,
    SyncLog,
    db,
)

logger = logging.getLogger(__name__)


def configure_database(app: Flask, database_url: Optional[str] = None) -> None:
    """Configure Flask app with database.
    
    Args:
        app: Flask application
        database_url: SQLAlchemy database URL
                     If None, uses SQLALCHEMY_DATABASE_URL env var
                     If env var not set, uses SQLite in-memory for development
    """
    if database_url is None:
        database_url = os.environ.get(
            "SQLALCHEMY_DATABASE_URL",
            "sqlite:///:memory:",  # Development default
        )
    
    logger.info(f"Configuring database: {_mask_url(database_url)}")
    
    # SQLAlchemy configuration
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_size": 5,
        "pool_recycle": 3600,  # Recycle connections every hour
        "pool_pre_ping": True,  # Test connections before using
        "connect_args": {"timeout": 30} if "sqlite" in database_url else {},
    }
    
    # Initialize SQLAlchemy with Flask app
    db.init_app(app)
    
    # Register connection event handlers
    @event.listens_for(Engine, "connect")
    def receive_connect(dbapi_conn, connection_record):
        # Enable foreign keys for SQLite
        if "sqlite" in database_url:
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    
    # Create tables if they don't exist
    with app.app_context():
        db.create_all()
        logger.info("Database tables initialized")


def init_app_database(app: Flask) -> None:
    """Initialize database with Flask app context."""
    with app.app_context():
        db.create_all()
        logger.info("Database initialization complete")


def reset_database() -> None:
    """Drop and recreate all tables (development/testing only)."""
    logger.warning("Resetting database - this will DELETE all data!")
    db.drop_all()
    db.create_all()
    logger.info("Database reset complete")


def _mask_url(url: str) -> str:
    """Mask sensitive parts of database URL."""
    if "://" in url:
        scheme, rest = url.split("://", 1)
        if "@" in rest:
            creds, location = rest.rsplit("@", 1)
            return f"{scheme}://***@{location}"
    return url


# ============================================================================
# Helper Functions for Event/Sync Management
# ============================================================================


def get_or_create_member(
    member_id: str, name: str, color: str
) -> FamilyMember:
    """Get or create family member."""
    member = FamilyMember.query.filter_by(member_id=member_id).first()
    if not member:
        member = FamilyMember(
            member_id=member_id,
            name=name,
            color=color,
        )
        db.session.add(member)
        db.session.commit()
        logger.info(f"Created family member: {member.name}")
    return member


def get_or_create_calendar_source(
    feed_url: str,
    name: str,
    source_type: str = "ics",
    show_details: bool = True,
    busy_text: str = "Busy",
    show_location: bool = False,
) -> CalendarSource:
    """Get or create calendar source."""
    source = CalendarSource.query.filter_by(feed_url=feed_url).first()
    if not source:
        source = CalendarSource(
            feed_url=feed_url,
            name=name,
            source_type=source_type,
            show_details=show_details,
            busy_text=busy_text,
            show_location=show_location,
        )
        db.session.add(source)
        db.session.commit()
        logger.info(f"Created calendar source: {source.name}")
    return source


def create_subscription(
    member: FamilyMember,
    source: CalendarSource,
    show_details: Optional[bool] = None,
    busy_text: Optional[str] = None,
    show_location: Optional[bool] = None,
) -> MemberCalendarSubscription:
    """Create or update member subscription to calendar source."""
    sub = MemberCalendarSubscription.query.filter_by(
        member_id=member.id,
        source_id=source.id,
    ).first()
    
    if sub:
        # Update existing
        if show_details is not None:
            sub.show_details = show_details
        if busy_text is not None:
            sub.busy_text = busy_text
        if show_location is not None:
            sub.show_location = show_location
    else:
        # Create new
        sub = MemberCalendarSubscription(
            member_id=member.id,
            source_id=source.id,
            show_details=show_details,
            busy_text=busy_text,
            show_location=show_location,
        )
        db.session.add(sub)
    
    db.session.commit()
    return sub


def upsert_event(
    source: CalendarSource,
    external_event_id: Optional[str],
    external_event_hash: Optional[str],
    title: str,
    description: Optional[str],
    location: Optional[str],
    start_time: datetime,
    end_time: Optional[datetime],
    all_day: bool,
    ics_uid: Optional[str],
    ics_transp: str = "OPAQUE",
    ics_status: str = "CONFIRMED",
    ics_raw: Optional[bytes] = None,
    last_modified: Optional[datetime] = None,
) -> tuple[Event, bool]:
    """
    Insert or update event (upsert).
    
    Returns:
        (event, is_new) - tuple of event and whether it was newly created
    """
    # Try to find existing event by external ID or hash
    event = None
    if external_event_id:
        event = Event.query.filter_by(
            source_id=source.id,
            external_event_id=external_event_id,
        ).first()
    
    if not event and external_event_hash:
        event = Event.query.filter_by(
            source_id=source.id,
            external_event_hash=external_event_hash,
        ).first()
    
    is_new = event is None
    
    if is_new:
        # Create new event
        event = Event(
            source_id=source.id,
            external_event_id=external_event_id,
            external_event_hash=external_event_hash,
            title=title,
            description=description,
            location=location,
            start_time=start_time,
            end_time=end_time,
            all_day=all_day,
            ics_uid=ics_uid,
            ics_transp=ics_transp,
            ics_status=ics_status,
            ics_raw=ics_raw,
            last_modified_in_source=last_modified,
        )
        db.session.add(event)
    else:
        # Update existing event
        event.title = title
        event.description = description
        event.location = location
        event.start_time = start_time
        event.end_time = end_time
        event.all_day = all_day
        event.ics_transp = ics_transp
        event.ics_status = ics_status
        event.ics_raw = ics_raw
        event.last_modified_in_source = last_modified
        event.updated_at = datetime.now(timezone.utc)
    
    db.session.commit()
    return event, is_new


def delete_events_for_source(source: CalendarSource) -> int:
    """Delete all events for a source (for re-sync).
    
    Returns:
        Number of events deleted
    """
    count = Event.query.filter_by(source_id=source.id).delete()
    db.session.commit()
    return count


def get_events_for_date_range(
    start_time: datetime,
    end_time: datetime,
    member_ids: Optional[list[str]] = None,
    source_ids: Optional[list[str]] = None,
) -> list[Event]:
    """Get events within date range.
    
    Args:
        start_time: Start of range (inclusive)
        end_time: End of range (exclusive)
        member_ids: Filter to specific members (optional)
        source_ids: Filter to specific sources (optional)
    
    Returns:
        List of events, sorted by start time
    """
    query = Event.query.filter(
        Event.start_time >= start_time,
        Event.start_time < end_time,
    )
    
    if source_ids:
        query = query.filter(Event.source_id.in_(source_ids))
    
    # If filtering by member, join through subscriptions
    if member_ids:
        query = query.join(
            CalendarSource,
            Event.source_id == CalendarSource.id,
        ).join(
            MemberCalendarSubscription,
            CalendarSource.id == MemberCalendarSubscription.source_id,
        ).filter(
            MemberCalendarSubscription.member_id.in_(member_ids)
        )
    
    return query.order_by(Event.start_time).all()


def create_sync_log(source: CalendarSource) -> SyncLog:
    """Create a new sync log entry."""
    sync_log = SyncLog(
        source_id=source.id,
        sync_started_at=datetime.now(timezone.utc),
    )
    db.session.add(sync_log)
    db.session.commit()
    return sync_log


def get_last_sync_log(source: CalendarSource) -> Optional[SyncLog]:
    """Get the most recent sync log for a source."""
    return SyncLog.query.filter_by(source_id=source.id).order_by(
        SyncLog.sync_started_at.desc()
    ).first()


def get_sync_logs(source: CalendarSource, limit: int = 10) -> list[SyncLog]:
    """Get recent sync logs for a source."""
    return SyncLog.query.filter_by(source_id=source.id).order_by(
        SyncLog.sync_started_at.desc()
    ).limit(limit).all()


# ============================================================================
# Fixtures for Testing
# ============================================================================


def create_test_data() -> tuple[FamilyMember, FamilyMember, CalendarSource]:
    """Create test data for development/testing.
    
    Returns:
        (reuben, toby, test_source)
    """
    # Create family members
    reuben = get_or_create_member("reuben", "Reuben", "#0078d4")
    toby = get_or_create_member("toby", "Toby", "#e74856")
    
    # Create test calendar source
    source = get_or_create_calendar_source(
        feed_url="https://example.com/reuben.ics",
        name="Reuben's Calendar",
        show_details=True,
    )
    
    # Create subscriptions
    create_subscription(reuben, source)
    create_subscription(toby, source)
    
    return reuben, toby, source


def add_sample_event(
    source: CalendarSource,
    title: str = "Sample Event",
    days_offset: int = 0,
) -> Event:
    """Add a sample event to a source (for testing)."""
    from datetime import timedelta
    
    start = datetime.now(timezone.utc) + timedelta(days=days_offset)
    end = start + timedelta(hours=1)
    
    event, _ = upsert_event(
        source=source,
        external_event_id=f"sample-{start.timestamp()}",
        external_event_hash=None,
        title=title,
        description="This is a sample event",
        location="Sample Location",
        start_time=start,
        end_time=end,
        all_day=False,
        ics_uid=f"sample-{start.timestamp()}@example.com",
    )
    
    return event
