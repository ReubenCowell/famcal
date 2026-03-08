# Updating to CalDAV Support

This guide explains how to update an existing Family Calendar Server deployment to add CalDAV support.

## What's New

- **CalDAV subscription support** — Connect directly to CalDAV servers for automatic syncing
- **Backward compatible** — Existing ICS URL sources continue to work unchanged
- **Per-source type configuration** — Mix ICS URLs and CalDAV sources for the same member

## Update Steps

### For Local Development

```bash
cd /path/to/famcal
source .venv/bin/activate  # if using venv
pip install -r requirements.txt  # installs caldav>=3.0.0
```

Restart your development server:

```bash
python family_calendar_server.py --config family_config.json
```

### For DigitalOcean / VPS Deployment

SSH into your server:

```bash
ssh user@your-server-ip
cd ~/famcal
source .venv/bin/activate
git pull  # if using git
pip install -r requirements.txt
sudo systemctl restart famcal
```

### For PythonAnywhere Deployment

1. Open a bash console in PythonAnywhere
2. Navigate to your project directory:
   ```bash
   cd ~/famcal
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. Reload your web app from the Web tab

### For Raspberry Pi Deployment

```bash
ssh pi@raspberrypi.local
cd ~/famcal
source .venv/bin/activate
git pull  # if using git
pip install -r requirements.txt
sudo systemctl restart famcal
```

## Verifying the Update

1. Open your admin interface: `http://your-server:8000/admin`
2. Click **+ Add Calendar** for any member
3. You should see **Source Type** options: **ICS URL** and **CalDAV Account**

## Configuration File Changes

Your existing `family_config.json` file is **fully compatible**. Old calendar sources will automatically get `source_type: "ics"` as the default.

Example of a migrated config:

```json
{
  "family_members": {
    "alex": {
      "name": "Alex",
      "color": "#0078d4",
      "calendars": [
        {
          "url": "https://calendar.google.com/calendar/ical/.../basic.ics",
          "name": "Google Calendar (ICS)",
          "show_details": true,
          "busy_text": "Busy",
          "show_location": false,
          "source_type": "ics",
          "caldav_username": "",
          "caldav_password": ""
        },
        {
          "url": "https://caldav.icloud.com/",
          "name": "iCloud Calendar (CalDAV)",
          "show_details": false,
          "busy_text": "Busy",
          "show_location": true,
          "source_type": "caldav",
          "caldav_username": "alex@icloud.com",
          "caldav_password": "xxxx-xxxx-xxxx-xxxx"
        }
      ]
    }
  },
  "server_settings": {
    "refresh_interval_seconds": 3600,
    "host": "0.0.0.0",
    "port": 8000,
    "domain": "",
    "password_hash": ""
  }
}
```

## Rollback

If you need to roll back (shouldn't be necessary, but just in case):

1. Restore your previous `family_config.json` backup
2. Uninstall caldav: `pip uninstall caldav`
3. Restore old code files
4. Restart the server

## Troubleshooting

### "No module named 'caldav'"

The caldav library wasn't installed. Run:

```bash
pip install caldav>=3.0.0
```

### Existing calendars showing errors after update

Check the server logs for specific error messages:

```bash
# systemd
sudo journalctl -u famcal -f

# Direct run
# Check console output
```

The update is backward compatible, so existing ICS sources should continue working.

### CalDAV sources not syncing

1. Check the admin UI for error messages
2. Verify CalDAV credentials are correct
3. Test the CalDAV URL directly in a CalDAV client
4. Check firewall rules allow outgoing HTTPS connections

## Security Note

**CalDAV passwords are stored in plain text** in `family_config.json`. Make sure to:

- Set proper file permissions: `chmod 600 family_config.json`
- Use app-specific passwords when available
- Keep backups secure
- Consider encrypting the file system or using environment variables (future feature)

## Questions?

See [docs/CALDAV_SUPPORT.md](CALDAV_SUPPORT.md) for detailed CalDAV usage instructions.
