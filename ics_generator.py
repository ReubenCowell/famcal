"""
ICS feed generation from database.

Generates ICS feeds for individual members or combined family feed,
maintaining compatibility with existing subscribers.

Key Constraint: Output must be binary-identical to the old file-based system.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from icalendar import Calendar, Event

from db_models import CalendarSource, Event as DbEvent, FamilyMember, db

logger = logging.getLogger(__name__)

# ICS/iCal constants
ICALENDAR_VERSION = "2.0"
PRODUCT_ID = "-//Family Calendar//EN"


def create_ics_event(
    db_event: DbEvent,
    subscription,  # MemberCalendarSubscription
) -> Event:
    """Create ICS event from database event with privacy controls.
    
    This applies the privacy settings (show_details, busy_text) from the
    member's subscription to the calendar source.
    
    Returns:
        icalendar.Event object
    """
    event = Event()
    
    # Copy basic fields
    event.add("uid", db_event.ics_uid or db_event.id)
    event.add("dtstamp", datetime.now(timezone.utc))
    
    # Copy start/end times
    if db_event.all_day:
        # All-day events use DATE format (YYYYMMDD)
        event.add("dtstart;VALUE=DATE", db_event.start_time.date())
        if db_event.end_time:
            # ICS all-day events include the day after as exclusive end
            event.add("dtend;VALUE=DATE", db_event.end_time.date())
    else:
        # Timed events use DATETIME format
        event.add("dtstart", db_event.start_time)
        if db_event.end_time:
            event.add("dtend", db_event.end_time)
    
    # Apply privacy settings
    if subscription.get_effective_show_details():
        # Show full details
        event.add("summary", db_event.title)
        event.add("description", db_event.description or "")
        if subscription.get_effective_show_location():
            event.add("location", db_event.location or "")
    else:
        # Privacy mode: show only busy text
        event.add("summary", subscription.get_effective_busy_text())
        event.add("description", "")
        # Location is hidden in privacy mode
    
    # Preserve the event's original busy/free and tentative status
    event.add("transp", db_event.ics_transp)  # OPAQUE (busy) or TRANSPARENT (free)
    if db_event.ics_status != "CONFIRMED":
        event.add("status", db_event.ics_status)  # TENTATIVE, CANCELLED
    
    event.add("class", "PUBLIC")
    
    return event


def generate_member_ics(member: FamilyMember) -> bytes:
    """Generate ICS feed for a single family member.
    
    Includes all calendar sources the member is subscribed to, with
    privacy settings applied per source.
    
    Returns:
        Binary ICS data
    """
    # Create calendar
    calendar = Calendar()
    calendar.add("prodid", PRODUCT_ID)
    calendar.add("version", ICALENDAR_VERSION)
    calendar.add("calscale", "GREGORIAN")
    calendar.add("method", "PUBLISH")
    calendar.add("x-wr-calname", f"{member.name}'s Calendar")
    calendar.add("x-wr-timezone", "UTC")
    
    # Track timezone components to avoid duplicates
    seen_tzids = set()
    
    # Get all subscriptions for this member
    subscriptions = member.subscriptions
    
    for subscription in subscriptions:
        source = subscription.source
        
        # Get all events from this source
        events = DbEvent.query.filter_by(source_id=source.id).order_by(
            DbEvent.start_time
        ).all()
        
        for db_event in events:
            # Create ICS event with privacy applied
            ics_event = create_ics_event(db_event, subscription)
            calendar.add_component(ics_event)
    
    # Generate ICS bytes
    ics_bytes = calendar.to_ical()
    return ics_bytes


def generate_family_ics() -> bytes:
    """Generate combined ICS feed for entire family.
    
    Includes events from all family members' subscribed sources,
    with privacy settings respected per member subscription.
    Events are prefixed with member names for clarity.
    
    Returns:
        Binary ICS data
    """
    # Create calendar
    calendar = Calendar()
    calendar.add("prodid", "-//Family Calendar - Combined//EN")
    calendar.add("version", ICALENDAR_VERSION)
    calendar.add("calscale", "GREGORIAN")
    calendar.add("method", "PUBLISH")
    calendar.add("x-wr-calname", "Family Calendar")
    calendar.add("x-wr-timezone", "UTC")
    
    # Get all events from all sources, organized by member
    members = FamilyMember.query.all()
    
    # Track events we've added to avoid duplicates
    added_event_ids = set()
    
    for member in members:
        subscriptions = member.subscriptions
        
        for subscription in subscriptions:
            source = subscription.source
            
            # Get all events from this source
            events = DbEvent.query.filter_by(source_id=source.id).order_by(
                DbEvent.start_time
            ).all()
            
            for db_event in events:
                # Avoid adding the same event twice if multiple members subscribe
                unique_key = (db_event.id, source.id)
                if unique_key in added_event_ids:
                    continue
                added_event_ids.add(unique_key)
                
                # Create ICS event with privacy applied for this member
                ics_event = create_ics_event(db_event, subscription)
                
                # Prefix summary with member name (like file-based version)
                original_summary = str(ics_event.get("SUMMARY", "Untitled"))
                ics_event["SUMMARY"] = f"{member.name}: {original_summary}"
                
                calendar.add_component(ics_event)
    
    # Generate ICS bytes
    ics_bytes = calendar.to_ical()
    return ics_bytes


def get_member_ics(member_id: str) -> Optional[bytes]:
    """Get ICS feed for member, or None if member not found."""
    member = FamilyMember.query.filter_by(member_id=member_id).first()
    if not member:
        return None
    
    return generate_member_ics(member)


def get_family_ics() -> bytes:
    """Get combined family ICS feed."""
    return generate_family_ics()


# ============================================================================
# Backward Compatibility: File-based Fallback
# ============================================================================


def write_ics_file(member_id: str, ics_bytes: bytes, output_dir: str) -> str:
    """Write ICS file for backup/compatibility.
    
    Args:
        member_id: Member ID
        ics_bytes: ICS data
        output_dir: Directory to write file
    
    Returns:
        Path to written file
    """
    import os
    from pathlib import Path
    
    output_path = Path(output_dir) / f"{member_id}_calendar.ics"
    
    # Write atomically (write to temp, then rename)
    temp_path = output_path.with_suffix(".ics.tmp")
    temp_path.write_bytes(ics_bytes)
    temp_path.replace(output_path)
    
    logger.info(f"Wrote ICS file: {output_path}")
    return str(output_path)


def write_all_ics_files(output_dir: str) -> list[str]:
    """Write ICS files for all members (for backup).
    
    Args:
        output_dir: Directory to write files
    
    Returns:
        List of written file paths
    """
    import os
    
    os.makedirs(output_dir, exist_ok=True)
    
    members = FamilyMember.query.all()
    written_files = []
    
    for member in members:
        try:
            ics_bytes = generate_member_ics(member)
            path = write_ics_file(member.member_id, ics_bytes, output_dir)
            written_files.append(path)
        except Exception as e:
            logger.error(f"Failed to write ICS file for {member.name}: {e}")
    
    # Also write family combined feed
    try:
        ics_bytes = generate_family_ics()
        path = write_ics_file("family", ics_bytes, output_dir)
        written_files.append(path)
    except Exception as e:
        logger.error(f"Failed to write combined ICS file: {e}")
    
    return written_files


# ============================================================================
# Backward Compatibility: Compare old vs new ICS
# ============================================================================


def compare_ics_outputs(old_ics: bytes, new_ics: bytes) -> tuple[bool, str]:
    """Compare old and new ICS outputs (for migration verification).
    
    Returns:
        (are_identical, difference_summary)
    """
    if old_ics == new_ics:
        return True, "ICS outputs are identical"
    
    # Parse both
    try:
        old_cal = Calendar.from_ical(old_ics)
        new_cal = Calendar.from_ical(new_ics)
    except Exception as e:
        return False, f"Failed to parse ICS: {e}"
    
    # Count events
    old_events = list(old_cal.walk("VEVENT"))
    new_events = list(new_cal.walk("VEVENT"))
    
    if len(old_events) != len(new_events):
        return False, f"Event count differs: {len(old_events)} old vs {len(new_events)} new"
    
    # Basic structural check
    old_summary = set(str(e.get("summary", "")) for e in old_events)
    new_summary = set(str(e.get("summary", "")) for e in new_events)
    
    if old_summary != new_summary:
        missing = old_summary - new_summary
        extra = new_summary - old_summary
        msg = f"Summary mismatch"
        if missing:
            msg += f"\n  Missing: {missing}"
        if extra:
            msg += f"\n  Extra: {extra}"
        return False, msg
    
    return False, "ICS outputs differ (binary not identical, but logically similar)"
