"""
Calendar sync engine - fetches WebCal feeds and updates database.

This module handles robust WebCal/CalDAV synchronization with error recovery,
deduplication, and transaction safety.
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import requests
from icalendar import Calendar, Event as IcsEvent

from db_init import create_sync_log, delete_events_for_source, upsert_event
from db_models import CalendarSource, SyncLog, db

logger = logging.getLogger(__name__)

# Constants
DEFAULT_TIMEOUT = 30
MAX_EVENTS_PER_BATCH = 1000
MAX_EVENT_SIZE_MB = 50


class SyncError(Exception):
    """Sync-related error."""

    pass


def fetch_calendar_data(url: str, timeout: int = DEFAULT_TIMEOUT) -> bytes:
    """Fetch calendar data from HTTP/WebCal URL.
    
    Args:
        url: HTTP(S) or WebCal URL
        timeout: Request timeout in seconds
    
    Returns:
        Raw ICS data (bytes)
    
    Raises:
        SyncError: On network or HTTP errors
    """
    # Convert WebCal protocol to HTTPS
    if url.startswith("webcal://"):
        url = "https://" + url[9:]
    elif url.startswith("webcals://"):
        url = "https://" + url[10:]
    
    try:
        logger.info(f"Fetching calendar from {url}")
        
        response = requests.get(
            url,
            timeout=timeout,
            headers={
                "User-Agent": "Family Calendar Server/2.0",
                "Accept": "text/calendar, application/ics",
            },
        )
        response.raise_for_status()
        
        data = response.content
        
        # Check size limit
        size_mb = len(data) / 1024 / 1024
        if size_mb > MAX_EVENT_SIZE_MB:
            raise SyncError(f"Calendar too large: {size_mb:.1f}MB (max {MAX_EVENT_SIZE_MB}MB)")
        
        logger.info(f"Fetched {len(data)} bytes")
        return data
        
    except requests.RequestException as e:
        raise SyncError(f"Failed to fetch calendar: {e}") from e


def fetch_caldav_calendar_data(
    url: str,
    username: str,
    password: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> bytes:
    """Fetch calendar data from CalDAV server.
    
    Args:
        url: CalDAV server URL
        username: CalDAV username
        password: CalDAV password
        timeout: Request timeout
    
    Returns:
        Combined ICS data (bytes)
    
    Raises:
        SyncError: On CalDAV errors
    """
    try:
        from caldav import DAVClient
        
        logger.info(f"Fetching CalDAV calendar from {url}")
        
        client = DAVClient(
            url=url,
            username=username,
            password=password,
            timeout=timeout,
        )
        
        principal = client.principal()
        calendars = principal.get_calendars()
        
        if not calendars:
            raise SyncError("No calendars found on CalDAV server")
        
        # Combine all calendars into single ICS
        combined = Calendar()
        combined.add("prodid", "-//Family Calendar CalDAV Sync//EN")
        combined.add("version", "2.0")
        
        event_count = 0
        for calendar in calendars:
            try:
                events = calendar.get_events()
                for event in events:
                    event_data = event.data
                    cal = Calendar.from_ical(event_data)
                    
                    # Copy VEVENT components
                    for component in cal.walk():
                        if component.name == "VEVENT":
                            combined.add_component(component)
                            event_count += 1
                            
            except Exception as e:
                logger.warning(f"Failed to fetch events from CalDAV calendar: {e}")
        
        logger.info(f"Fetched {event_count} events from CalDAV")
        return combined.to_ical()
        
    except SyncError:
        raise
    except Exception as e:
        raise SyncError(f"CalDAV fetch failed: {e}") from e


def parse_calendar_data(raw_data: bytes, source_url: str) -> Calendar:
    """Parse raw ICS data into Calendar object.
    
    Raises:
        SyncError: On parsing errors
    """
    try:
        return Calendar.from_ical(raw_data)
    except Exception as e:
        raise SyncError(f"Failed to parse ICS from {source_url}: {e}") from e


def compute_event_hash(
    summary: str,
    start: datetime,
    end: Optional[datetime],
    location: str,
) -> str:
    """Compute SHA256 hash of event content for deduplication.
    
    Used for events without UID.
    """
    key_parts = [
        str(summary or ""),
        str(start),
        str(end or ""),
        str(location or ""),
    ]
    key = "|".join(key_parts)
    return hashlib.sha256(key.encode()).hexdigest()


def extract_ics_event(
    ics_event: IcsEvent,
    source: CalendarSource,
) -> Optional[dict]:
    """Extract and normalize an ICS event.
    
    Returns:
        Dictionary of event data, or None if event should be skipped
    
    Raises:
        ValueError: On invalid event data
    """
    # Get UID (required for deduplication)
    ics_uid = str(ics_event.get("UID") or "").strip()
    
    # Get summary/title
    title = str(ics_event.get("SUMMARY") or "").strip()
    if not title:
        logger.warning("Skipping event without summary")
        return None
    
    # Get timing
    dtstart = ics_event.get("DTSTART")
    dtend = ics_event.get("DTEND")
    
    if not dtstart:
        logger.warning(f"Skipping event without start time: {title}")
        return None
    
    # Parse start time
    if hasattr(dtstart.dt, "date"):
        # Date-only (all-day event)
        start_time = datetime.combine(
            dtstart.dt,
            datetime.min.time(),
            tzinfo=timezone.utc,
        )
        all_day = True
    else:
        # DateTime
        start_time = dtstart.dt
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        else:
            start_time = start_time.astimezone(timezone.utc)
        all_day = False
    
    # Parse end time
    end_time = None
    if dtend:
        if hasattr(dtend.dt, "date"):
            end_time = datetime.combine(
                dtend.dt,
                datetime.min.time(),
                tzinfo=timezone.utc,
            )
        else:
            end_time = dtend.dt
            if end_time.tzinfo is None:
                end_time = end_time.replace(tzinfo=timezone.utc)
            else:
                end_time = end_time.astimezone(timezone.utc)
    
    # Get description
    description = str(ics_event.get("DESCRIPTION") or "").strip() or None
    
    # Get location
    location = str(ics_event.get("LOCATION") or "").strip() or None
    
    # Get TRANSP (busy/free status)
    ics_transp = str(ics_event.get("TRANSP") or "OPAQUE").upper()
    if ics_transp not in ("OPAQUE", "TRANSPARENT"):
        ics_transp = "OPAQUE"
    
    # Get STATUS
    ics_status = str(ics_event.get("STATUS") or "CONFIRMED").upper()
    if ics_status not in ("CONFIRMED", "TENTATIVE", "CANCELLED"):
        ics_status = "CONFIRMED"
    
    # Get last modified time
    last_modified = None
    dtstamp = ics_event.get("DTSTAMP")
    if dtstamp:
        last_modified = dtstamp.dt
        if hasattr(last_modified, "tzinfo") and last_modified.tzinfo is None:
            last_modified = last_modified.replace(tzinfo=timezone.utc)
    
    # Compute hash for deduplication (UID-less fallback)
    external_hash = None
    if not ics_uid:
        external_hash = compute_event_hash(title, start_time, end_time, location or "")
    
    # Store raw event for recovery
    ics_raw = None
    try:
        ics_raw = ics_event.to_ical()
    except Exception as e:
        logger.warning(f"Failed to store raw ICS for {title}: {e}")
    
    return {
        "ics_uid": ics_uid or None,
        "external_hash": external_hash,
        "title": title,
        "description": description,
        "location": location,
        "start_time": start_time,
        "end_time": end_time,
        "all_day": all_day,
        "ics_transp": ics_transp,
        "ics_status": ics_status,
        "last_modified": last_modified,
        "ics_raw": ics_raw,
    }


def sync_calendar_source(source: CalendarSource) -> SyncLog:
    """Synchronize a single calendar source.
    
    This is the main sync operation - fetches, parses, and stores events.
    
    Returns:
        SyncLog entry with results/errors
    """
    start_time = time.time()
    sync_log = create_sync_log(source)
    
    try:
        logger.info(f"Syncing calendar source: {source.name}")
        
        # Fetch raw ICS data
        if source.source_type == "caldav":
            if not source.caldav_username or not source.caldav_password:
                raise SyncError("CalDAV source missing credentials")
            raw_data = fetch_caldav_calendar_data(
                source.feed_url,
                source.caldav_username,
                source.caldav_password,
            )
        else:
            raw_data = fetch_calendar_data(source.feed_url)
        
        sync_log.fetched_bytes = len(raw_data)
        sync_log.http_status_code = 200
        
        # Parse ICS
        calendar = parse_calendar_data(raw_data, source.feed_url)
        
        # Clear existing events for fresh import
        # (Could be optimized to do incremental updates)
        deleted = delete_events_for_source(source)
        logger.info(f"Cleared {deleted} existing events for fresh sync")
        
        # Extract events
        events_found = 0
        events_imported = 0
        events_updated = 0
        duplicates_skipped = 0
        parse_errors = 0
        error_details = []
        
        for ics_event in calendar.walk("VEVENT"):
            events_found += 1
            
            try:
                # Extract event data
                event_data = extract_ics_event(ics_event, source)
                if not event_data:
                    continue
                
                # Insert or update event in database
                event, is_new = upsert_event(
                    source=source,
                    external_event_id=event_data["ics_uid"],
                    external_event_hash=event_data["external_hash"],
                    title=event_data["title"],
                    description=event_data["description"],
                    location=event_data["location"],
                    start_time=event_data["start_time"],
                    end_time=event_data["end_time"],
                    all_day=event_data["all_day"],
                    ics_uid=event_data["ics_uid"],
                    ics_transp=event_data["ics_transp"],
                    ics_status=event_data["ics_status"],
                    ics_raw=event_data["ics_raw"],
                    last_modified=event_data["last_modified"],
                )
                
                if is_new:
                    events_imported += 1
                else:
                    events_updated += 1
                    
            except Exception as e:
                parse_errors += 1
                error_msg = f"Failed to parse event {events_found}: {str(e)}"
                logger.warning(error_msg)
                error_details.append({"event_number": events_found, "error": str(e)})
        
        # Update sync log with results
        sync_log.events_found = events_found
        sync_log.events_imported = events_imported
        sync_log.events_updated = events_updated
        sync_log.duplicates_skipped = duplicates_skipped
        sync_log.parse_errors = parse_errors
        
        if error_details:
            import json
            sync_log.error_details = json.dumps(error_details[:10])  # Limit to 10 errors
        
        # Determine status
        if parse_errors == 0:
            status = "success"
        elif events_imported > 0 or events_updated > 0:
            status = "partial"
        else:
            status = "failed"
            sync_log.error_message = f"No events extracted ({parse_errors} errors)"
        
        duration_ms = int((time.time() - start_time) * 1000)
        sync_log.complete(status, duration_ms)
        
        # Update source tracking
        source.last_sync_at = datetime.now(timezone.utc)
        source.last_sync_status = status
        if status == "failed":
            source.last_sync_error = sync_log.error_message
        else:
            source.last_sync_error = None
        
        db.session.commit()
        
        logger.info(
            f"Sync complete for {source.name}: "
            f"{events_imported} imported, {events_updated} updated, "
            f"{parse_errors} errors ({duration_ms}ms)"
        )
        
        return sync_log
        
    except SyncError as e:
        logger.error(f"Sync failed for {source.name}: {e}")
        sync_log.status = "failed"
        sync_log.error_message = str(e)
        sync_log.complete("failed", int((time.time() - start_time) * 1000))
        
        source.last_sync_at = datetime.now(timezone.utc)
        source.last_sync_status = "failed"
        source.last_sync_error = str(e)
        
        db.session.commit()
        return sync_log
        
    except Exception as e:
        logger.exception(f"Unexpected error syncing {source.name}")
        sync_log.status = "failed"
        sync_log.error_message = f"Unexpected error: {str(e)}"
        sync_log.complete("failed", int((time.time() - start_time) * 1000))
        
        source.last_sync_at = datetime.now(timezone.utc)
        source.last_sync_status = "failed"
        source.last_sync_error = str(e)
        
        db.session.commit()
        return sync_log


def sync_all_sources() -> list[SyncLog]:
    """Synchronize all calendar sources.
    
    Returns:
        List of SyncLog entries
    """
    from db_models import CalendarSource as DbCalendarSource
    
    sources = DbCalendarSource.query.all()
    results = []
    
    for source in sources:
        try:
            sync_log = sync_calendar_source(source)
            results.append(sync_log)
        except Exception as e:
            logger.exception(f"Failed to sync {source.name}")
    
    return results
