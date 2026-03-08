#!/usr/bin/env python3
"""
Merge multiple public iCal/ICS URLs into one feed and host it with Flask.

Install:
    python3 -m venv .venv
    source .venv/bin/activate
    pip install requests icalendar flask

Run:
    python ics_merge_server.py \
        --url "https://example.com/calendar-a.ics" \
        --url "https://example.com/calendar-b.ics"

You can also provide URLs via:
    1) ICS_URLS env var (comma-separated)
    2) --urls-file path/to/urls.txt (one URL per line)

Subscribe to:
    http://<server-host>:8000/merged_calendar.ics
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import requests
from flask import Flask, Response, jsonify, render_template
from icalendar import Calendar

DEFAULT_OUTPUT_FILE = "merged_calendar.ics"
DEFAULT_REFRESH_SECONDS = 3600
DEFAULT_ROUTE = "/merged_calendar.ics"


@dataclass
class MergeStatus:
    last_refresh_utc: str | None = None
    last_error: str | None = None
    merged_events: int = 0
    duplicate_events_skipped: int = 0
    successful_sources: int = 0
    configured_sources: int = 0


def read_urls_file(urls_file: str | None) -> list[str]:
    if not urls_file:
        return []

    path = Path(urls_file)
    if not path.exists():
        raise FileNotFoundError(f"URLs file not found: {urls_file}")

    urls: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


def collect_source_urls(cli_urls: list[str], urls_file: str | None, env_urls: str) -> list[str]:
    urls: list[str] = []
    urls.extend([url.strip() for url in cli_urls if url.strip()])
    urls.extend(read_urls_file(urls_file))
    urls.extend([url.strip() for url in env_urls.split(",") if url.strip()])

    # Preserve order while removing duplicate source URLs.
    return list(dict.fromkeys(urls))


def fetch_calendar_data(url: str, timeout_seconds: int = 30) -> bytes:
    response = requests.get(
        url,
        timeout=timeout_seconds,
        headers={"User-Agent": "merged-ics-server/1.0"},
    )
    response.raise_for_status()
    return response.content


def parse_calendar_data(raw_data: bytes, source_url: str) -> Calendar:
    try:
        return Calendar.from_ical(raw_data)
    except Exception as exc:  # pragma: no cover - defensive parser guard
        raise ValueError(f"Invalid ICS data from {source_url}: {exc}") from exc


def event_uid(event) -> str:
    uid_value = event.get("UID")
    uid = str(uid_value).strip() if uid_value else ""

    if uid:
        return uid

    fallback_key = "|".join(
        [
            str(event.get("SUMMARY", "")),
            str(event.get("DTSTART", "")),
            str(event.get("DTEND", "")),
            str(event.get("LOCATION", "")),
        ]
    )
    digest = hashlib.sha1(fallback_key.encode("utf-8")).hexdigest()
    generated_uid = f"generated-{digest}@merged-ics"
    event["UID"] = generated_uid
    return generated_uid


def merge_calendars(calendars: Iterable[Calendar]) -> tuple[Calendar, int, int]:
    merged = Calendar()
    merged.add("prodid", "-//Merged ICS Feed//EN")
    merged.add("version", "2.0")
    merged.add("calscale", "GREGORIAN")

    seen_uids: set[str] = set()
    seen_tzids: set[str] = set()
    merged_events = 0
    duplicates_skipped = 0

    for calendar in calendars:
        for timezone_component in calendar.walk("VTIMEZONE"):
            tzid_value = timezone_component.get("TZID")
            tzid = str(tzid_value).strip() if tzid_value else ""
            if tzid and tzid not in seen_tzids:
                seen_tzids.add(tzid)
                merged.add_component(timezone_component)

        for event in calendar.walk("VEVENT"):
            uid = event_uid(event)
            if uid in seen_uids:
                duplicates_skipped += 1
                continue

            seen_uids.add(uid)
            merged.add_component(event)
            merged_events += 1

    return merged, merged_events, duplicates_skipped


def save_calendar_to_file(calendar: Calendar, output_path: Path, lock: threading.Lock) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = calendar.to_ical()
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")

    with lock:
        temp_path.write_bytes(payload)
        temp_path.replace(output_path)


def refresh_merged_calendar(
    urls: list[str],
    output_path: Path,
    lock: threading.Lock,
    status: MergeStatus,
    timeout_seconds: int,
) -> None:
    parsed_calendars: list[Calendar] = []

    for url in urls:
        try:
            raw_data = fetch_calendar_data(url, timeout_seconds=timeout_seconds)
            parsed = parse_calendar_data(raw_data, source_url=url)
            parsed_calendars.append(parsed)
        except Exception as exc:
            logging.warning("Skipping source %s: %s", url, exc)

    if not parsed_calendars:
        raise RuntimeError("No source calendars could be fetched or parsed.")

    merged, merged_count, duplicate_count = merge_calendars(parsed_calendars)
    save_calendar_to_file(merged, output_path, lock)

    with lock:
        status.last_refresh_utc = datetime.now(timezone.utc).isoformat()
        status.last_error = None
        status.merged_events = merged_count
        status.duplicate_events_skipped = duplicate_count
        status.successful_sources = len(parsed_calendars)

    logging.info(
        "Refresh complete: merged=%s duplicate_skips=%s successful_sources=%s",
        merged_count,
        duplicate_count,
        len(parsed_calendars),
    )


def safe_refresh(
    urls: list[str],
    output_path: Path,
    lock: threading.Lock,
    status: MergeStatus,
    timeout_seconds: int,
) -> None:
    try:
        refresh_merged_calendar(urls, output_path, lock, status, timeout_seconds)
    except Exception as exc:
        with lock:
            status.last_error = str(exc)
        logging.exception("Refresh failed: %s", exc)


def start_refresh_scheduler(
    urls: list[str],
    interval_seconds: int,
    output_path: Path,
    lock: threading.Lock,
    status: MergeStatus,
    timeout_seconds: int,
) -> tuple[threading.Event, threading.Thread]:
    stop_event = threading.Event()

    def _worker() -> None:
        while not stop_event.wait(interval_seconds):
            safe_refresh(urls, output_path, lock, status, timeout_seconds)

    thread = threading.Thread(target=_worker, name="ics-refresh-worker", daemon=True)
    thread.start()
    return stop_event, thread


def create_app(output_path: Path, lock: threading.Lock, status: MergeStatus) -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def index():
        """Serve the web interface."""
        return render_template("index.html")

    @app.get("/api/status")
    def api_status() -> Response:
        """API endpoint for status information."""
        with lock:
            body = {
                "feed_path": DEFAULT_ROUTE,
                "output_file": str(output_path),
                "last_refresh_utc": status.last_refresh_utc,
                "last_error": status.last_error,
                "merged_events": status.merged_events,
                "duplicate_events_skipped": status.duplicate_events_skipped,
                "successful_sources": status.successful_sources,
                "configured_sources": status.configured_sources,
            }
        return jsonify(body)

    @app.get(DEFAULT_ROUTE)
    def merged_calendar_feed() -> Response:
        with lock:
            if not output_path.exists():
                return Response(
                    "Merged calendar is not ready yet.",
                    status=503,
                    mimetype="text/plain",
                )
            ics_payload = output_path.read_bytes()

        return Response(
            ics_payload,
            mimetype="text/calendar; charset=utf-8",
            headers={"Content-Disposition": f"inline; filename={output_path.name}"},
        )

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge public ICS calendars and host a combined feed."
    )
    parser.add_argument(
        "--url",
        action="append",
        default=[],
        help="Source ICS URL. Pass multiple times to add more feeds.",
    )
    parser.add_argument(
        "--urls-file",
        default=os.getenv("ICS_URLS_FILE"),
        help="Path to a text file containing one source URL per line.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.getenv("REFRESH_INTERVAL_SECONDS", DEFAULT_REFRESH_SECONDS)),
        help="Refresh interval in seconds (default: 3600).",
    )
    parser.add_argument(
        "--output",
        default=os.getenv("MERGED_ICS_PATH", DEFAULT_OUTPUT_FILE),
        help="Output file path for the merged ICS file.",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("HOST", "0.0.0.0"),
        help="Host/interface to bind Flask to.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("PORT", "8000")),
        help="Port to bind Flask to.",
    )
    parser.add_argument(
        "--fetch-timeout",
        type=int,
        default=int(os.getenv("FETCH_TIMEOUT_SECONDS", "30")),
        help="HTTP timeout for fetching source calendars.",
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("LOG_LEVEL", "INFO").upper(),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log verbosity.",
    )

    args = parser.parse_args()
    env_urls = os.getenv("ICS_URLS", "")
    urls = collect_source_urls(args.url, args.urls_file, env_urls)

    if not urls:
        parser.error(
            "No source calendars provided. Use --url, --urls-file, or ICS_URLS env var."
        )

    if args.interval < 60:
        parser.error("--interval must be at least 60 seconds.")

    args.urls = urls
    return args


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    output_path = Path(args.output).resolve()
    lock = threading.Lock()
    status = MergeStatus(configured_sources=len(args.urls))

    safe_refresh(args.urls, output_path, lock, status, args.fetch_timeout)
    stop_event, scheduler_thread = start_refresh_scheduler(
        args.urls,
        args.interval,
        output_path,
        lock,
        status,
        args.fetch_timeout,
    )

    app = create_app(output_path, lock, status)
    logging.info("Serving merged calendar on http://%s:%s%s", args.host, args.port, DEFAULT_ROUTE)

    try:
        app.run(host=args.host, port=args.port, threaded=True)
    finally:
        stop_event.set()
        scheduler_thread.join(timeout=2)


if __name__ == "__main__":
    main()
