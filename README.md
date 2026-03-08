# Family Calendar Server

A self-hosted family calendar service that merges ICS feeds from any calendar provider (Google, Outlook, Apple, etc.) into per-member subscribable feeds, with an Outlook-style web UI. **Now supports CalDAV subscriptions** for automatic syncing from CalDAV-enabled servers!

## Features

- **Outlook-style month grid** with colored event bars, day detail panel, and list view
- **Dynamic family members** — managed entirely through the admin UI
- **Per-member ICS feeds** — subscribable `/<member_id>/calendar.ics` URLs
- **CalDAV support** — Subscribe to CalDAV calendars with automatic background syncing
- **Password-protected UI** — optional password-only login for web/admin/API access
- **Privacy mode** — per-calendar toggle: full details or "Busy" only
- **Color-coded members** with filter pills to toggle visibility
- **Mobile-friendly** responsive design


## Quick Start

```bash
git clone <your-repo-url> famcal && cd famcal
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python family_calendar_server.py --config family_config.json
```

Open `http://localhost:8000` for the calendar, `http://localhost:8000/admin` to add family members.

### Optional website password

To require a password before opening the web UI, set either:

- `server_settings.website_password` in `family_config.json`
- `FAMCAL_WEB_PASSWORD` as an environment variable (preferred)

When enabled, UI pages and `/api/*` require login. ICS feed URLs remain accessible for calendar subscriptions.

### Subscribe to feeds

Each member's merged calendar is available at:

```
http://<your-server>:8000/<member_id>/calendar.ics
```

- **Apple Calendar**: Settings → Accounts → Add Account → Other → Add Subscribed Calendar
- **Google Calendar**: Other calendars (+) → From URL
- **Outlook**: File → Account Settings → Internet Calendars → New


## Deployment

| Platform | Guide | Cost |
|----------|-------|------|
| **DigitalOcean** | [docs/DEPLOY_DIGITALOCEAN.md](docs/DEPLOY_DIGITALOCEAN.md) | $6/mo (free with GitHub Student Pack) |
| **PythonAnywhere** | [docs/DEPLOY_PYTHONANYWHERE.md](docs/DEPLOY_PYTHONANYWHERE.md) | Free tier available |
| **Raspberry Pi** | [docs/RASPBERRY_PI.md](docs/RASPBERRY_PI.md) | Just the hardware |


## Documentation

| Document | Description |
|----------|-------------|
| [API Reference](docs/API.md) | All endpoints, query parameters, privacy/availability logic, CLI options |
| [DigitalOcean Deploy](docs/DEPLOY_DIGITALOCEAN.md) | Full VPS setup with Nginx + HTTPS |
| [PythonAnywhere Deploy](docs/DEPLOY_PYTHONANYWHERE.md) | Free hosted Flask deployment |
| [Raspberry Pi Setup](docs/RASPBERRY_PI.md) | Local always-on server with systemd |


## Project Structure

```
family_calendar_server.py   # Main server (Flask)
wsgi.py                     # WSGI entry point (gunicorn / PythonAnywhere)
family_config.json          # Runtime config (auto-created)
requirements.txt            # Python dependencies
start_server.sh             # Quick-start script
setup_pi.sh                 # Raspberry Pi setup script
famcal.service              # systemd service file
static/style.css            # Outlook-inspired stylesheet
templates/
  family_index.html         # Calendar viewer (month grid + list)
  admin.html                # Admin panel
docs/
  API.md                    # API reference
  DEPLOY_DIGITALOCEAN.md    # DigitalOcean deployment guide
  DEPLOY_PYTHONANYWHERE.md  # PythonAnywhere deployment guide
  RASPBERRY_PI.md           # Raspberry Pi deployment guide
```


## Troubleshooting

| Problem | Solution |
|---------|----------|
| "Calendar not ready yet" (503) | Wait a minute or trigger manual refresh from admin |
| Events show 0/N successful sources | ICS URL is invalid or unreachable — test it in a browser |
| Can't access from another device | Ensure `host` is `0.0.0.0` and port 8000 is open |
| Calendar app won't subscribe | Use `http://<ip>:8000/<id>/calendar.ics` — not `localhost` |


## Security

- No authentication built in. Run on a trusted network or behind a reverse proxy with auth.
- `family_config.json` contains calendar URLs with private tokens — keep it secure.