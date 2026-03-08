# Family Calendar Server

A self-hosted family calendar service that merges ICS feeds from any calendar provider (Google, Outlook, Apple, etc.) into per-member subscribable feeds, with an Outlook-style web UI.

## Features

- **Outlook-style month grid** — full calendar view with colored event bars, day detail panel, and list view fallback.
- **Dynamic family members** — add/remove members and calendars entirely through the admin UI. Nothing is hardcoded.
- **Per-member ICS feeds** — each member gets a subscribable `/<member_id>/calendar.ics` URL.
- **Privacy mode** — per-calendar toggle: show full details, or show only "Busy" while preserving the event's real busy/tentative/free status from the source calendar.
- **Color-coded members** — each member is auto-assigned a distinct color, visible in pills and calendar events.
- **Combined family view** — see all members' events overlaid on one grid, with filter pills to toggle members on/off.
- **Mobile-friendly** — responsive design works on desktop, tablet, and phone.
- **Raspberry Pi ready** — includes systemd service file and setup script for headless deployment.
- **Cloud ready** — WSGI file included for PythonAnywhere, Render, or any gunicorn-based host.


## Quick Start

### 1. Clone and set up

```bash
cd ~
git clone <your-repo-url> famcal
cd famcal
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 2. Start the server

```bash
python family_calendar_server.py --config family_config.json
```

Or use the convenience script:

```bash
./start_server.sh
```

### 3. Open in a browser

| Page | URL |
|------|-----|
| Calendar viewer | `http://localhost:8000/` |
| Admin panel | `http://localhost:8000/admin` |

### 4. Add your family

1. Go to `http://localhost:8000/admin`
2. Click **+ Add Member** — enter an ID (lowercase, no spaces) and a display name
3. For each member, click **+ Add Calendar** and paste the ICS URL from Google/Outlook/Apple
4. Choose **Show event details** or uncheck for privacy mode
5. Go back to `http://localhost:8000/` to see the calendar

### 5. Subscribe to feeds

Each member's merged calendar is available at:

```
http://<your-server>:8000/<member_id>/calendar.ics
```

Subscribe to this URL in any calendar app:

- **Apple Calendar**: Settings → Accounts → Add Account → Other → Add Subscribed Calendar
- **Google Calendar**: Other calendars (+) → From URL
- **Outlook**: File → Account Settings → Internet Calendars → New


## Configuration

The server reads `family_config.json` (created automatically on first run):

```json
{
  "family_members": {
    "alex": {
      "name": "Alex",
      "color": "#0078d4",
      "calendars": [
        {
          "url": "https://calendar.google.com/...",
          "name": "Work",
          "show_details": true
        },
        {
          "url": "https://outlook.live.com/...",
          "name": "Personal",
          "show_details": false
        }
      ]
    }
  },
  "server_settings": {
    "refresh_interval_seconds": 3600,
    "host": "0.0.0.0",
    "port": 8000,
    "domain": ""
  }
}
```

| Setting | Description |
|---------|-------------|
| `refresh_interval_seconds` | How often to re-fetch source calendars (default: 1 hour) |
| `host` | Bind address (`0.0.0.0` for all interfaces) |
| `port` | HTTP port (default: 8000) |
| `domain` | Optional domain for generated feed URLs (leave empty for auto-detect) |


## Privacy Mode

Each calendar source can be set to **show details** or **privacy mode**:

| Mode | What appears | What's hidden |
|------|-------------|---------------|
| **Show details** | Full event title, location, description | Nothing |
| **Privacy mode** | "Busy" as the title | Title, location, description |

In both modes, the event's **busy/tentative/free/cancelled** status is always preserved from the source calendar — it comes from the event's `STATUS` and `TRANSP` fields, not from configuration.

The availability logic:

| Source field | Value | Displayed as |
|-------------|-------|-------------|
| `STATUS` | `TENTATIVE` | Tentative |
| `STATUS` | `CANCELLED` | Cancelled |
| `TRANSP` | `TRANSPARENT` | Free |
| (default) | — | Busy |


## API Endpoints

### Public

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

### Combined Events Query Parameters

`GET /api/events?start=2026-03-01&end=2026-04-01&member_ids=alex,ben`

| Param | Description |
|-------|-------------|
| `start` | ISO date, range start (inclusive) |
| `end` | ISO date, range end (exclusive) |
| `member_ids` | Comma-separated member IDs (default: all) |

### Admin

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/admin/members` | Add a member (`{id, name}`) |
| `DELETE` | `/api/admin/members/<id>` | Delete a member |
| `POST` | `/api/admin/members/<id>/calendars` | Add a calendar (`{name, url, show_details}`) |
| `PUT` | `/api/admin/members/<id>/calendars/<idx>` | Update calendar settings |
| `DELETE` | `/api/admin/members/<id>/calendars/<idx>` | Delete a calendar |
| `POST` | `/api/admin/refresh` | Trigger manual refresh |


## CLI Options

```
python family_calendar_server.py [OPTIONS]
```

| Option | Env var | Default | Description |
|--------|---------|---------|-------------|
| `--config` | `FAMILY_CONFIG` | `family_config.json` | Config file path |
| `--fetch-timeout` | `FETCH_TIMEOUT_SECONDS` | `30` | HTTP timeout for fetching source calendars |
| `--log-level` | `LOG_LEVEL` | `INFO` | Log verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |


## Raspberry Pi Setup

### Step-by-step

1. **Copy files to your Pi** (via `scp`, `rsync`, or `git clone`):

   ```bash
   ssh pi@raspberrypi.local
   cd ~
   git clone <your-repo-url> famcal
   cd famcal
   ```

2. **Install Python and dependencies**:

   ```bash
   sudo apt-get update
   sudo apt-get install -y python3 python3-pip python3-venv
   python3 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

3. **Test it runs**:

   ```bash
   python family_calendar_server.py --config family_config.json
   ```

   Open `http://raspberrypi.local:8000` in a browser to confirm. Press `Ctrl+C` to stop.

4. **Install as a system service** (auto-starts on boot):

   ```bash
   sudo cp famcal.service /etc/systemd/system/famcal.service
   sudo systemctl daemon-reload
   sudo systemctl enable famcal
   sudo systemctl start famcal
   ```

5. **Check it's running**:

   ```bash
   sudo systemctl status famcal
   ```

6. **View logs**:

   ```bash
   sudo journalctl -u famcal -f
   ```

### Or use the setup script

```bash
cd ~/famcal
./setup_pi.sh
sudo systemctl start famcal
```

This script copies files, installs dependencies, and registers the systemd service.


## Deploy to PythonAnywhere (Free)

If you don't have a Raspberry Pi (or just want it online), PythonAnywhere gives you free Flask hosting.

### 1. Create an account

Go to [pythonanywhere.com](https://www.pythonanywhere.com/) and sign up for a free **Beginner** account.

Your app will be available at `https://<your-username>.pythonanywhere.com`.

### 2. Upload your files

**Option A — via the web UI:**

1. Go to the **Files** tab
2. Navigate to `/home/<your-username>/`
3. Create a folder called `famcal`
4. Upload all the project files into it (including `templates/` and `static/` folders)

**Option B — via git (recommended):**

1. Go to the **Consoles** tab → start a **Bash** console
2. Run:
   ```bashio
   cd ~
   git clone <your-repo-url> famcal
   ```

### 3. Set up the virtual environment

In a PythonAnywhere Bash console:

```bash
cd ~/famcal
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Create the web app

1. Go to the **Web** tab
2. Click **Add a new web app**
3. Choose **Manual configuration** (not Flask — we need manual for WSGI)
4. Select **Python 3.10** (or whichever version matches your venv)
5. Click Next/Done

### 5. Configure WSGI

1. On the **Web** tab, find **WSGI configuration file** and click the link (e.g., `/var/www/<username>_pythonanywhere_com_wsgi.py`)
2. **Delete everything** in that file and replace it with:

   ```python
   import sys
   import os

   project_dir = '/home/<your-username>/famcal'
   if project_dir not in sys.path:
       sys.path.insert(0, project_dir)
   os.chdir(project_dir)
   os.environ['FAMILY_CONFIG'] = os.path.join(project_dir, 'family_config.json')

   from wsgi import application  # noqa
   ```

   Replace `<your-username>` with your actual PythonAnywhere username.

3. Click **Save**

### 6. Set the virtualenv path

On the **Web** tab, under **Virtualenv**, enter:

```
/home/<your-username>/famcal/.venv
```

### 7. Set static files

On the **Web** tab, under **Static files**, add:

| URL | Directory |
|-----|-----------|
| `/static/` | `/home/<your-username>/famcal/static` |

### 8. Reload

Click the green **Reload** button on the **Web** tab.

Visit `https://<your-username>.pythonanywhere.com` — your calendar is live!

### 9. Set up your calendars

Go to `https://<your-username>.pythonanywhere.com/admin` and add your family members and calendar URLs.

### PythonAnywhere notes

- **Free tier limits**: one web app, your-username.pythonanywhere.com domain, and outbound HTTP only to a whitelist of sites. Google Calendar and Outlook URLs are on the whitelist. If a calendar URL is blocked, you may need a paid account ($5/mo).
- **Scheduled refresh**: PythonAnywhere free tier doesn't run background threads reliably. Calendars refresh when the app restarts (daily on free tier) or when you click Refresh in admin. For automatic refreshes, go to the **Tasks** tab and add a scheduled task:
  ```bash
  cd ~/famcal && source .venv/bin/activate && python -c "from family_calendar_server import *; import pathlib; m=FamilyCalendarManager(pathlib.Path('family_config.json')); refresh_all_calendars(m, 30)"
  ```
- **Custom domain**: Available on paid accounts ($5/mo). Set `domain` in `family_config.json` to your domain so ICS feed URLs are correct.
- **Updating**: Pull new code (`git pull` in a Bash console) then click **Reload** on the Web tab.


## Troubleshooting

| Problem | Solution |
|---------|----------|
| "Calendar not ready yet" (HTTP 503) | Initial refresh hasn't completed. Wait a minute or trigger a manual refresh from admin. |
| Events show 0/N successful sources | The ICS URL is invalid, unreachable, or returns non-ICS data. Check the URL in a browser. |
| Can't access from another device | Make sure `host` is `0.0.0.0`, the port is open, and the firewall allows 8000. |
| No members visible | Add members in `/admin` first. |
| Calendar app won't subscribe | Ensure the URL is `http://<ip>:8000/<id>/calendar.ics` — not `localhost`. |

## Security Notes

- No authentication is built in. Run on a trusted network, or place behind a reverse proxy (e.g., nginx) with authentication.
- Calendar source URLs may contain private tokens. They are stored in `family_config.json` — keep this file secure.


## Project Structure

```
family_calendar_server.py   # Main server (Flask)
wsgi.py                     # WSGI entry point (PythonAnywhere / gunicorn)
family_config.json          # Runtime configuration (auto-created)
requirements.txt            # Python dependencies
start_server.sh             # Quick start script
setup_pi.sh                 # Raspberry Pi setup script
famcal.service              # systemd service file
static/style.css            # Outlook-inspired stylesheet
templates/
  family_index.html         # Calendar viewer (month grid + list)
  admin.html                # Admin panel
  index.html                # Legacy single-feed page
output/                     # Generated ICS files (auto-created)
```


## Legacy Single-Feed Scripts

These older scripts are still available if you want one merged feed for everyone:

- `ics_merge_server.py` — UI at `/`, feed at `/merged_calendar.ics`
- `ics_html_server.py` — HTML at `/merged_calendar.html`, feed at `/merged_calendar.ics`

They accept `--url`, `--urls-file`, and `ICS_URLS` input modes.