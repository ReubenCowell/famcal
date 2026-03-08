#!/usr/bin/env python3
"""
Merge multiple public iCal/ICS URLs and serve both:
1) a subscribable ICS feed
2) an HTML calendar view

Install:
    python3 -m venv .venv
    source .venv/bin/activate
    pip install requests icalendar flask

Run:
    python ics_html_server.py \
        --url "https://example.com/calendar-a.ics" \
        --url "https://example.com/calendar-b.ics"

Open in browser:
    http://<server-host>:8000/merged_calendar.html

Subscribe in calendar apps:
    http://<server-host>:8000/merged_calendar.ics
"""

from __future__ import annotations

import argparse
import logging
import os
import threading
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, render_template_string, request
from icalendar import Calendar

from ics_merge_server import (
    DEFAULT_OUTPUT_FILE,
    DEFAULT_REFRESH_SECONDS,
    MergeStatus,
    collect_source_urls,
    safe_refresh,
    start_refresh_scheduler,
)

ICS_ROUTE = "/merged_calendar.ics"
HTML_ROUTE = "/merged_calendar.html"

HTML_TEMPLATE = """<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>Merged Calendar</title>
  <style>
    :root {
      --bg: #f6f7fb;
      --panel: #ffffff;
      --text: #17202a;
      --muted: #6f7a84;
      --accent: #0b7285;
      --border: #dbe1e7;
      --warn: #b42318;
    }

    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif;
      background: linear-gradient(180deg, #f8fafc 0%, #eef2f8 100%);
      color: var(--text);
    }

    main {
      max-width: 980px;
      margin: 2rem auto;
      padding: 0 1rem 2rem;
    }

    .card {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 14px;
      box-shadow: 0 6px 22px rgba(0, 0, 0, 0.06);
      padding: 1rem 1.2rem;
    }

    h1 {
      margin: 0 0 0.5rem;
      font-size: 1.5rem;
    }

    .meta {
      margin: 0.25rem 0;
      color: var(--muted);
      font-size: 0.95rem;
    }

    code {
      background: #edf2f7;
      padding: 0.15rem 0.4rem;
      border-radius: 6px;
    }

    .error {
      margin-top: 0.8rem;
      color: var(--warn);
      font-weight: 600;
    }

    table {
      margin-top: 1rem;
      width: 100%;
      border-collapse: collapse;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 14px;
      overflow: hidden;
    }

    th,
    td {
      text-align: left;
      padding: 0.7rem 0.8rem;
      border-bottom: 1px solid #edf0f4;
      vertical-align: top;
      font-size: 0.95rem;
    }

    th {
      color: #0f3d47;
      background: #eaf4f6;
    }

    tr:last-child td {
      border-bottom: 0;
    }

    .summary {
      font-weight: 600;
      color: #0a3140;
    }

    @media (max-width: 700px) {
      th:nth-child(4),
      td:nth-child(4) {
        display: none;
      }

      main {
        margin-top: 1rem;
      }
    }
  </style>
</head>
<body>
  <main>
    <section class=\"card\">
      <h1>Merged Calendar</h1>
      <p class=\"meta\">ICS subscription URL: <code>{{ base_url }}{{ ics_route.lstrip('/') }}</code></p>
      <p class=\"meta\">Last refresh (UTC): {{ status.last_refresh_utc or 'Pending first refresh' }}</p>
      <p class=\"meta\">Merged events: {{ status.merged_events }} | Duplicate events skipped: {{ status.duplicate_events_skipped }}</p>
      {% if status.last_error %}
        <p class=\"error\">Last refresh error: {{ status.last_error }}</p>
      {% endif %}
    </section>

    {% if events %}
      <table>
        <thead>
          <tr>
            <th>Start</th>
            <th>End</th>
            <th>Event</th>
            <th>Location</th>
          </tr>
        </thead>
        <tbody>
          {% for event in events %}
            <tr>
              <td>{{ event.start }}</td>
              <td>{{ event.end or '-' }}</td>
              <td>
                <div class=\"summary\">{{ event.summary }}</div>
                {% if event.description %}
                  <div>{{ event.description }}</div>
                {% endif %}
              </td>
              <td>{{ event.location or '-' }}</td>
            </tr>
          {% endfor %}
        </tbody>
      </table>
    {% else %}
      <section class=\"card\" style=\"margin-top: 1rem;\">
        <p>No events available yet. Check source calendar URLs or wait for refresh.</p>
      </section>
    {% endif %}
  </main>
</body>
</html>
"""


def _sort_key_for_value(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)

    return datetime.max.replace(tzinfo=timezone.utc)


def _format_calendar_value(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.strftime("%Y-%m-%d %H:%M")
        return value.astimezone().strftime("%Y-%m-%d %H:%M %Z")

    if isinstance(value, date):
        return value.strftime("%Y-%m-%d (all day)")

    return ""


def read_events_from_merged_ics(
    output_path: Path,
    lock: threading.Lock,
    max_events: int,
) -> list[dict[str, str]]:
    with lock:
        if not output_path.exists():
            return []
        payload = output_path.read_bytes()

    calendar = Calendar.from_ical(payload)
    events: list[dict[str, Any]] = []

    for component in calendar.walk("VEVENT"):
        start_prop = component.get("DTSTART")
        end_prop = component.get("DTEND")
        start_raw = start_prop.dt if start_prop else None
        end_raw = end_prop.dt if end_prop else None

        events.append(
            {
                "summary": str(component.get("SUMMARY", "(No title)")),
                "description": str(component.get("DESCRIPTION", "")).replace("\\n", " "),
                "location": str(component.get("LOCATION", "")),
                "start": _format_calendar_value(start_raw),
                "end": _format_calendar_value(end_raw),
                "_sort_key": _sort_key_for_value(start_raw),
            }
        )

    events.sort(key=lambda item: item["_sort_key"])

    limited_events = events[:max_events]
    for item in limited_events:
        item.pop("_sort_key", None)

    return limited_events


def create_app(
    output_path: Path,
    lock: threading.Lock,
    status: MergeStatus,
    max_events: int,
) -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def health() -> Response:
        with lock:
            body = {
                "feed_path": ICS_ROUTE,
                "html_path": HTML_ROUTE,
                "output_file": str(output_path),
                "last_refresh_utc": status.last_refresh_utc,
                "last_error": status.last_error,
                "merged_events": status.merged_events,
                "duplicate_events_skipped": status.duplicate_events_skipped,
                "successful_sources": status.successful_sources,
                "configured_sources": status.configured_sources,
            }
        return jsonify(body)

    @app.get(ICS_ROUTE)
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

    @app.get(HTML_ROUTE)
    def merged_calendar_html() -> Response:
        events = read_events_from_merged_ics(output_path, lock, max_events=max_events)

        with lock:
            status_snapshot = {
                "last_refresh_utc": status.last_refresh_utc,
                "last_error": status.last_error,
                "merged_events": status.merged_events,
                "duplicate_events_skipped": status.duplicate_events_skipped,
            }

        return Response(
            render_template_string(
                HTML_TEMPLATE,
                events=events,
                status=status_snapshot,
                base_url=request.url_root.rstrip("/") + "/",
                ics_route=ICS_ROUTE,
            ),
            mimetype="text/html",
        )

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge public ICS calendars and serve both ICS + HTML views."
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
        help="Output path for the merged ICS file.",
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
        "--max-events",
        type=int,
        default=int(os.getenv("HTML_MAX_EVENTS", "1000")),
        help="Maximum number of events rendered on the HTML page.",
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
    if args.max_events < 1:
        parser.error("--max-events must be at least 1.")

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

    app = create_app(output_path, lock, status, max_events=args.max_events)
    logging.info("Serving ICS feed on http://%s:%s%s", args.host, args.port, ICS_ROUTE)
    logging.info("Serving HTML page on http://%s:%s%s", args.host, args.port, HTML_ROUTE)

    try:
        app.run(host=args.host, port=args.port, threaded=True)
    finally:
        stop_event.set()
        scheduler_thread.join(timeout=2)


if __name__ == "__main__":
    main()
