# CalDAV Support

The Family Calendar Server now supports CalDAV subscriptions in addition to direct ICS URLs. This allows automatic syncing from CalDAV-enabled calendar servers.

## ICS URLs vs CalDAV: What's the Difference?

### Use **ICS URL** for:
- **Public/published calendar links** (including `webcal://` URLs)
- Static calendar exports
- Shared calendars that don't require authentication
- Example: `webcal://p164-caldav.icloud.com/published/2/...` (iCloud published calendar)
- Example: `https://calendar.google.com/calendar/ical/.../basic.ics` (Google public calendar)

### Use **CalDAV** for:
- **Private calendars requiring authentication**
- Full account access with username/password
- Multiple calendars from one account merged together
- More reliable syncing for accounts you control
- Example: Your personal iCloud calendar with app-specific password
- Example: Your work Nextcloud calendar

**Note:** `webcal://` and `webcals://` URLs are automatically converted to `https://` and work with the ICS URL source type.

## What is CalDAV?

CalDAV is a standardized protocol that allows calendar clients to access and manage calendar data on a remote server. Many calendar providers support CalDAV, including:

- **Apple iCloud Calendar**
- **Google Calendar**
- **Nextcloud / ownCloud**
- **FastMail**
- **Radicale**
- **Synology Calendar**
- **Lark/Feishu**
- Most self-hosted calendar servers

## How It Works

Instead of providing a static ICS URL, you provide:
1. CalDAV server URL
2. Username
3. Password (or app-specific password)

The server will:
1. Connect to the CalDAV server using your credentials
2. Fetch all calendars from your account
3. Retrieve all events from those calendars
4. Convert them to ICS format
5. Merge them with your other calendar sources
6. Automatically refresh on the configured schedule (default: every hour)

## Adding a CalDAV Calendar

### Via Admin UI

1. Go to `/admin` in your browser
2. Find the family member you want to add a calendar to
3. Click **+ Add Calendar**
4. Select **CalDAV Account** as the source type
5. Fill in:
   - **Calendar Name**: A friendly name (e.g., "Work Calendar")
   - **CalDAV Server URL**: Your CalDAV server URL
   - **CalDAV Username**: Your username or email
   - **CalDAV Password**: Your password or app-specific password
6. Configure privacy settings (show details vs. busy-only)
7. Click **Add Calendar**

### Finding CalDAV Server URLs

#### Apple iCloud
- URL: `https://caldav.icloud.com/`
- Username: Your Apple ID email
- Password: App-specific password (generate at appleid.apple.com)

#### Google Calendar
- URL: `https://apidata.googleusercontent.com/caldav/v2/USERNAME@gmail.com/events`
- Replace `USERNAME@gmail.com` with your Google email
- Username: Your Google email
- Password: App-specific password (generate at myaccount.google.com/apppasswords)
  - Requires 2-factor authentication enabled

#### Nextcloud/ownCloud
- URL: `https://your-server.com/remote.php/dav`
- Username: Your Nextcloud username
- Password: Your Nextcloud password or app password

#### FastMail
- URL: `https://caldav.fastmail.com/`
- Username: Your FastMail email
- Password: Your FastMail password or app password

#### Synology Calendar
- URL: `https://your-synology.example.com:5006/`
- Username: Your DSM username
- Password: Your DSM password

## Security Considerations

- **Passwords are stored in plain text** in `family_config.json` — keep this file secure with appropriate file permissions
- Consider using **app-specific passwords** instead of your main account password
- Run the server on a trusted network or behind authentication
- Consider using HTTPS with a reverse proxy (see deployment docs)

## Troubleshooting

### "CalDAV sources require username and password"
Make sure you've entered both username and password fields when adding a CalDAV source.

### "Failed to fetch CalDAV data"
Check:
- Server URL is correct and accessible
- Username and password are correct
- Server supports CalDAV protocol
- Firewall isn't blocking outgoing connections

### Server logs showing connection errors
- Check the server logs: `sudo journalctl -u famcal -f` (systemd) or console output
- Look for specific error messages from the CalDAV library
- Verify credentials and server URL

### Events not updating
- CalDAV sources refresh on the same schedule as ICS sources (default: every hour)
- Trigger a manual refresh from the admin UI to force an immediate sync
- Check the "Last Updated" timestamp in the admin UI

## Technical Details

### Background Sync
CalDAV sources are fetched during the regular refresh cycle (controlled by `refresh_interval_seconds` in config). The server:
1. Connects to the CalDAV server
2. Discovers the principal (user account)
3. Lists all calendars for that principal
4. Fetches all events from all calendars
5. Merges events into a single ICS feed
6. Applies privacy settings (if configured)

### Compatibility
This uses the Python `caldav` library (v3.0+) which supports:
- RFC 4791 (CalDAV)
- RFC 5545 (iCalendar)
- RFC 6638 (CalDAV Scheduling)

### Performance
- Initial sync may take longer for accounts with many events
- Subsequent syncs reuse connections where possible
- Failed syncs are logged but don't stop other calendar sources from syncing
- Each CalDAV source is fetched independently

## Configuration File Format

When you add a CalDAV source, it's stored in `family_config.json` like this:

```json
{
  "url": "https://caldav.example.com/",
  "name": "My Work Calendar",
  "source_type": "caldav",
  "caldav_username": "user@example.com",
  "caldav_password": "my-password-or-token",
  "show_details": true,
  "busy_text": "Busy"
}
```

## Migration from ICS URLs

If you're currently using an ICS export URL from a CalDAV-capable service, you can switch to native CalDAV for better reliability:

1. Edit the existing calendar source
2. Change source type from "ICS URL" to "CalDAV Account"
3. Enter the CalDAV server URL (not the ICS export URL)
4. Enter your username and password
5. Save changes

The server will automatically start using CalDAV on the next refresh cycle.
