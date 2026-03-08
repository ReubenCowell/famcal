# API Reference

## Public Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Calendar viewer UI |
| `GET` | `/admin` | Admin UI |
| `GET` | `/api/members` | List all members with feed URLs and colors |
| `GET` | `/api/status` | Refresh status for all members |
| `GET` | `/api/<member_id>/status` | Status for one member |
| `GET` | `/api/<member_id>/events` | Events for one member (optional `?start=&end=`) |
| `GET` | `/api/events` | Combined events for multiple members |
| `GET` | `/<member_id>/calendar.ics` | Subscribable ICS feed |

## Combined Events Query Parameters

```
GET /api/events?start=2026-03-01&end=2026-04-01&member_ids=alex,ben
```

| Param | Description |
|-------|-------------|
| `start` | ISO date, range start (inclusive) |
| `end` | ISO date, range end (exclusive) |
| `member_ids` | Comma-separated member IDs (default: all) |

## Admin Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/admin/members` | Add a member (`{id, name}`) |
| `DELETE` | `/api/admin/members/<id>` | Delete a member |
| `POST` | `/api/admin/members/<id>/calendars` | Add a calendar (`{name, url, show_details}`) |
| `PUT` | `/api/admin/members/<id>/calendars/<idx>` | Update calendar settings |
| `DELETE` | `/api/admin/members/<id>/calendars/<idx>` | Delete a calendar |
| `POST` | `/api/admin/refresh` | Trigger manual refresh |

## Privacy & Availability

Each calendar source can be set to **show details** or **privacy mode**:

| Mode | What appears | What's hidden |
|------|-------------|---------------|
| **Show details** | Full event title, location, description | Nothing |
| **Privacy mode** | "Busy" as the title | Title, location, description |

In both modes, the event's **busy/tentative/free/cancelled** status is always preserved from the source calendar.

### Availability logic

| Source field | Value | Displayed as |
|-------------|-------|-------------|
| `STATUS` | `TENTATIVE` | Tentative |
| `STATUS` | `CANCELLED` | Cancelled |
| `TRANSP` | `TRANSPARENT` | Free |
| (default) | — | Busy |

## CLI Options

```
python family_calendar_server.py [OPTIONS]
```

| Option | Env var | Default | Description |
|--------|---------|---------|-------------|
| `--config` | `FAMILY_CONFIG` | `family_config.json` | Config file path |
| `--fetch-timeout` | `FETCH_TIMEOUT_SECONDS` | `30` | HTTP timeout for fetching source calendars |
| `--log-level` | `LOG_LEVEL` | `INFO` | Log verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
