#!/usr/bin/env python3
"""
Family Calendar Server - Multi-user ICS calendar server with privacy controls.

Each family member gets their own subscribable ICS feed with configurable privacy.
Events can show:
  - Full details (summary, description, location) with original busy/tentative from event
  - Privacy mode: "Busy" title only, preserving the event's original TRANSP and STATUS

The busy/tentative status is read from the actual calendar events (as set in Outlook/Google Calendar),
not from configuration.

Run:
    python family_calendar_server.py --config family_config.json
"""

from __future__ import annotations

import argparse
import ipaddress
import hashlib
import json
import logging
import os
import re
import threading
from hmac import compare_digest
from uuid import uuid4
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from flask import Flask, Response, g, jsonify, redirect, render_template, request, session, url_for
from icalendar import Calendar, Event

RECURRENCE_EXPANSION_AVAILABLE = False
try:
    import recurring_ical_events
    RECURRENCE_EXPANSION_AVAILABLE = True
except ImportError:
    recurring_ical_events = None

# Database integration (Phase 1: Dual-write mode)
DATABASE_AVAILABLE = False
try:
    from db_models import db as database_db, FamilyMember as DbFamilyMember
    from db_init import configure_database, get_or_create_member, get_or_create_calendar_source, create_subscription, upsert_event
    from ics_generator import generate_member_ics, generate_family_ics
    DATABASE_AVAILABLE = True
except ImportError:
    pass  # Will log this after logging is initialized

DEFAULT_CONFIG_FILE = "family_config.json"
DEFAULT_REFRESH_SECONDS = 3600
MAX_CALENDAR_URL_LENGTH = 2048
MAX_EVENT_TEXT_LENGTH = 4096
MAX_WEBSITE_PASSWORD_LENGTH = 256
MAX_SECRET_KEY_LENGTH = 512
DEFAULT_MEMBER_COLOR = "#0078d4"
ALLOWED_SOURCE_TYPES = {"ics", "caldav"}
USE_DATABASE = os.getenv("FAMCAL_USE_DATABASE", "false").lower() == "true"  # Feature flag
USE_DATABASE_FOR_ICS = os.getenv("FAMCAL_DATABASE_ICS", "true").lower() == "true"  # Phase 3: Database-first ICS generation


def _safe_positive_int_env(var_name: str, default_value: int, minimum: int = 1) -> int:
    """Read a positive integer env var with a safe fallback."""
    raw_value = (os.getenv(var_name, "") or "").strip()
    if not raw_value:
        return default_value
    try:
        parsed_value = int(raw_value)
    except ValueError:
        return default_value
    return max(minimum, parsed_value)


# Keep API limits high by default so large family calendars do not get clipped in UI views.
API_MEMBER_EVENTS_LIMIT = _safe_positive_int_env("FAMCAL_API_MEMBER_EVENTS_LIMIT", 100000, minimum=1000)
API_COMBINED_EVENTS_LIMIT = _safe_positive_int_env("FAMCAL_API_COMBINED_EVENTS_LIMIT", 1000000, minimum=5000)

# Predefined member colors for calendar display
MEMBER_COLORS = [
    "#0078d4",  # blue
    "#e74856",  # red
    "#00cc6a",  # green
    "#f7630c",  # orange
    "#886ce4",  # purple
    "#00b7c3",  # teal
    "#ca5010",  # brown-orange
    "#e3008c",  # magenta
    "#498205",  # olive
    "#bf0077",  # rose
    "#008272",  # dark teal
    "#4a154b",  # dark purple
]


@dataclass
class CalendarSource:
    """Configuration for a single calendar source."""
    url: str
    name: str
    show_details: bool  # If False, only show busy_text but preserve event's TRANSP/STATUS
    busy_text: str = "Busy"  # Custom text shown when show_details is False
    show_location: bool = False  # If True, preserve location even when details are hidden
    source_type: str = "ics"  # "ics" for direct ICS URL, "caldav" for CalDAV subscription
    caldav_username: str = ""  # Username for CalDAV authentication
    caldav_password: str = ""  # Password for CalDAV authentication


@dataclass
class FamilyMember:
    """Configuration for a family member."""
    id: str
    name: str
    calendars: list[CalendarSource]
    color: str = ""


@dataclass
class ServerConfig:
    """Server configuration."""
    refresh_interval_seconds: int = 3600
    host: str = "0.0.0.0"
    port: int = 8000
    domain: str | None = None
    website_password: str = ""
    secret_key: str = ""


@dataclass
class MemberStatus:
    """Status for a family member's calendar."""
    last_refresh_utc: str | None = None
    last_error: str | None = None
    merged_events: int = 0
    duplicate_events_skipped: int = 0
    successful_sources: int = 0
    configured_sources: int = 0


class FamilyCalendarManager:
    """Manages family calendar configuration and data."""

    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.members: dict[str, FamilyMember] = {}
        self.server_config: ServerConfig = ServerConfig()
        self.statuses: dict[str, MemberStatus] = {}
        self.locks: dict[str, threading.Lock] = {}  # Per-member locks
        self.refresh_in_progress: dict[str, bool] = {}  # Track ongoing refreshes to prevent concurrency
        self.output_dir = Path("output")
        self.output_dir.mkdir(exist_ok=True)
        self.global_lock = threading.Lock()  # For config file writes
        self.status_lock = threading.Lock()  # For status dict updates
        self.load_config()

    def load_config(self) -> None:
        """Load configuration from JSON file."""
        if not self.config_path.exists():
            # Create default config
            self.save_config()
            return

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logging.error("Failed to load config %s: %s", self.config_path, exc)
            config = {}

        if not isinstance(config, dict):
            logging.error("Config file has invalid root type (%s); using defaults", type(config).__name__)
            config = {}

        # Load family members
        self.members = {}
        self.statuses = {}
        self.locks = {}
        self.refresh_in_progress = {}
        member_index = 0
        family_members = config.get("family_members", {})
        if not isinstance(family_members, dict):
            logging.warning("Ignoring invalid family_members section (expected object)")
            family_members = {}

        for raw_member_id, member_data in family_members.items():
            if not isinstance(member_data, dict):
                logging.warning("Skipping malformed member entry for key %r", raw_member_id)
                continue

            member_id = str(raw_member_id).strip().lower()
            if not member_id or not re.match(r"^[a-z0-9_]+$", member_id):
                logging.warning("Skipping member with invalid id: %r", raw_member_id)
                continue

            calendars_raw = member_data.get("calendars", [])
            if not isinstance(calendars_raw, list):
                logging.warning("Member %s has invalid calendars list; using empty list", member_id)
                calendars_raw = []

            calendars: list[CalendarSource] = []
            for cal in calendars_raw:
                if not isinstance(cal, dict):
                    logging.warning("Skipping malformed calendar entry for member %s", member_id)
                    continue

                source_type = str(cal.get("source_type", "ics") or "ics").strip().lower()
                if source_type not in ALLOWED_SOURCE_TYPES:
                    source_type = "ics"

                calendars.append(
                    CalendarSource(
                        url=str(cal.get("url", "") or "").strip(),
                        name=_safe_text(cal.get("name", "Untitled"), default="Untitled", max_len=200),
                        show_details=bool(cal.get("show_details", True)),
                        busy_text=_safe_text(cal.get("busy_text", "Busy"), default="Busy", max_len=120),
                        show_location=bool(cal.get("show_location", False)),
                        source_type=source_type,
                        caldav_username=_safe_text(cal.get("caldav_username", ""), max_len=200),
                        caldav_password=str(cal.get("caldav_password", "") or ""),
                    )
                )

            color = _normalize_member_color(member_data.get("color"))
            if color == DEFAULT_MEMBER_COLOR:
                color = MEMBER_COLORS[member_index % len(MEMBER_COLORS)]

            self.members[member_id] = FamilyMember(
                id=member_id,
                name=_safe_text(member_data.get("name", member_id.capitalize()), default=member_id.capitalize(), max_len=120),
                calendars=calendars,
                color=color,
            )
            self.statuses[member_id] = MemberStatus(configured_sources=len(calendars))
            self.locks[member_id] = threading.Lock()
            self.refresh_in_progress[member_id] = False
            member_index += 1

        # Load server settings
        server_settings = config.get("server_settings", {})
        if not isinstance(server_settings, dict):
            logging.warning("Ignoring invalid server_settings section (expected object)")
            server_settings = {}

        refresh_interval_raw = server_settings.get("refresh_interval_seconds", DEFAULT_REFRESH_SECONDS)
        try:
            refresh_interval_seconds = int(refresh_interval_raw)
        except (TypeError, ValueError):
            logging.warning(
                "Invalid refresh_interval_seconds %r; using default %d",
                refresh_interval_raw,
                DEFAULT_REFRESH_SECONDS,
            )
            refresh_interval_seconds = DEFAULT_REFRESH_SECONDS
        refresh_interval_seconds = max(300, refresh_interval_seconds)

        port_raw = server_settings.get("port", 8000)
        try:
            port = int(port_raw)
        except (TypeError, ValueError):
            logging.warning("Invalid port %r; using default 8000", port_raw)
            port = 8000
        if not (1 <= port <= 65535):
            logging.warning("Out-of-range port %r; using default 8000", port_raw)
            port = 8000

        host = _safe_text(server_settings.get("host", "0.0.0.0"), default="0.0.0.0", max_len=255)
        if not host:
            host = "0.0.0.0"

        domain_raw = _safe_text(server_settings.get("domain", ""), max_len=255)
        domain = domain_raw or None

        website_password = str(server_settings.get("website_password", "") or "")
        if len(website_password) > MAX_WEBSITE_PASSWORD_LENGTH:
            logging.warning(
                "website_password is too long; truncating to %d characters",
                MAX_WEBSITE_PASSWORD_LENGTH,
            )
            website_password = website_password[:MAX_WEBSITE_PASSWORD_LENGTH]

        secret_key = str(server_settings.get("secret_key", "") or "")
        if len(secret_key) > MAX_SECRET_KEY_LENGTH:
            logging.warning(
                "secret_key is too long; truncating to %d characters",
                MAX_SECRET_KEY_LENGTH,
            )
            secret_key = secret_key[:MAX_SECRET_KEY_LENGTH]

        self.server_config = ServerConfig(
            refresh_interval_seconds=refresh_interval_seconds,
            host=host,
            port=port,
            domain=domain,
            website_password=website_password,
            secret_key=secret_key,
        )

        logging.info("Loaded validated config for %d family members", len(self.members))

    def save_config(self) -> None:
        """Save configuration to JSON file."""
        config = {
            "family_members": {
                member.id: {
                    "name": member.name,
                    "color": member.color,
                    "calendars": [
                        {
                            "url": cal.url,
                            "name": cal.name,
                            "show_details": cal.show_details,
                            "busy_text": cal.busy_text,
                            "show_location": cal.show_location,
                            "source_type": cal.source_type,
                            "caldav_username": cal.caldav_username,
                            "caldav_password": cal.caldav_password
                        }
                        for cal in member.calendars
                    ]
                }
                for member in self.members.values()
            },
            "server_settings": {
                "refresh_interval_seconds": self.server_config.refresh_interval_seconds,
                "host": self.server_config.host,
                "port": self.server_config.port,
                "domain": self.server_config.domain or "",
                "website_password": self.server_config.website_password,
                "secret_key": self.server_config.secret_key,
            }
        }
        
        with self.global_lock:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)

    def get_output_path(self, member_id: str) -> Path:
        """Get output ICS file path for a member."""
        return self.output_dir / f"{member_id}_calendar.ics"

    def add_or_update_member(self, member: FamilyMember) -> None:
        """Add or update a family member."""
        if member.id not in self.members:
            self.locks[member.id] = threading.Lock()
            self.statuses[member.id] = MemberStatus(configured_sources=len(member.calendars))
            self.refresh_in_progress[member.id] = False
            # Assign color if not set
            if not member.color:
                idx = len(self.members) % len(MEMBER_COLORS)
                member.color = MEMBER_COLORS[idx]
        
        with self.locks[member.id]:
            self.members[member.id] = member
            with self.status_lock:
                self.statuses[member.id].configured_sources = len(member.calendars)

    def remove_member(self, member_id: str) -> None:
        """Remove a family member."""
        if member_id in self.members:
            with self.locks[member_id]:
                del self.members[member_id]
                with self.status_lock:
                    del self.statuses[member_id]
                del self.locks[member_id]
                del self.refresh_in_progress[member_id]
            
            # Delete output file
            output_path = self.get_output_path(member_id)
            if output_path.exists():
                try:
                    output_path.unlink()
                except OSError as e:
                    logging.warning(f"Failed to delete {output_path}: {e}")


def fetch_calendar_data(url: str, timeout_seconds: int = 30) -> bytes:
    """Fetch calendar data from a URL."""
    # Convert webcal:// to https:// (webcal is just http(s) with a different protocol handler)
    if url.startswith("webcal://"):
        url = "https://" + url[9:]
    elif url.startswith("webcals://"):
        url = "https://" + url[10:]
    
    response = requests.get(
        url,
        timeout=timeout_seconds,
        headers={"User-Agent": "family-calendar-server/1.0"},
    )
    response.raise_for_status()
    return response.content


def fetch_caldav_calendar_data(
    url: str,
    username: str,
    password: str,
    timeout_seconds: int = 30
) -> bytes:
    """Fetch calendar data from a CalDAV server and convert to ICS."""
    try:
        from caldav import DAVClient
        
        # Connect to CalDAV server
        client = DAVClient(
            url=url,
            username=username,
            password=password,
            timeout=timeout_seconds
        )
        
        # Get the principal and calendars
        principal = client.principal()
        calendars = principal.get_calendars()
        
        if not calendars:
            raise ValueError(f"No calendars found at {url}")
        
        # Create a combined calendar from all events
        combined_calendar = Calendar()
        combined_calendar.add('prodid', '-//Family Calendar CalDAV Sync//EN')
        combined_calendar.add('version', '2.0')
        combined_calendar.add('calscale', 'GREGORIAN')
        
        seen_tzids: set[str] = set()
        
        for calendar in calendars:
            try:
                # Get all events from this calendar
                events = calendar.get_events()
                
                for event in events:
                    # Parse the event data
                    event_data = event.data
                    cal = Calendar.from_ical(event_data)
                    
                    # Copy timezone components
                    for tz_component in cal.walk("VTIMEZONE"):
                        tzid_value = tz_component.get("TZID")
                        tzid = str(tzid_value).strip() if tzid_value else ""
                        if tzid and tzid not in seen_tzids:
                            seen_tzids.add(tzid)
                            combined_calendar.add_component(tz_component)
                    
                    # Add VEVENT components
                    for component in cal.walk():
                        if component.name == 'VEVENT':
                            combined_calendar.add_component(component)
                            
            except Exception as e:
                logging.warning(f"Error processing CalDAV calendar {calendar}: {e}")
        
        # Convert to ICS bytes
        return combined_calendar.to_ical()
        
    except Exception as exc:
        raise ValueError(f"Failed to fetch CalDAV data from {url}: {exc}") from exc


def parse_calendar_data(raw_data: bytes, source_url: str) -> Calendar:
    """Parse ICS data into a Calendar object."""
    try:
        return Calendar.from_ical(raw_data)
    except Exception as exc:
        raise ValueError(f"Invalid ICS data from {source_url}: {exc}") from exc


def _event_fingerprint(event: Event) -> str:
    """Build a stable fingerprint string for collision detection."""
    return "|".join([
        str(event.get("SUMMARY", "") or ""),
        str(event.get("DTSTART", "") or ""),
        str(event.get("DTEND", "") or ""),
        str(event.get("RECURRENCE-ID", "") or ""),
        str(event.get("LOCATION", "") or ""),
        str(event.get("DESCRIPTION", "") or ""),
    ])


def _collision_safe_uid(uid: str, fingerprint: str, seen_uid_fingerprints: dict[str, str]) -> str:
    """Return a unique UID if the original UID collides with different content."""
    suffix = hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:8]
    candidate = f"{uid}-collision-{suffix}"
    counter = 2
    while candidate in seen_uid_fingerprints:
        candidate = f"{uid}-collision-{suffix}-{counter}"
        counter += 1
    return candidate


def event_uid(event: Event, source_namespace: str = "") -> str:
    """Get or generate a UID for an event, namespaced for missing-UID fallback."""
    uid_value = event.get("UID")
    uid = str(uid_value).strip() if uid_value else ""

    if uid:
        return uid

    fallback_key = "|".join([source_namespace, _event_fingerprint(event)])
    digest = hashlib.sha1(fallback_key.encode("utf-8")).hexdigest()
    generated_uid = f"generated-{digest}@famcal"
    event["UID"] = generated_uid
    return generated_uid


def validate_calendar_url(url: str, allow_insecure_http: bool = False) -> tuple[bool, str]:
    """Validate user-provided calendar URLs and reject common SSRF targets."""
    if not url:
        return False, "URL is required"
    if len(url) > MAX_CALENDAR_URL_LENGTH:
        return False, f"URL is too long (max {MAX_CALENDAR_URL_LENGTH} characters)"

    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https", "webcal", "webcals"}:
        return False, "URL scheme must be https://, webcal://, or webcals://"
    if scheme == "http" and not allow_insecure_http:
        return False, "Insecure http:// URLs are not allowed; use https:// or webcal://"
    if not parsed.netloc:
        return False, "URL must include a hostname"
    if parsed.username or parsed.password:
        return False, "URL must not include embedded credentials"

    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False, "URL must include a valid hostname"
    if host == "localhost" or host.endswith(".local"):
        return False, "Local hosts are not allowed"

    try:
        ip = ipaddress.ip_address(host)
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False, "Private or local network addresses are not allowed"
    except ValueError:
        # Host is not a literal IP; keep it.
        pass

    return True, ""


def _parse_date_param(value: str | None, field_name: str) -> tuple[str | None, str | None]:
    """Validate query date parameters and normalize to YYYY-MM-DD."""
    if value is None or value == "":
        return None, None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None, f"{field_name} must be in YYYY-MM-DD format"
    return parsed.isoformat(), None


def _safe_text(value: Any, default: str = "", max_len: int = MAX_EVENT_TEXT_LENGTH) -> str:
    """Normalize optional text fields and cap unbounded values."""
    text = str(value if value is not None else default)
    text = text.strip()
    if not text:
        text = default
    if len(text) > max_len:
        text = text[:max_len]
    return text


def _normalize_member_color(color: str | None) -> str:
    """Return a safe color token for frontend rendering."""
    color_value = (color or "").strip()
    if re.match(r"^#[0-9a-fA-F]{6}$", color_value):
        return color_value
    return DEFAULT_MEMBER_COLOR


def apply_privacy_to_event(event: Event, calendar_source: CalendarSource) -> Event:
    """
    Apply privacy settings to an event based on calendar source configuration.
    
    If show_details=True: Keep all event details as-is
    If show_details=False: Hide details but preserve the event's own TRANSP and STATUS
                           (busy/tentative is determined by the event itself, not config)
    """
    if calendar_source.show_details:
        return event

    # Clone first so DATE/DATETIME params (including TZID) are preserved exactly.
    private_event = Event.from_ical(event.to_ical())

    preserve_keys = {
        "UID",
        "DTSTART",
        "DTEND",
        "DTSTAMP",
        "TRANSP",
        "STATUS",
        "DURATION",
        "RRULE",
        "RDATE",
        "EXDATE",
        "RECURRENCE-ID",
        "SEQUENCE",
        "CREATED",
        "LAST-MODIFIED",
    }
    for key in list(private_event.keys()):
        if key not in preserve_keys:
            del private_event[key]

    if "DTSTAMP" not in private_event:
        private_event["DTSTAMP"] = datetime.now(timezone.utc)

    private_event["SUMMARY"] = calendar_source.busy_text or "Busy"
    private_event["CLASS"] = "PRIVATE"

    if calendar_source.show_location and "LOCATION" in event:
        location = str(event.get("LOCATION", "") or "")
        if location:
            private_event["LOCATION"] = location
    
    return private_event


def merge_member_calendars(
    member: FamilyMember,
    timeout_seconds: int = 30
) -> tuple[Calendar, int, int, int, list[str]]:
    """
    Merge all calendars for a family member with privacy controls.
    
    Returns: (merged_calendar, merged_events, duplicates_skipped, successful_sources, failed_sources)
    """
    merged = Calendar()
    merged.add("prodid", f"-//Family Calendar - {member.name}//EN")
    merged.add("version", "2.0")
    merged.add("calscale", "GREGORIAN")
    merged.add("x-wr-calname", f"{member.name}'s Calendar")

    seen_uid_fingerprints: dict[str, str] = {}
    seen_tzids: set[str] = set()
    merged_events = 0
    duplicates_skipped = 0
    successful_sources = 0
    failed_sources: list[str] = []

    for calendar_source in member.calendars:
        if not calendar_source.url:
            logging.warning("Skipping empty URL for %s: %s", member.name, calendar_source.name)
            continue

        is_valid_url, url_error = validate_calendar_url(calendar_source.url, allow_insecure_http=True)
        if not is_valid_url:
            logging.warning(
                "Skipping invalid URL for %s (%s): %s",
                member.name,
                calendar_source.name,
                url_error,
            )
            continue

        if calendar_source.url.lower().startswith("http://"):
            logging.warning("Using insecure http:// URL for %s (%s)", member.name, calendar_source.name)

        try:
            # Fetch data based on source type
            if calendar_source.source_type == "caldav":
                if not calendar_source.caldav_username or not calendar_source.caldav_password:
                    logging.warning("Skipping CalDAV source without credentials: %s", calendar_source.name)
                    continue
                logging.info("Fetching CalDAV calendar: %s for %s", calendar_source.name, member.name)
                raw_data = fetch_caldav_calendar_data(
                    calendar_source.url,
                    calendar_source.caldav_username,
                    calendar_source.caldav_password,
                    timeout_seconds
                )
            else:
                logging.info("Fetching ICS calendar: %s for %s from %s", calendar_source.name, member.name, calendar_source.url)
                raw_data = fetch_calendar_data(calendar_source.url, timeout_seconds)
            
            calendar = parse_calendar_data(raw_data, calendar_source.url)
            
            # Copy timezone components
            for timezone_component in calendar.walk("VTIMEZONE"):
                tzid_value = timezone_component.get("TZID")
                tzid = str(tzid_value).strip() if tzid_value else ""
                if tzid and tzid not in seen_tzids:
                    seen_tzids.add(tzid)
                    merged.add_component(timezone_component)

            # Process events with privacy controls
            for event in calendar.walk("VEVENT"):
                fingerprint = _event_fingerprint(event)
                source_namespace = f"{member.id}|{calendar_source.url}|{calendar_source.name}"
                uid = event_uid(event, source_namespace=source_namespace)

                if uid in seen_uid_fingerprints:
                    if seen_uid_fingerprints[uid] == fingerprint:
                        duplicates_skipped += 1
                        continue

                    original_uid = uid
                    uid = _collision_safe_uid(uid, fingerprint, seen_uid_fingerprints)
                    event["UID"] = uid
                    logging.warning(
                        "UID collision for %s (%s): %s -> %s",
                        member.name,
                        calendar_source.name,
                        original_uid,
                        uid,
                    )

                seen_uid_fingerprints[uid] = fingerprint

                # Apply privacy settings (preserves event's own busy/tentative status)
                processed_event = apply_privacy_to_event(event, calendar_source)
                merged.add_component(processed_event)
                merged_events += 1

            successful_sources += 1
            logging.info("Successfully merged %s for %s (%d events, %d duplicates skipped)", calendar_source.name, member.name, merged_events, duplicates_skipped)

        except Exception as exc:
            failed_sources.append(f"{calendar_source.name}: {exc}")
            logging.warning("Failed to fetch %s for %s: %s", calendar_source.name, member.name, exc)

    return merged, merged_events, duplicates_skipped, successful_sources, failed_sources


def refresh_member_calendar(
    manager: FamilyCalendarManager,
    member_id: str,
    timeout_seconds: int = 30
) -> bool:
    """Refresh calendar for a single family member. Returns True if successful.
    
    Uses deduplication to prevent concurrent refresh of same member.
    """
    if member_id not in manager.members:
        return False
    
    # Prevent concurrent refresh of same member
    lock = manager.locks[member_id]
    with lock:
        if manager.refresh_in_progress[member_id]:
            logging.debug(f"Refresh already in progress for {member_id}, skipping")
            return False
        manager.refresh_in_progress[member_id] = True
    
    try:
        member = manager.members[member_id]
        status = manager.statuses[member_id]
        output_path = manager.get_output_path(member_id)
        
        logging.info(f"Starting refresh for {member.name} ({member_id})")

        merged, event_count, duplicate_count, successful, failed_sources = merge_member_calendars(
            member, timeout_seconds
        )

        # Save to file atomically
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = merged.to_ical()
        temp_path = output_path.with_suffix(output_path.suffix + ".tmp")

        with lock:
            try:
                # Write to temp file
                temp_path.write_bytes(payload)
                # Atomic replace
                temp_path.replace(output_path)
                
                # Phase 1: Dual-write to database (if enabled)
                if DATABASE_AVAILABLE and USE_DATABASE:
                    try:
                        _sync_events_to_database(member, merged)
                        logging.debug(f"Synced events to database for {member.name}")
                    except Exception as db_err:
                        logging.warning(f"Database sync failed for {member.name}: {db_err}")
                        # Don't fail the refresh if database sync fails
                
                # Update status
                with manager.status_lock:
                    status.last_refresh_utc = datetime.now(timezone.utc).isoformat()
                    status.last_error = "; ".join(failed_sources[:3]) if failed_sources else None
                    status.merged_events = event_count
                    status.duplicate_events_skipped = duplicate_count
                    status.successful_sources = successful
                
                logging.info(
                    "Refreshed %s: events=%d duplicates=%d sources=%d failed=%d (%.2f%% success)",
                    member.name, event_count, duplicate_count, successful, len(failed_sources),
                    (successful / status.configured_sources * 100) if status.configured_sources > 0 else 0
                )
                return True
            except OSError as e:
                logging.error(f"Failed to write calendar file for {member_id}: {e}")
                with manager.status_lock:
                    status.last_error = f"File write error: {e}"
                return False

    except Exception as exc:
        with lock:
            with manager.status_lock:
                status.last_error = str(exc)
        logging.exception("Failed to refresh %s: %s", member_id, exc)
        return False
    finally:
        # Always clear the in-progress flag
        with lock:
            manager.refresh_in_progress[member_id] = False


def refresh_all_calendars(manager: FamilyCalendarManager, timeout_seconds: int = 30) -> None:
    """Refresh calendars for all family members."""
    for member_id in list(manager.members.keys()):
        refresh_member_calendar(manager, member_id, timeout_seconds)


def _sync_config_to_database(manager: FamilyCalendarManager) -> None:
    """Sync configuration from JSON to database (Phase 1: Dual-write).
    
    This function ensures that members and calendar sources defined in the
    configuration file are also present in the database.
    """
    if not DATABASE_AVAILABLE or not USE_DATABASE:
        return
    
    try:
        # Sync each family member
        for member_id, member in manager.members.items():
            # Create/update member in database
            db_member = get_or_create_member(
                member_id=member.id,
                name=member.name,
                color=member.color
            )
            
            # Sync calendar sources
            for calendar_source in member.calendars:
                if not calendar_source.url:
                    continue
                
                # Create/update source
                db_source = get_or_create_calendar_source(
                    feed_url=calendar_source.url,
                    name=calendar_source.name,
                    source_type=calendar_source.source_type,
                    show_details=calendar_source.show_details,
                    busy_text=calendar_source.busy_text,
                    show_location=calendar_source.show_location,
                )
                
                # Create subscription (member → source)
                create_subscription(
                    member=db_member,
                    source=db_source,
                    show_details=calendar_source.show_details,
                    busy_text=calendar_source.busy_text,
                    show_location=calendar_source.show_location,
                )
        
        logging.info(f"Synced {len(manager.members)} members to database")
    except Exception as e:
        logging.error(f"Failed to sync config to database: {e}")


def _sync_events_to_database(member: FamilyMember, merged_calendar: Calendar) -> None:
    """Sync events from merged calendar to database (Phase 1: Dual-write).
    
    This extracts events from the ICS calendar and stores them in the database,
    maintaining the same deduplication and structure.
    """
    if not DATABASE_AVAILABLE or not USE_DATABASE:
        return
    
    try:
        # Get database member
        db_member = DbFamilyMember.query.filter_by(member_id=member.id).first()
        if not db_member:
            logging.debug(f"Member {member.id} not found in database during event sync")
            return
        
        # Get all sources for this member
        subscriptions = db_member.subscriptions
        if not subscriptions:
            logging.debug(f"No subscriptions found for {member.name}, skipping event sync")
            return
        
        # Extract events from the merged calendar
        event_count = 0
        for ics_event in merged_calendar.walk("VEVENT"):
            try:
                # Extract event properties
                uid = str(ics_event.get("UID", "")).strip()
                if not uid:
                    continue
                    
                title = str(ics_event.get("SUMMARY", "")).strip() or "Untitled"
                description = str(ics_event.get("DESCRIPTION", "") or "").strip() or None
                location = str(ics_event.get("LOCATION", "") or "").strip() or None
                
                # Parse dates
                dtstart = ics_event.get("DTSTART")
                dtend = ics_event.get("DTEND")
                
                if not dtstart:
                    continue
                    
                # Convert to datetime
                if hasattr(dtstart.dt, "date"):
                    # Date-only (all-day)
                    start_time = datetime.combine(dtstart.dt, datetime.min.time(), tzinfo=timezone.utc)
                    all_day = True
                else:
                    # DateTime
                    start_time = dtstart.dt
                    if start_time.tzinfo is None:
                        start_time = start_time.replace(tzinfo=timezone.utc)
                    else:
                        start_time = start_time.astimezone(timezone.utc)
                    all_day = False
                
                end_time = None
                if dtend:
                    if hasattr(dtend.dt, "date"):
                        end_time = datetime.combine(dtend.dt, datetime.min.time(), tzinfo=timezone.utc)
                    else:
                        end_time = dtend.dt
                        if end_time.tzinfo is None:
                            end_time = end_time.replace(tzinfo=timezone.utc)
                        else:
                            end_time = end_time.astimezone(timezone.utc)
                
                # Get TRANSP and STATUS
                ics_transp = str(ics_event.get("TRANSP", "OPAQUE")).upper()
                if ics_transp not in ("OPAQUE", "TRANSPARENT"):
                    ics_transp = "OPAQUE"
                
                ics_status = str(ics_event.get("STATUS", "CONFIRMED")).upper()
                if ics_status not in ("CONFIRMED", "TENTATIVE", "CANCELLED"):
                    ics_status = "CONFIRMED"
                
                # Store raw ICS
                ics_raw = ics_event.to_ical()
                
                # For Phase 1, store events associated with the first subscription's source
                source = subscriptions[0].source
                
                # Upsert event
                upsert_event(
                    source=source,
                    external_event_id=uid,
                    external_event_hash=None,
                    title=title,
                    description=description,
                    location=location,
                    start_time=start_time,
                    end_time=end_time,
                    all_day=all_day,
                    ics_uid=uid,
                    ics_transp=ics_transp,
                    ics_status=ics_status,
                    ics_raw=ics_raw,
                    last_modified=None,
                )
                event_count += 1
            except Exception as e:
                logging.debug(f"Failed to sync event to database: {e}")
        
        logging.debug(f"Synced {event_count} events to database for {member.name}")
    except Exception as e:
        logging.warning(f"Database event sync failed for {member.name}: {e}")


def start_refresh_scheduler(
    manager: FamilyCalendarManager,
    interval_seconds: int,
    timeout_seconds: int
) -> tuple[threading.Event, threading.Thread]:
    """Start background thread to refresh calendars on schedule."""
    stop_event = threading.Event()

    def _worker() -> None:
        while not stop_event.wait(interval_seconds):
            refresh_all_calendars(manager, timeout_seconds)

    thread = threading.Thread(target=_worker, name="calendar-refresh-worker", daemon=True)
    thread.start()
    return stop_event, thread


def _normalize_dt(dt_prop) -> str | None:
    """Normalize a DTSTART/DTEND to an ISO string. Returns None if missing."""
    if dt_prop is None:
        return None
    dt = dt_prop.dt
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    if isinstance(dt, date):
        return dt.isoformat()
    return str(dt)


def _is_all_day(dtstart, dtend) -> bool:
    """Detect all-day events (DATE type, not DATETIME)."""
    if dtstart is None:
        return False
    dt = dtstart.dt
    # If it's a pure date (not datetime), it's all-day
    return isinstance(dt, date) and not isinstance(dt, datetime)


def _dt_to_date(dt_prop) -> date | None:
    """Convert a dt property to a date for range comparison."""
    if dt_prop is None:
        return None
    dt = dt_prop.dt
    if isinstance(dt, datetime):
        return dt.date()
    if isinstance(dt, date):
        return dt
    return None


def _parse_iso_value(value: str | None) -> datetime | date | None:
    """Parse normalized ISO values for basic ordering checks."""
    if not value:
        return None
    if len(value) == 10:
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _iter_events_for_range(
    cal: Calendar,
    filter_start: date | None,
    filter_end: date | None,
) -> list[Event]:
    """Return VEVENT components, expanding recurrences for bounded queries when possible."""
    if not (filter_start and filter_end):
        return list(cal.walk("VEVENT"))

    if not RECURRENCE_EXPANSION_AVAILABLE:
        return list(cal.walk("VEVENT"))

    window_start = datetime.combine(filter_start, datetime.min.time(), tzinfo=timezone.utc)
    window_end = datetime.combine(filter_end, datetime.min.time(), tzinfo=timezone.utc)

    try:
        expanded = recurring_ical_events.of(cal).between(window_start, window_end)
        return list(expanded)
    except Exception as exc:
        logging.warning("Recurring expansion failed (%s); falling back to raw VEVENTs", exc)
        return list(cal.walk("VEVENT"))


def _is_valid_event_payload(payload: dict[str, Any]) -> bool:
    """Validate extracted event payload before returning to API callers."""
    required_fields = ("member_id", "member_name", "member_color", "summary", "availability")
    for field_name in required_fields:
        value = payload.get(field_name)
        if value is None:
            return False
        if isinstance(value, str) and not value.strip() and field_name in {"member_id", "member_name", "summary"}:
            return False

    if not re.match(r"^#[0-9a-fA-F]{6}$", str(payload.get("member_color", ""))):
        return False

    start_val = payload.get("start")
    end_val = payload.get("end")
    if start_val and not re.match(r"^\d{4}-\d{2}-\d{2}", str(start_val)):
        return False
    if end_val and not re.match(r"^\d{4}-\d{2}-\d{2}", str(end_val)):
        return False

    if payload.get("availability") not in {"busy", "free", "tentative", "cancelled"}:
        return False

    # Keep payload sizes bounded for API responses.
    max_lengths = {
        "summary": 500,
        "location": 500,
        "description": MAX_EVENT_TEXT_LENGTH,
    }
    for field_name, max_len in max_lengths.items():
        value = payload.get(field_name)
        if isinstance(value, str) and len(value) > max_len:
            payload[field_name] = value[:max_len]

    return True


def _extract_events(
    cal: Calendar,
    member: FamilyMember,
    range_start: str | None = None,
    range_end: str | None = None,
) -> list[dict]:
    """Extract events from a parsed calendar with optional date range filtering."""
    filter_start = date.fromisoformat(range_start) if range_start else None
    filter_end = date.fromisoformat(range_end) if range_end else None

    member_id = _safe_text(member.id, max_len=80)
    if not member_id:
        logging.warning("Skipping event extraction for member with missing id")
        return []

    member_name = _safe_text(member.name, default=member_id, max_len=120)
    member_color = _normalize_member_color(member.color)

    events = []
    seen_event_ids: set[str] = set()
    event_components = _iter_events_for_range(cal, filter_start, filter_end)

    for event in event_components:
        dtstart = event.get("DTSTART")
        dtend = event.get("DTEND")

        start_iso = _normalize_dt(dtstart)
        if not start_iso:
            logging.debug("Skipping event without DTSTART for member %s", member_id)
            continue
        end_iso = _normalize_dt(dtend)

        # Range filter
        if filter_start or filter_end:
            ev_start = _dt_to_date(dtstart)
            ev_end = _dt_to_date(dtend) or ev_start
            if ev_start and filter_end and ev_start >= filter_end:
                continue
            if ev_end and filter_start and ev_end <= filter_start:
                continue

        status_val = _safe_text(event.get("STATUS", "CONFIRMED"), default="CONFIRMED", max_len=32).upper()
        transp_val = _safe_text(event.get("TRANSP", "OPAQUE"), default="OPAQUE", max_len=32).upper()
        if status_val not in {"CONFIRMED", "TENTATIVE", "CANCELLED"}:
            status_val = "CONFIRMED"
        if transp_val not in {"OPAQUE", "TRANSPARENT"}:
            transp_val = "OPAQUE"

        if status_val == "TENTATIVE":
            availability = "tentative"
        elif status_val == "CANCELLED":
            availability = "cancelled"
        elif transp_val == "TRANSPARENT":
            availability = "free"
        else:
            availability = "busy"

        all_day = _is_all_day(dtstart, dtend)
        start_obj = _parse_iso_value(start_iso)
        end_obj = _parse_iso_value(end_iso)
        if end_obj and start_obj and type(start_obj) is type(end_obj) and end_obj < start_obj:
            end_iso = start_iso

        recurrence_id_iso = _normalize_dt(event.get("RECURRENCE-ID"))
        instance_key = recurrence_id_iso or start_iso or ""
        event_id = f"{member_id}:{event_uid(event, source_namespace=member_id)}"
        if instance_key:
            event_id = f"{event_id}:{instance_key}"

        if event_id in seen_event_ids:
            continue
        seen_event_ids.add(event_id)

        event_payload = {
            "event_id": event_id,
            "summary": _safe_text(event.get("SUMMARY", "Untitled"), default="Untitled", max_len=500),
            "start": start_iso,
            "end": end_iso,
            "all_day": all_day,
            "location": _safe_text(event.get("LOCATION", ""), max_len=500),
            "description": _safe_text(event.get("DESCRIPTION", ""), max_len=MAX_EVENT_TEXT_LENGTH),
            "status": status_val,
            "availability": availability,
            "member_id": member_id,
            "member_name": member_name,
            "member_color": member_color,
        }

        if not _is_valid_event_payload(event_payload):
            logging.warning("Skipping invalid event payload for member %s (event_id=%s)", member_id, event_id)
            continue

        events.append(event_payload)

    return events


def create_app(manager: FamilyCalendarManager, fetch_timeout: int) -> Flask:
    """Create Flask application."""
    global USE_DATABASE
    app = Flask(__name__)
    app.json.sort_keys = False

    # Keep a stable secret key so sessions work across workers/restarts.
    configured_secret_key = os.getenv("FAMCAL_SECRET_KEY", "").strip() or os.getenv("SECRET_KEY", "").strip()
    if not configured_secret_key:
        configured_secret_key = manager.server_config.secret_key
    if not configured_secret_key:
        secret_seed = "|".join([
            str(manager.config_path.resolve()),
            manager.server_config.domain or "",
            manager.server_config.host,
            str(manager.server_config.port),
        ])
        configured_secret_key = hashlib.sha256(secret_seed.encode("utf-8")).hexdigest()
        logging.warning(
            "No explicit secret key configured; using deterministic fallback. "
            "Set FAMCAL_SECRET_KEY or server_settings.secret_key for stronger security."
        )
    app.secret_key = configured_secret_key
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    def _configured_site_password() -> str:
        env_password = os.getenv("FAMCAL_WEB_PASSWORD")
        if env_password is not None:
            return env_password
        return manager.server_config.website_password

    def _auth_enabled() -> bool:
        return bool(_configured_site_password())

    def _is_public_path(path: str) -> bool:
        if path.startswith("/static/"):
            return True
        if path in {"/login", "/logout", "/favicon.ico", "/family/calendar.ics"}:
            return True
        return bool(re.match(r"^/[a-z0-9_]+/calendar\.ics$", path))

    def _safe_next_path(raw_value: str | None) -> str:
        if not raw_value:
            return url_for("index")
        parsed = urlparse(raw_value)
        if parsed.scheme or parsed.netloc:
            return url_for("index")
        path = parsed.path or "/"
        if not path.startswith("/") or path in {"/login", "/logout"} or _is_public_path(path):
            return url_for("index")
        if parsed.query:
            return f"{path}?{parsed.query}"
        return path
    
    # Initialize database if available and enabled
    if DATABASE_AVAILABLE and USE_DATABASE:
        try:
            database_url = os.getenv("SQLALCHEMY_DATABASE_URL", "sqlite:///famcal.db")
            configure_database(app, database_url)
            logging.info(f"Database initialized: {database_url.split('@')[-1] if '@' in database_url else database_url}")
            
            # Sync configuration to database
            with app.app_context():
                _sync_config_to_database(manager)
        except Exception as e:
            logging.error(f"Failed to initialize database: {e}. Continuing with file-only mode.")
            USE_DATABASE = False

    def _request_id() -> str:
        request_id = getattr(g, "request_id", None)
        return request_id if request_id else "-"

    @app.before_request
    def attach_request_id() -> None:
        incoming_request_id = (request.headers.get("X-Request-ID") or "").strip()
        g.request_id = incoming_request_id[:120] if incoming_request_id else f"req-{uuid4().hex[:16]}"

    @app.before_request
    def require_site_password() -> Response | tuple[Response, int] | None:
        if not _auth_enabled():
            return None

        path = request.path or "/"
        if _is_public_path(path):
            return None
        if session.get("authenticated"):
            return None

        if path.startswith("/api/"):
            return jsonify({"error": "Authentication required"}), 401

        next_path = request.full_path if request.query_string else path
        return redirect(url_for("login", next=next_path))

    @app.after_request
    def add_request_id_header(response: Response) -> Response:
        request_id = _request_id()
        response.headers["X-Request-ID"] = request_id
        logging.info("rid=%s %s %s %s", request_id, request.method, request.path, response.status_code)
        return response

    @app.route("/login", methods=["GET", "POST"])
    def login() -> Response:
        """Password-only login page."""
        if not _auth_enabled():
            return redirect(url_for("index"))

        next_path = _safe_next_path(request.args.get("next"))
        if request.method == "POST":
            submitted_password = request.form.get("password", "")
            submitted_password = submitted_password[: MAX_WEBSITE_PASSWORD_LENGTH * 4]
            next_path = _safe_next_path(request.form.get("next"))

            configured_password = _configured_site_password()
            if configured_password and compare_digest(submitted_password, configured_password):
                session["authenticated"] = True
                session.permanent = True
                return redirect(next_path)

            return render_template(
                "login.html",
                error="Incorrect password. Please try again.",
                next_path=next_path,
            ), 401

        return render_template("login.html", error=None, next_path=next_path)

    @app.get("/logout")
    def logout() -> Response:
        """Clear auth session and return to login."""
        session.clear()
        return redirect(url_for("login") if _auth_enabled() else url_for("index"))

    @app.get("/")
    def index():
        """Main web interface."""
        return render_template("family_index.html", auth_enabled=_auth_enabled())

    @app.get("/admin")
    def admin():
        """Admin interface to manage calendars."""
        return render_template("admin.html", auth_enabled=_auth_enabled())

    @app.get("/api/members")
    def api_members() -> Response:
        """Get list of family members."""
        domain = manager.server_config.domain
        host = request.host if not domain else domain
        protocol = request.scheme if not domain else "https"
        
        def _has_valid_url(url: str) -> bool:
            valid, _ = validate_calendar_url(url, allow_insecure_http=True)
            return valid
        
        members_data = [
            {
                "id": member.id,
                "name": member.name,
                "color": member.color,
                "feed_url": f"{protocol}://{host}/{member.id}/calendar.ics",
                "calendar_count": len(member.calendars),
                "calendars": [
                    {
                        "name": cal.name,
                        "has_url": _has_valid_url(cal.url),
                        "show_details": cal.show_details,
                        "busy_text": cal.busy_text,
                        "url": cal.url if cal.url else "",
                        "source_type": cal.source_type,
                        "caldav_username": cal.caldav_username if cal.source_type == "caldav" else ""
                    }
                    for cal in member.calendars
                ]
            }
            for member in manager.members.values()
        ]
        combined_feed_url = f"{protocol}://{host}/family/calendar.ics"
        return jsonify({"members": members_data, "combined_feed_url": combined_feed_url})

    @app.get("/api/status")
    def api_status() -> Response:
        """Get status for all members.
        
        Uses locks to ensure status data is read consistently.
        """
        status_data = {}
        with manager.status_lock:
            for member_id, member in manager.members.items():
                status = manager.statuses[member_id]
                with manager.locks[member_id]:
                    status_data[member_id] = {
                        "name": member.name,
                        "last_refresh_utc": status.last_refresh_utc,
                        "last_error": status.last_error,
                        "merged_events": status.merged_events,
                        "duplicate_events_skipped": status.duplicate_events_skipped,
                        "successful_sources": status.successful_sources,
                        "configured_sources": status.configured_sources,
                    }
        return jsonify(status_data)

    @app.get("/api/<member_id>/status")
    def api_member_status(member_id: str) -> Response:
        """Get status for a specific member."""
        if member_id not in manager.members:
            return jsonify({"error": "Member not found"}), 404

        def _has_valid_url(url: str) -> bool:
            valid, _ = validate_calendar_url(url, allow_insecure_http=True)
            return valid

        member = manager.members[member_id]
        status = manager.statuses[member_id]

        with manager.locks[member_id]:
            return jsonify({
                "id": member.id,
                "name": member.name,
                "last_refresh_utc": status.last_refresh_utc,
                "last_error": status.last_error,
                "merged_events": status.merged_events,
                "duplicate_events_skipped": status.duplicate_events_skipped,
                "successful_sources": status.successful_sources,
                "configured_sources": status.configured_sources,
                "calendars": [
                    {
                        "name": cal.name,
                        "has_url": _has_valid_url(cal.url),
                        "show_details": cal.show_details,
                        "busy_text": cal.busy_text,
                        "url": cal.url,
                        "source_type": cal.source_type,
                        "caldav_username": cal.caldav_username if cal.source_type == "caldav" else ""
                    }
                    for cal in member.calendars
                ]
            })

    @app.get("/family/calendar.ics")
    def family_combined_feed() -> Response:
        """Serve a combined ICS feed with all family members' events, prefixed by member name.
        
        Phase 3: Generates from database if available, falls back to file-based method.
        """
        # Phase 3: Try database-first generation
        if DATABASE_AVAILABLE and USE_DATABASE and USE_DATABASE_FOR_ICS:
            try:
                ics_payload = generate_family_ics()
                logging.info(f"Combined feed generated from database: {len(ics_payload)} bytes")
                
                disposition = "attachment" if request.args.get("download") == "1" else "inline"
                return Response(
                    ics_payload,
                    mimetype="text/calendar; charset=utf-8",
                    headers={
                        "Content-Disposition": f"{disposition}; filename=family_calendar.ics",
                        "X-Calendar-Name": "Family Calendar"
                    },
                )
            except Exception as e:
                logging.error(f"Database ICS generation failed, falling back to files: {e}")
                # Fall through to file-based method
        
        # Fallback: File-based generation (Phase 1 compatibility)
        combined = Calendar()
        combined.add("prodid", "-//Family Calendar - Combined//EN")
        combined.add("version", "2.0")
        combined.add("calscale", "GREGORIAN")
        combined.add("x-wr-calname", "Family Calendar")

        seen_tzids: set[str] = set()
        total_events = 0

        for member_id, member in manager.members.items():
            output_path = manager.get_output_path(member_id)
            lock = manager.locks[member_id]
            # Use lock to ensure we read while file is in consistent state
            with lock:
                if not output_path.exists():
                    continue
                try:
                    raw = output_path.read_bytes()
                except OSError as e:
                    logging.warning(f"Failed to read {output_path}: {e}")
                    continue
            
            try:
                cal = Calendar.from_ical(raw)
            except Exception as e:
                logging.warning(f"Failed to parse ICS for {member_id}: {e}")
                continue

            # Copy timezone components
            for tz_comp in cal.walk("VTIMEZONE"):
                tzid_val = tz_comp.get("TZID")
                tzid = str(tzid_val).strip() if tzid_val else ""
                if tzid and tzid not in seen_tzids:
                    seen_tzids.add(tzid)
                    combined.add_component(tz_comp)

            # Copy events with member name prefix
            for event in cal.walk("VEVENT"):
                summary = str(event.get("SUMMARY", "Untitled"))
                event["SUMMARY"] = f"{member.name}: {summary}"
                combined.add_component(event)
                total_events += 1
        
        logging.info(f"Combined feed generated from files: {total_events} events from {len(manager.members)} members")

        disposition = "attachment" if request.args.get("download") == "1" else "inline"

        return Response(
            combined.to_ical(),
            mimetype="text/calendar; charset=utf-8",
            headers={
                "Content-Disposition": f"{disposition}; filename=family_calendar.ics",
                "X-Calendar-Name": "Family Calendar"
            },
        )

    @app.get("/<member_id>/calendar.ics")
    def member_calendar_feed(member_id: str) -> Response:
        """Serve ICS feed for a specific member.
        
        Phase 3: Generates from database if available, falls back to file-based method.
        """
        if member_id not in manager.members:
            logging.warning(f"ICS feed requested for non-existent member: {member_id}")
            return Response("Member not found", status=404, mimetype="text/plain")

        member = manager.members[member_id]
        
        # Phase 3: Try database-first generation
        if DATABASE_AVAILABLE and USE_DATABASE and USE_DATABASE_FOR_ICS:
            try:
                # Get database member
                with app.app_context():
                    db_member = DbFamilyMember.query.filter_by(member_id=member_id).first()
                    if db_member:
                        ics_payload = generate_member_ics(db_member)
                        logging.debug(f"Serving ICS feed for {member.name} from database: {len(ics_payload)} bytes")
                        
                        filename = f"{member_id}_calendar.ics"
                        disposition = "attachment" if request.args.get("download") == "1" else "inline"
                        
                        return Response(
                            ics_payload,
                            mimetype="text/calendar; charset=utf-8",
                            headers={
                                "Content-Disposition": f"{disposition}; filename={filename}",
                                "X-Calendar-Name": f"{member.name}'s Calendar"
                            },
                        )
                    else:
                        logging.warning(f"Member {member_id} not found in database, falling back to file")
            except Exception as e:
                logging.error(f"Database ICS generation failed for {member_id}, falling back to files: {e}")
                # Fall through to file-based method
        
        # Fallback: File-based generation (Phase 1 compatibility)
        output_path = manager.get_output_path(member_id)
        lock = manager.locks[member_id]

        # Use lock to ensure we read consistent data while refresh might be happening
        with lock:
            if not output_path.exists():
                logging.info(f"ICS feed requested but not ready: {member_id}")
                return Response(
                    f"Calendar for {member_id} is not ready yet.",
                    status=503,
                    mimetype="text/plain",
                )
            try:
                ics_payload = output_path.read_bytes()
            except OSError as e:
                logging.error(f"Failed to read ICS file for {member_id}: {e}")
                return Response(
                    f"Failed to read calendar for {member_id}",
                    status=500,
                    mimetype="text/plain",
                )

        filename = f"{member_id}_calendar.ics"
        disposition = "attachment" if request.args.get("download") == "1" else "inline"
        
        logging.debug(f"Serving ICS feed for {member.name} from file: {len(ics_payload)} bytes")

        return Response(
            ics_payload,
            mimetype="text/calendar; charset=utf-8",
            headers={
                "Content-Disposition": f"{disposition}; filename={filename}",
                "X-Calendar-Name": f"{member.name}'s Calendar"
            },
        )

    @app.get("/api/<member_id>/events")
    def api_member_events(member_id: str) -> Response:
        """Get events for calendar viewer."""
        if member_id not in manager.members:
            return jsonify({"error": "Member not found"}), 404

        range_start, range_start_error = _parse_date_param(request.args.get("start"), "start")
        if range_start_error:
            return jsonify({"events": [], "error": range_start_error}), 400

        range_end, range_end_error = _parse_date_param(request.args.get("end"), "end")
        if range_end_error:
            return jsonify({"events": [], "error": range_end_error}), 400

        if range_start and range_end and range_start >= range_end:
            return jsonify({"events": [], "error": "start must be earlier than end"}), 400

        output_path = manager.get_output_path(member_id)
        
        if not output_path.exists():
            return jsonify({"events": [], "error": "Calendar not ready yet"})

        try:
            with open(output_path, "rb") as f:
                cal = Calendar.from_ical(f.read())

            member = manager.members[member_id]
            events = _extract_events(cal, member, range_start, range_end)
            events.sort(key=lambda e: e["start"] or "")

            payload: dict[str, Any] = {"events": events[:API_MEMBER_EVENTS_LIMIT]}
            if len(events) > API_MEMBER_EVENTS_LIMIT:
                payload["truncated"] = True
                payload["total_events"] = len(events)
                payload["limit"] = API_MEMBER_EVENTS_LIMIT

            return jsonify(payload)

        except Exception as exc:
            logging.exception("Failed to get events for %s", member_id)
            return jsonify({"events": [], "error": str(exc)}), 500

    @app.get("/api/events")
    def api_combined_events() -> Response:
        """Get combined events for multiple members (family view).
        
        Query params:
            start: ISO date for range start (inclusive)
            end:   ISO date for range end (exclusive)
            member_ids: comma-separated member IDs (default: all)
        """
        range_start, range_start_error = _parse_date_param(request.args.get("start"), "start")
        if range_start_error:
            return jsonify({"events": [], "error": range_start_error}), 400

        range_end, range_end_error = _parse_date_param(request.args.get("end"), "end")
        if range_end_error:
            return jsonify({"events": [], "error": range_end_error}), 400

        if range_start and range_end and range_start >= range_end:
            return jsonify({"events": [], "error": "start must be earlier than end"}), 400

        member_ids_param = request.args.get("member_ids", "")
        if len(member_ids_param) > 4000:
            return jsonify({"events": [], "error": "member_ids parameter is too long"}), 400

        if member_ids_param:
            requested_ids = [mid.strip() for mid in member_ids_param.split(",") if mid.strip()]
        else:
            requested_ids = list(manager.members.keys())

        unique_requested_ids = list(dict.fromkeys(requested_ids))
        valid_ids = [mid for mid in unique_requested_ids if mid in manager.members]
        invalid_ids = [mid for mid in unique_requested_ids if mid not in manager.members]

        if member_ids_param and not valid_ids:
            return jsonify({"events": [], "error": "No valid member_ids were provided"}), 400

        if not member_ids_param:
            valid_ids = list(manager.members.keys())

        all_events = []
        partial_errors: list[str] = []
        for mid in valid_ids:
            output_path = manager.get_output_path(mid)
            if not output_path.exists():
                continue
            try:
                with open(output_path, "rb") as f:
                    cal = Calendar.from_ical(f.read())
                member = manager.members[mid]
                events = _extract_events(cal, member, range_start, range_end)
                all_events.extend(events)
            except Exception as exc:
                partial_errors.append(f"{mid}: {exc}")
                logging.warning("Failed to read events for %s: %s", mid, exc)

        all_events.sort(key=lambda e: e["start"] or "")
        payload: dict[str, Any] = {"events": all_events[:API_COMBINED_EVENTS_LIMIT]}
        if len(all_events) > API_COMBINED_EVENTS_LIMIT:
            payload["truncated"] = True
            payload["total_events"] = len(all_events)
            payload["limit"] = API_COMBINED_EVENTS_LIMIT

        if invalid_ids:
            payload["ignored_member_ids"] = invalid_ids
        if partial_errors:
            payload["partial_errors"] = partial_errors[:10]
        return jsonify(payload)

    @app.post("/api/admin/members")
    def api_admin_add_member() -> Response:
        """Add a new family member."""
        try:
            data = request.get_json(silent=True) or {}
            if not isinstance(data, dict):
                return jsonify({"success": False, "error": "Invalid JSON body"}), 400

            member_id = _safe_text(data.get("id", ""), max_len=80).lower()
            member_name = _safe_text(data.get("name", ""), max_len=120)

            if not member_id or not member_name:
                return jsonify({"success": False, "error": "ID and name are required"}), 400

            # Validate ID (alphanumeric and underscore only)
            if not re.match(r'^[a-z0-9_]+$', member_id):
                return jsonify({"success": False, "error": "ID must be lowercase alphanumeric"}), 400

            with manager.global_lock:
                if member_id in manager.members:
                    return jsonify({"success": False, "error": "Member already exists"}), 409

                # Create new member
                member = FamilyMember(id=member_id, name=member_name, calendars=[])
                manager.add_or_update_member(member)
                logging.info(f"Added new family member: {member_name} ({member_id})")
            
            manager.save_config()

            return jsonify({"success": True, "message": f"Added {member_name}"})

        except Exception as exc:
            logging.exception("Failed to add member")
            return jsonify({"success": False, "error": str(exc)}), 500

    @app.put("/api/admin/members/<member_id>")
    def api_admin_update_member(member_id: str) -> Response:
        """Update a family member's name and/or color."""
        try:
            if member_id not in manager.members:
                return jsonify({"success": False, "error": "Member not found"}), 404

            data = request.get_json(silent=True) or {}
            if not isinstance(data, dict):
                return jsonify({"success": False, "error": "Invalid JSON body"}), 400
            
            # Use member lock to prevent concurrent modifications
            lock = manager.locks[member_id]
            with lock:
                member = manager.members[member_id]
                
                old_name = member.name
                if "name" in data:
                    name = _safe_text(data["name"], max_len=120)
                    if name:
                        member.name = name
                        logging.info(f"Updated member name: {old_name} -> {member.name}")

                if "color" in data:
                    color = data["color"].strip()
                    if re.match(r'^#[0-9a-fA-F]{6}$', color):
                        member.color = color
                        logging.info(f"Updated color for {member.name}: {color}")
                    else:
                        return jsonify({"success": False, "error": "Color must be #RRGGBB hex format"}), 400

            manager.save_config()
            return jsonify({"success": True, "message": f"Updated {manager.members[member_id].name}"})

        except Exception as exc:
            logging.exception("Failed to update member")
            return jsonify({"success": False, "error": str(exc)}), 500

    @app.delete("/api/admin/members/<member_id>")
    def api_admin_delete_member(member_id: str) -> Response:
        """Delete a family member."""
        try:
            if member_id not in manager.members:
                return jsonify({"success": False, "error": "Member not found"}), 404

            with manager.global_lock:
                if member_id not in manager.members:
                    return jsonify({"success": False, "error": "Member not found"}), 404
                
                member_name = manager.members[member_id].name
                manager.remove_member(member_id)
                logging.info(f"Deleted family member: {member_name} ({member_id})")
            
            manager.save_config()

            return jsonify({"success": True, "message": f"Deleted {member_name}"})

        except Exception as exc:
            logging.exception("Failed to delete member")
            return jsonify({"success": False, "error": str(exc)}), 500

    @app.post("/api/admin/members/<member_id>/calendars")
    def api_admin_add_calendar(member_id: str) -> Response:
        """Add a calendar to a member."""
        try:
            if member_id not in manager.members:
                return jsonify({"success": False, "error": "Member not found"}), 404

            data = request.get_json(silent=True) or {}
            if not isinstance(data, dict):
                return jsonify({"success": False, "error": "Invalid JSON body"}), 400

            url = _safe_text(data.get("url", ""), max_len=MAX_CALENDAR_URL_LENGTH)
            name = _safe_text(data.get("name", ""), max_len=200)
            show_details = bool(data.get("show_details", True))
            busy_text = _safe_text(data.get("busy_text", "Busy"), default="Busy", max_len=120)
            source_type = _safe_text(data.get("source_type", "ics"), default="ics", max_len=20).lower()
            caldav_username = _safe_text(data.get("caldav_username", ""), max_len=200)
            caldav_password = str(data.get("caldav_password", "") or "").strip()

            if not url or not name:
                return jsonify({"success": False, "error": "URL and name are required"}), 400

            is_valid_url, url_error = validate_calendar_url(url)
            if not is_valid_url:
                return jsonify({"success": False, "error": url_error}), 400

            if source_type not in ALLOWED_SOURCE_TYPES:
                return jsonify({"success": False, "error": "source_type must be either 'ics' or 'caldav'"}), 400
            
            # Validate CalDAV sources have credentials
            if source_type == "caldav" and (not caldav_username or not caldav_password):
                return jsonify({"success": False, "error": "CalDAV sources require username and password"}), 400

            # Use member lock to prevent concurrent modifications
            lock = manager.locks[member_id]
            with lock:
                member = manager.members[member_id]
                show_location = bool(data.get("show_location", False))
                member.calendars.append(CalendarSource(
                    url=url,
                    name=name,
                    show_details=show_details,
                    busy_text=busy_text,
                    show_location=show_location,
                    source_type=source_type,
                    caldav_username=caldav_username,
                    caldav_password=caldav_password
                ))
                with manager.status_lock:
                    manager.statuses[member_id].configured_sources = len(member.calendars)
                
                logging.info(f"Added calendar '{name}' to {member.name}")  
            
            manager.save_config()
            
            # Refresh this member's calendar (synchronously wait for result)
            success = refresh_member_calendar(manager, member_id, fetch_timeout)
            
            if success:
                return jsonify({"success": True, "message": f"Added calendar to {manager.members[member_id].name}"})
            else:
                # Refresh failed, but calendar was added - return partial success
                error = manager.statuses[member_id].last_error or "Refresh failed"
                logging.warning(f"Added calendar but refresh failed: {error}")
                return jsonify({"success": True, "message": f"Added calendar (but refresh had error: {error})"})

        except Exception as exc:
            logging.exception("Failed to add calendar")
            return jsonify({"success": False, "error": str(exc)}), 500

    @app.delete("/api/admin/members/<member_id>/calendars/<int:cal_index>")
    def api_admin_delete_calendar(member_id: str, cal_index: int) -> Response:
        """Delete a calendar from a member."""
        try:
            if member_id not in manager.members:
                return jsonify({"success": False, "error": "Member not found"}), 404

            # Use member lock to prevent concurrent modifications
            lock = manager.locks[member_id]
            with lock:
                member = manager.members[member_id]
                
                if cal_index < 0 or cal_index >= len(member.calendars):
                    return jsonify({"success": False, "error": "Calendar not found"}), 404

                cal_name = member.calendars[cal_index].name
                del member.calendars[cal_index]
                with manager.status_lock:
                    manager.statuses[member_id].configured_sources = len(member.calendars)
                
                logging.info(f"Deleted calendar '{cal_name}' from {member.name}")
            
            manager.save_config()
            
            # Refresh this member's calendar
            success = refresh_member_calendar(manager, member_id, fetch_timeout)
            
            if success or len(manager.members[member_id].calendars) == 0:
                return jsonify({"success": True, "message": f"Deleted {cal_name}"})
            else:
                return jsonify({"success": True, "message": f"Deleted {cal_name} (refresh failed)"})

        except Exception as exc:
            logging.exception("Failed to delete calendar")
            return jsonify({"success": False, "error": str(exc)}), 500

    @app.put("/api/admin/members/<member_id>/calendars/<int:cal_index>")
    def api_admin_update_calendar(member_id: str, cal_index: int) -> Response:
        """Update a calendar's settings."""
        try:
            if member_id not in manager.members:
                return jsonify({"success": False, "error": "Member not found"}), 404

            data = request.get_json(silent=True) or {}
            if not isinstance(data, dict):
                return jsonify({"success": False, "error": "Invalid JSON body"}), 400

            # Use member lock to prevent concurrent modifications
            lock = manager.locks[member_id]
            with lock:
                member = manager.members[member_id]
                
                if cal_index < 0 or cal_index >= len(member.calendars):
                    return jsonify({"success": False, "error": "Calendar not found"}), 404

                calendar = member.calendars[cal_index]
                old_name = calendar.name
                
                if "url" in data:
                    url = _safe_text(data["url"], max_len=MAX_CALENDAR_URL_LENGTH)
                    if not url:
                        return jsonify({"success": False, "error": "URL cannot be empty"}), 400
                    is_valid_url, url_error = validate_calendar_url(url)
                    if not is_valid_url:
                        return jsonify({"success": False, "error": url_error}), 400
                    calendar.url = url
                
                if "name" in data:
                    calendar.name = _safe_text(data["name"], default="Untitled", max_len=200)
                
                if "show_details" in data:
                    calendar.show_details = bool(data["show_details"])
                
                if "busy_text" in data:
                    calendar.busy_text = _safe_text(data["busy_text"], default="Busy", max_len=120)
                
                if "show_location" in data:
                    calendar.show_location = bool(data["show_location"])
                
                if "source_type" in data:
                    source_type = _safe_text(data["source_type"], default="ics", max_len=20).lower()
                    if source_type not in ALLOWED_SOURCE_TYPES:
                        return jsonify({"success": False, "error": "source_type must be either 'ics' or 'caldav'"}), 400
                    calendar.source_type = source_type
                
                if "caldav_username" in data:
                    calendar.caldav_username = _safe_text(data["caldav_username"], max_len=200)
                
                if "caldav_password" in data:
                    calendar.caldav_password = str(data["caldav_password"] or "").strip()
                
                # Validate CalDAV sources have credentials
                if calendar.source_type == "caldav" and (not calendar.caldav_username or not calendar.caldav_password):
                    return jsonify({"success": False, "error": "CalDAV sources require username and password"}), 400
                
                logging.info(f"Updated calendar '{old_name}' in {member.name}")
            
            manager.save_config()
            
            # Refresh this member's calendar
            refresh_member_calendar(manager, member_id, fetch_timeout)

            return jsonify({"success": True, "message": f"Updated calendar"})

        except Exception as exc:
            logging.exception("Failed to update calendar")
            return jsonify({"success": False, "error": str(exc)}), 500

    @app.post("/api/admin/refresh")
    def api_admin_refresh() -> Response:
        """Manually trigger a refresh of all calendars."""
        try:
            logging.info(f"Manual refresh triggered for {len(manager.members)} members")
            refresh_all_calendars(manager, fetch_timeout)
            
            # Gather refresh results
            with manager.status_lock:
                total_events = sum(s.merged_events for s in manager.statuses.values())
                errors = [s.last_error for s in manager.statuses.values() if s.last_error]
            
            if errors:
                logging.warning(f"Refresh completed with {len(errors)} errors")
            
            return jsonify({"success": True, "message": f"Refresh completed: {total_events} events across {len(manager.members)} members"})
        except Exception as exc:
            logging.exception("Failed to refresh")
            return jsonify({"success": False, "error": str(exc)}), 500

    return app


def make_app_from_env() -> Flask:
    """
    Create a Flask app using environment variables for configuration.
    Used by WSGI servers (gunicorn, PythonAnywhere, etc.).
    """
    config_path = Path(os.getenv("FAMILY_CONFIG", DEFAULT_CONFIG_FILE)).resolve()
    fetch_timeout = int(os.getenv("FETCH_TIMEOUT_SECONDS", "30"))
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    
    # Log database availability
    if DATABASE_AVAILABLE:
        logging.info("Database integration available (Phase 1: Dual-write mode)")
        if os.getenv("FAMCAL_USE_DATABASE", "false").lower() == "true":
            logging.info("Database mode ENABLED via FAMCAL_USE_DATABASE")
        else:
            logging.info("Database mode disabled (set FAMCAL_USE_DATABASE=true to enable)")
    else:
        logging.warning("Database not available - running in file-only mode")

    if RECURRENCE_EXPANSION_AVAILABLE:
        logging.info("Recurring event expansion enabled for /api/events date-range queries")
    else:
        logging.warning("Recurring event expansion unavailable; install recurring-ical-events for complete API views")

    mgr = FamilyCalendarManager(config_path)
    if mgr.members:
        refresh_all_calendars(mgr, fetch_timeout)

    interval = mgr.server_config.refresh_interval_seconds
    start_refresh_scheduler(mgr, interval, fetch_timeout)

    return create_app(mgr, fetch_timeout)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Family Calendar Server - Multi-user ICS server with privacy controls"
    )
    parser.add_argument(
        "--config",
        default=os.getenv("FAMILY_CONFIG", DEFAULT_CONFIG_FILE),
        help="Path to family configuration JSON file"
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("LOG_LEVEL", "INFO").upper(),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log verbosity"
    )
    parser.add_argument(
        "--fetch-timeout",
        type=int,
        default=int(os.getenv("FETCH_TIMEOUT_SECONDS", "30")),
        help="HTTP timeout for fetching source calendars"
    )
    return parser.parse_args()


def main() -> None:
    """Main entry point."""
    args = parse_args()
    
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if RECURRENCE_EXPANSION_AVAILABLE:
        logging.info("Recurring event expansion enabled for /api/events date-range queries")
    else:
        logging.warning("Recurring event expansion unavailable; install recurring-ical-events for complete API views")

    # Load configuration
    config_path = Path(args.config).resolve()
    manager = FamilyCalendarManager(config_path)

    # Initial refresh (if there are any members)
    if manager.members:
        logging.info("Performing initial calendar refresh...")
        refresh_all_calendars(manager, args.fetch_timeout)

    # Start scheduler
    interval = manager.server_config.refresh_interval_seconds
    stop_event, scheduler_thread = start_refresh_scheduler(
        manager, interval, args.fetch_timeout
    )

    # Create and run Flask app
    app = create_app(manager, args.fetch_timeout)
    host = manager.server_config.host
    port = manager.server_config.port

    logging.info("=" * 70)
    logging.info("🍓 Family Calendar Server Started")
    logging.info("=" * 70)
    if manager.members:
        for member in manager.members.values():
            logging.info("  %s: http://%s:%d/%s/calendar.ics", 
                        member.name, host, port, member.id)
    else:
        logging.info("  No family members configured yet")
    logging.info("  Web interface: http://%s:%d/", host, port)
    logging.info("  Admin interface: http://%s:%d/admin", host, port)
    logging.info("=" * 70)

    try:
        app.run(host=host, port=port, threaded=True)
    finally:
        stop_event.set()
        scheduler_thread.join(timeout=2)


if __name__ == "__main__":
    main()
