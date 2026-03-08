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
import hashlib
import json
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import secrets

import requests
from flask import Flask, Response, jsonify, redirect, render_template, request, session, url_for
from icalendar import Calendar, Event
from werkzeug.security import check_password_hash, generate_password_hash

DEFAULT_CONFIG_FILE = "family_config.json"
DEFAULT_REFRESH_SECONDS = 3600

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
    password_hash: str = ""


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
        self.locks: dict[str, threading.Lock] = {}
        self.output_dir = Path("output")
        self.output_dir.mkdir(exist_ok=True)
        self.global_lock = threading.Lock()  # For config file writes
        self.load_config()

    def load_config(self) -> None:
        """Load configuration from JSON file."""
        if not self.config_path.exists():
            # Create default config
            self.save_config()
            return

        with open(self.config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        # Load family members
        self.members = {}
        member_index = 0
        for member_id, member_data in config.get("family_members", {}).items():
            calendars = [
                CalendarSource(
                    url=cal.get("url", ""),
                    name=cal.get("name", "Untitled"),
                    show_details=cal.get("show_details", True),
                    busy_text=cal.get("busy_text", "Busy")
                )
                for cal in member_data.get("calendars", [])
            ]
            color = member_data.get("color", "") or MEMBER_COLORS[member_index % len(MEMBER_COLORS)]
            self.members[member_id] = FamilyMember(
                id=member_id,
                name=member_data.get("name", member_id.capitalize()),
                calendars=calendars,
                color=color
            )
            self.statuses[member_id] = MemberStatus(configured_sources=len(calendars))
            self.locks[member_id] = threading.Lock()
            member_index += 1

        # Load server settings
        server_settings = config.get("server_settings", {})
        self.server_config = ServerConfig(
            refresh_interval_seconds=server_settings.get("refresh_interval_seconds", 3600),
            host=server_settings.get("host", "0.0.0.0"),
            port=server_settings.get("port", 8000),
            domain=server_settings.get("domain"),
            password_hash=server_settings.get("password_hash", "")
        )

        logging.info("Loaded config for %d family members", len(self.members))

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
                            "busy_text": cal.busy_text
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
                "password_hash": self.server_config.password_hash
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
            # Assign color if not set
            if not member.color:
                idx = len(self.members) % len(MEMBER_COLORS)
                member.color = MEMBER_COLORS[idx]
        
        self.members[member.id] = member
        self.statuses[member.id].configured_sources = len(member.calendars)

    def remove_member(self, member_id: str) -> None:
        """Remove a family member."""
        if member_id in self.members:
            del self.members[member_id]
            del self.statuses[member_id]
            del self.locks[member_id]
            
            # Delete output file
            output_path = self.get_output_path(member_id)
            if output_path.exists():
                output_path.unlink()


def fetch_calendar_data(url: str, timeout_seconds: int = 30) -> bytes:
    """Fetch calendar data from a URL."""
    response = requests.get(
        url,
        timeout=timeout_seconds,
        headers={"User-Agent": "family-calendar-server/1.0"},
    )
    response.raise_for_status()
    return response.content


def parse_calendar_data(raw_data: bytes, source_url: str) -> Calendar:
    """Parse ICS data into a Calendar object."""
    try:
        return Calendar.from_ical(raw_data)
    except Exception as exc:
        raise ValueError(f"Invalid ICS data from {source_url}: {exc}") from exc


def event_uid(event: Event) -> str:
    """Get or generate a UID for an event."""
    uid_value = event.get("UID")
    uid = str(uid_value).strip() if uid_value else ""

    if uid:
        return uid

    # Generate UID from event properties
    fallback_key = "|".join([
        str(event.get("SUMMARY", "")),
        str(event.get("DTSTART", "")),
        str(event.get("DTEND", "")),
        str(event.get("LOCATION", "")),
    ])
    digest = hashlib.sha1(fallback_key.encode("utf-8")).hexdigest()
    generated_uid = f"generated-{digest}@famcal"
    event["UID"] = generated_uid
    return generated_uid


def apply_privacy_to_event(event: Event, calendar_source: CalendarSource) -> Event:
    """
    Apply privacy settings to an event based on calendar source configuration.
    
    If show_details=True: Keep all event details as-is
    If show_details=False: Hide details but preserve the event's own TRANSP and STATUS
                           (busy/tentative is determined by the event itself, not config)
    """
    if calendar_source.show_details:
        # Keep all event details as-is
        return event

    # Privacy mode: hide details, show only "Busy" but keep event's transparency/status
    private_event = Event()
    
    # Copy essential fields
    private_event["UID"] = event.get("UID")
    private_event["DTSTART"] = event.get("DTSTART")
    private_event["DTEND"] = event.get("DTEND")
    private_event["DTSTAMP"] = event.get("DTSTAMP", datetime.now(timezone.utc))
    
    # Preserve the event's original TRANSP and STATUS
    # These fields indicate busy/tentative as set in the original calendar app
    if "TRANSP" in event:
        private_event["TRANSP"] = event.get("TRANSP")  # OPAQUE (busy) or TRANSPARENT (free)
    
    if "STATUS" in event:
        private_event["STATUS"] = event.get("STATUS")  # CONFIRMED, TENTATIVE, or CANCELLED
    
    # Set custom busy text
    private_event["SUMMARY"] = calendar_source.busy_text or "Busy"
    private_event["CLASS"] = "PRIVATE"
    
    # Don't copy description or location (privacy)
    
    return private_event


def merge_member_calendars(
    member: FamilyMember,
    timeout_seconds: int = 30
) -> tuple[Calendar, int, int, int]:
    """
    Merge all calendars for a family member with privacy controls.
    
    Returns: (merged_calendar, merged_events, duplicates_skipped, successful_sources)
    """
    merged = Calendar()
    merged.add("prodid", f"-//Family Calendar - {member.name}//EN")
    merged.add("version", "2.0")
    merged.add("calscale", "GREGORIAN")
    merged.add("x-wr-calname", f"{member.name}'s Calendar")

    seen_uids: set[str] = set()
    seen_tzids: set[str] = set()
    merged_events = 0
    duplicates_skipped = 0
    successful_sources = 0

    for calendar_source in member.calendars:
        if not calendar_source.url or not calendar_source.url.startswith("http"):
            logging.warning("Skipping invalid URL for %s: %s", member.name, calendar_source.name)
            continue

        try:
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
                uid = event_uid(event)
                
                if uid in seen_uids:
                    duplicates_skipped += 1
                    continue

                seen_uids.add(uid)
                
                # Apply privacy settings (preserves event's own busy/tentative status)
                processed_event = apply_privacy_to_event(event, calendar_source)
                merged.add_component(processed_event)
                merged_events += 1

            successful_sources += 1
            logging.info("Merged %s for %s", calendar_source.name, member.name)

        except Exception as exc:
            logging.warning("Failed to fetch %s for %s: %s", calendar_source.name, member.name, exc)

    return merged, merged_events, duplicates_skipped, successful_sources


def refresh_member_calendar(
    manager: FamilyCalendarManager,
    member_id: str,
    timeout_seconds: int = 30
) -> None:
    """Refresh calendar for a single family member."""
    if member_id not in manager.members:
        return

    member = manager.members[member_id]
    status = manager.statuses[member_id]
    lock = manager.locks[member_id]
    output_path = manager.get_output_path(member_id)

    try:
        merged, event_count, duplicate_count, successful = merge_member_calendars(
            member, timeout_seconds
        )

        # Save to file
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = merged.to_ical()
        temp_path = output_path.with_suffix(output_path.suffix + ".tmp")

        with lock:
            temp_path.write_bytes(payload)
            temp_path.replace(output_path)

            # Update status
            status.last_refresh_utc = datetime.now(timezone.utc).isoformat()
            status.last_error = None
            status.merged_events = event_count
            status.duplicate_events_skipped = duplicate_count
            status.successful_sources = successful

        logging.info(
            "Refreshed %s: events=%d duplicates=%d sources=%d",
            member.name, event_count, duplicate_count, successful
        )

    except Exception as exc:
        with lock:
            status.last_error = str(exc)
        logging.exception("Failed to refresh %s: %s", member.name, exc)


def refresh_all_calendars(manager: FamilyCalendarManager, timeout_seconds: int = 30) -> None:
    """Refresh calendars for all family members."""
    for member_id in list(manager.members.keys()):
        refresh_member_calendar(manager, member_id, timeout_seconds)


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


def _extract_events(
    cal: Calendar,
    member: FamilyMember,
    range_start: str | None = None,
    range_end: str | None = None,
) -> list[dict]:
    """Extract events from a parsed calendar with optional date range filtering."""
    filter_start = date.fromisoformat(range_start) if range_start else None
    filter_end = date.fromisoformat(range_end) if range_end else None

    events = []
    for event in cal.walk("VEVENT"):
        dtstart = event.get("DTSTART")
        dtend = event.get("DTEND")

        # Range filter
        if filter_start or filter_end:
            ev_start = _dt_to_date(dtstart)
            ev_end = _dt_to_date(dtend) or ev_start
            if ev_start and filter_end and ev_start >= filter_end:
                continue
            if ev_end and filter_start and ev_end <= filter_start:
                continue

        status_val = str(event.get("STATUS", "CONFIRMED")).upper()
        transp_val = str(event.get("TRANSP", "OPAQUE")).upper()

        if status_val == "TENTATIVE":
            availability = "tentative"
        elif status_val == "CANCELLED":
            availability = "cancelled"
        elif transp_val == "TRANSPARENT":
            availability = "free"
        else:
            availability = "busy"

        all_day = _is_all_day(dtstart, dtend)

        events.append({
            "summary": str(event.get("SUMMARY", "Untitled")),
            "start": _normalize_dt(dtstart),
            "end": _normalize_dt(dtend),
            "all_day": all_day,
            "location": str(event.get("LOCATION", "") or ""),
            "description": str(event.get("DESCRIPTION", "") or ""),
            "status": status_val,
            "availability": availability,
            "member_id": member.id,
            "member_name": member.name,
            "member_color": member.color,
        })

    return events


def create_app(manager: FamilyCalendarManager, fetch_timeout: int) -> Flask:
    """Create Flask application."""
    app = Flask(__name__)
    app.json.sort_keys = False
    app.secret_key = os.getenv("SECRET_KEY", secrets.token_hex(32))

    # ===== Authentication =====
    PUBLIC_PATHS = {"/login", "/static"}

    @app.before_request
    def require_login():
        """Require password for all routes except login, static files, and ICS feeds."""
        if not manager.server_config.password_hash:
            return  # No password set, skip auth
        path = request.path
        if path.startswith("/static") or path == "/login":
            return
        # Allow ICS feed URLs without auth (for calendar app subscriptions)
        if path.endswith("/calendar.ics"):
            return
        if not session.get("authenticated"):
            return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        """Login page with password-only authentication."""
        if not manager.server_config.password_hash:
            return redirect("/")
        if request.method == "POST":
            password = request.form.get("password", "")
            if check_password_hash(manager.server_config.password_hash, password):
                session["authenticated"] = True
                return redirect("/")
            return render_template("login.html", error="Incorrect password. Please try again.")
        return render_template("login.html")

    @app.get("/logout")
    def logout():
        """Clear session and redirect to login."""
        session.clear()
        return redirect("/login")

    @app.post("/api/admin/set-password")
    def api_set_password():
        """Set or update the app password."""
        data = request.get_json()
        password = data.get("password", "").strip()
        if len(password) < 4:
            return jsonify({"success": False, "error": "Password must be at least 4 characters"}), 400
        manager.server_config.password_hash = generate_password_hash(password)
        manager.save_config()
        session["authenticated"] = True
        return jsonify({"success": True, "message": "Password updated"})

    @app.post("/api/admin/remove-password")
    def api_remove_password():
        """Remove the app password (make it public)."""
        manager.server_config.password_hash = ""
        manager.save_config()
        return jsonify({"success": True, "message": "Password removed"})

    @app.get("/")
    def index():
        """Main web interface."""
        return render_template("family_index.html")

    @app.get("/admin")
    def admin():
        """Admin interface to manage calendars."""
        return render_template("admin.html")

    @app.get("/api/members")
    def api_members() -> Response:
        """Get list of family members."""
        domain = manager.server_config.domain
        host = request.host if not domain else domain
        protocol = request.scheme if not domain else "https"
        
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
                        "has_url": bool(cal.url and cal.url.startswith("http")),
                        "show_details": cal.show_details,
                        "busy_text": cal.busy_text,
                        "url": cal.url if cal.url else ""
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
        """Get status for all members."""
        status_data = {}
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
                        "has_url": bool(cal.url and cal.url.startswith("http")),
                        "show_details": cal.show_details,
                        "busy_text": cal.busy_text,
                        "url": cal.url
                    }
                    for cal in member.calendars
                ]
            })

    @app.get("/family/calendar.ics")
    def family_combined_feed() -> Response:
        """Serve a combined ICS feed with all family members' events, prefixed by member name."""
        combined = Calendar()
        combined.add("prodid", "-//Family Calendar - Combined//EN")
        combined.add("version", "2.0")
        combined.add("calscale", "GREGORIAN")
        combined.add("x-wr-calname", "Family Calendar")

        seen_tzids: set[str] = set()

        for member_id, member in manager.members.items():
            output_path = manager.get_output_path(member_id)
            lock = manager.locks[member_id]
            with lock:
                if not output_path.exists():
                    continue
                raw = output_path.read_bytes()
            try:
                cal = Calendar.from_ical(raw)
            except Exception:
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

        return Response(
            combined.to_ical(),
            mimetype="text/calendar; charset=utf-8",
            headers={
                "Content-Disposition": "inline; filename=family_calendar.ics",
                "X-Calendar-Name": "Family Calendar"
            },
        )

    @app.get("/<member_id>/calendar.ics")
    def member_calendar_feed(member_id: str) -> Response:
        """Serve ICS feed for a specific member."""
        if member_id not in manager.members:
            return Response("Member not found", status=404, mimetype="text/plain")

        output_path = manager.get_output_path(member_id)
        lock = manager.locks[member_id]

        with lock:
            if not output_path.exists():
                return Response(
                    f"Calendar for {member_id} is not ready yet.",
                    status=503,
                    mimetype="text/plain",
                )
            ics_payload = output_path.read_bytes()

        member = manager.members[member_id]
        filename = f"{member_id}_calendar.ics"

        return Response(
            ics_payload,
            mimetype="text/calendar; charset=utf-8",
            headers={
                "Content-Disposition": f"inline; filename={filename}",
                "X-Calendar-Name": f"{member.name}'s Calendar"
            },
        )

    @app.get("/api/<member_id>/events")
    def api_member_events(member_id: str) -> Response:
        """Get events for calendar viewer."""
        if member_id not in manager.members:
            return jsonify({"error": "Member not found"}), 404

        output_path = manager.get_output_path(member_id)
        
        if not output_path.exists():
            return jsonify({"events": [], "error": "Calendar not ready yet"})

        try:
            with open(output_path, "rb") as f:
                cal = Calendar.from_ical(f.read())

            member = manager.members[member_id]
            range_start = request.args.get("start")
            range_end = request.args.get("end")
            events = _extract_events(cal, member, range_start, range_end)
            events.sort(key=lambda e: e["start"] or "")

            return jsonify({"events": events[:500]})

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
        range_start = request.args.get("start")
        range_end = request.args.get("end")
        member_ids_param = request.args.get("member_ids", "")

        if member_ids_param:
            requested_ids = [mid.strip() for mid in member_ids_param.split(",") if mid.strip()]
        else:
            requested_ids = list(manager.members.keys())

        all_events = []
        for mid in requested_ids:
            if mid not in manager.members:
                continue
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
                logging.warning("Failed to read events for %s: %s", mid, exc)

        all_events.sort(key=lambda e: e["start"] or "")
        return jsonify({"events": all_events})

    @app.post("/api/admin/members")
    def api_admin_add_member() -> Response:
        """Add a new family member."""
        try:
            data = request.get_json()
            member_id = data.get("id", "").strip().lower()
            member_name = data.get("name", "").strip()

            if not member_id or not member_name:
                return jsonify({"success": False, "error": "ID and name are required"}), 400

            # Validate ID (alphanumeric and underscore only)
            if not re.match(r'^[a-z0-9_]+$', member_id):
                return jsonify({"success": False, "error": "ID must be lowercase alphanumeric"}), 400

            if member_id in manager.members:
                return jsonify({"success": False, "error": "Member already exists"}), 409

            # Create new member
            member = FamilyMember(id=member_id, name=member_name, calendars=[])
            manager.add_or_update_member(member)
            manager.save_config()

            return jsonify({"success": True, "message": f"Added {member_name}"})

        except Exception as exc:
            logging.exception("Failed to add member")
            return jsonify({"success": False, "error": str(exc)}), 500

    @app.delete("/api/admin/members/<member_id>")
    def api_admin_delete_member(member_id: str) -> Response:
        """Delete a family member."""
        try:
            if member_id not in manager.members:
                return jsonify({"success": False, "error": "Member not found"}), 404

            member_name = manager.members[member_id].name
            manager.remove_member(member_id)
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

            data = request.get_json()
            url = data.get("url", "").strip()
            name = data.get("name", "").strip()
            show_details = data.get("show_details", True)
            busy_text = data.get("busy_text", "Busy").strip() or "Busy"

            if not url or not name:
                return jsonify({"success": False, "error": "URL and name are required"}), 400

            if not url.startswith("http"):
                return jsonify({"success": False, "error": "URL must start with http:// or https://"}), 400

            member = manager.members[member_id]
            member.calendars.append(CalendarSource(url=url, name=name, show_details=show_details, busy_text=busy_text))
            manager.statuses[member_id].configured_sources = len(member.calendars)
            
            manager.save_config()
            
            # Refresh this member's calendar
            refresh_member_calendar(manager, member_id, fetch_timeout)

            return jsonify({"success": True, "message": f"Added calendar to {member.name}"})

        except Exception as exc:
            logging.exception("Failed to add calendar")
            return jsonify({"success": False, "error": str(exc)}), 500

    @app.delete("/api/admin/members/<member_id>/calendars/<int:cal_index>")
    def api_admin_delete_calendar(member_id: str, cal_index: int) -> Response:
        """Delete a calendar from a member."""
        try:
            if member_id not in manager.members:
                return jsonify({"success": False, "error": "Member not found"}), 404

            member = manager.members[member_id]
            
            if cal_index < 0 or cal_index >= len(member.calendars):
                return jsonify({"success": False, "error": "Calendar not found"}), 404

            cal_name = member.calendars[cal_index].name
            del member.calendars[cal_index]
            manager.statuses[member_id].configured_sources = len(member.calendars)
            
            manager.save_config()
            
            # Refresh this member's calendar
            refresh_member_calendar(manager, member_id, fetch_timeout)

            return jsonify({"success": True, "message": f"Deleted {cal_name}"})

        except Exception as exc:
            logging.exception("Failed to delete calendar")
            return jsonify({"success": False, "error": str(exc)}), 500

    @app.put("/api/admin/members/<member_id>/calendars/<int:cal_index>")
    def api_admin_update_calendar(member_id: str, cal_index: int) -> Response:
        """Update a calendar's settings."""
        try:
            if member_id not in manager.members:
                return jsonify({"success": False, "error": "Member not found"}), 404

            member = manager.members[member_id]
            
            if cal_index < 0 or cal_index >= len(member.calendars):
                return jsonify({"success": False, "error": "Calendar not found"}), 404

            data = request.get_json()
            calendar = member.calendars[cal_index]
            
            if "url" in data:
                url = data["url"].strip()
                if url and not url.startswith("http"):
                    return jsonify({"success": False, "error": "URL must start with http:// or https://"}), 400
                calendar.url = url
            
            if "name" in data:
                calendar.name = data["name"].strip() or "Untitled"
            
            if "show_details" in data:
                calendar.show_details = bool(data["show_details"])
            
            if "busy_text" in data:
                calendar.busy_text = data["busy_text"].strip() or "Busy"
            
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
            refresh_all_calendars(manager, fetch_timeout)
            return jsonify({"success": True, "message": "Refresh triggered"})
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
