# Database Integration Guide

## Overview

This guide explains how to integrate the database backend into the existing `family_calendar_server.py` while maintaining backward compatibility.

---

## Architecture Overview

```
Old System:
  WebCal Feed → fetch_calendar_data() → parse_calendar_data() → 
  merge_member_calendars() → Output/*.ics file → API reads file

New System (Database-Backed):
  WebCal Feed → fetch_calendar_data() → parse_calendar_data() → 
  sync_engine.sync_calendar_source() → Database → ICS endpoint reads DB → API reads DB
```

---

## File Structure

New files created:

```
db_models.py              # SQLAlchemy models (tables, relationships)
db_init.py                # Database initialization, migrations, helpers
sync_engine.py            # WebCal sync logic (fetch, parse, deduplicate)
ics_generator.py          # ICS feed generation from database
RECOMMENDED_FIXES_DB.md   # This file - integration guide

Modified files:
family_calendar_server.py # Add database setup, update endpoints
requirements.txt          # Add database dependencies
```

---

## Step 1: Update requirements.txt

Add database dependencies:

```txt
# Existing dependencies...
requests==2.31.0
icalendar==5.0.11
flask==3.0.0
gunicorn==21.2.0

# NEW - Database support
Flask-SQLAlchemy==3.0.5
SQLAlchemy==2.0.23
psycopg2-binary==2.9.9  # PostgreSQL adapter
alembic==1.13.0         # Migrations
```

---

## Step 2: Update family_calendar_server.py

### Import database modules

Add these imports at the top after existing imports:

```python
# New database imports
from db_models import (
    CalendarSource,
    Event,
    FamilyMember,
    MemberCalendarSubscription,
    db,
)
from db_init import (
    configure_database,
    create_subscription,
    get_or_create_calendar_source,
    get_or_create_member,
)
from sync_engine import sync_calendar_source, sync_all_sources
from ics_generator import get_family_ics, get_member_ics
```

### Update app initialization

Replace the Flask app creation section:

```python
# OLD:
app = Flask(__name__, template_folder=TEMPLATE_FOLDER, static_folder=STATIC_FOLDER)

# NEW:
app = Flask(__name__, template_folder=TEMPLATE_FOLDER, static_folder=STATIC_FOLDER)

# Configure database (replaces FamilyCalendarManager file-based storage)
DATABASE_URL = os.environ.get(
    "SQLALCHEMY_DATABASE_URL",
    f"sqlite:///{Path.cwd() / 'family_calendar.db'}"
)
configure_database(app, DATABASE_URL)
```

### Update FamilyCalendarManager

The `FamilyCalendarManager` class should now interact with the database:

```python
class FamilyCalendarManager:
    """Manages calendar configuration - updated to use database."""

    def __init__(self, config_path: Path):
        self.config_path = Path(config_path)
        self.load_config()
    
    def load_config(self) -> None:
        """Load configuration from family_config.json and sync to database."""
        if not self.config_path.exists():
            logger.warning(f"Config file not found: {self.config_path}")
            return
        
        with open(self.config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        
        # Sync family members to database
        for member_id, member_data in config.get("family_members", {}).items():
            name = member_data.get("name", member_id.capitalize())
            color = member_data.get("color", "#0078d4")
            
            member = get_or_create_member(member_id, name, color)
            
            # Sync calendar sources to database
            for cal_data in member_data.get("calendars", []):
                url = cal_data.get("url", "").strip()
                if not url:
                    continue
                
                cal_name = cal_data.get("name", "Untitled")
                
                source = get_or_create_calendar_source(
                    feed_url=url,
                    name=cal_name,
                    show_details=cal_data.get("show_details", True),
                    busy_text=cal_data.get("busy_text", "Busy"),
                    show_location=cal_data.get("show_location", False),
                )
                
                # Create subscription
                create_subscription(member, source)
    
    def save_config(self) -> None:
        """Save configuration back to family_config.json."""
        # Still maintain file format for backward compatibility
        config = {
            "family_members": {},
            "server_settings": {
                "refresh_interval_seconds": 3600,
                "host": "0.0.0.0",
                "port": 8000,
            }
        }
        
        # Fetch from database
        members = FamilyMember.query.all()
        
        for member in members:
            member_data = {
                "name": member.name,
                "color": member.color,
                "calendars": [],
            }
            
            for subscription in member.subscriptions:
                source = subscription.source
                member_data["calendars"].append({
                    "url": source.feed_url,
                    "name": source.name,
                    "show_details": subscription.get_effective_show_details(),
                    "busy_text": subscription.get_effective_busy_text(),
                    "show_location": subscription.get_effective_show_location(),
                })
            
            config["family_members"][member.member_id] = member_data
        
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
    
    @property
    def members(self) -> dict:
        """Get members dict (backward compatibility)."""
        members = FamilyMember.query.all()
        return {m.member_id: m for m in members}
    
    def get_member_ids(self) -> list[str]:
        """Get all member IDs."""
        return [m.member_id for m in FamilyMember.query.all()]
```

### Update API endpoints - /api/members

```python
@app.get("/api/members")
def api_members():
    """Get all family members and their calendar feeds."""
    members = FamilyMember.query.all()
    
    result = {
        "members": [],
        "combined_feed_url": "", # Updated below
    }
    
    protocol = "https" if app.config.get("HTTPS") else "http"
    host = request.host
    
    for member in members:
        result["members"].append({
            "id": member.member_id,
            "name": member.name,
            "color": member.color,
            "feed_url": f"{protocol}://{host}/{member.member_id}/calendar.ics",
        })
    
    result["combined_feed_url"] = f"{protocol}://{host}/family/calendar.ics"
    return jsonify(result)
```

### Update API endpoints - /api/events

```python
@app.get("/api/events")
def api_events():
    """Get events within a date range, filtered by members."""
    from datetime import datetime, timedelta
    from db_init import get_events_for_date_range
    
    # Parse parameters
    start_str = request.args.get("start", "")
    end_str = request.args.get("end", "")
    member_ids_str = request.args.get("member_ids", "")
    
    try:
        start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        end = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
    except ValueError:
        return jsonify({"error": "Invalid date format"}), 400
    
    # Parse member filter
    member_ids = None
    if member_ids_str:
        member_ids = member_ids_str.split(",")
    
    # Get events from database
    events = get_events_for_date_range(start, end, member_ids)
    
    # Convert to API format
    result_events = []
    for event in events:
        # This is simplified - you'd need to apply privacy per member
        result_events.append(event.to_dict(apply_privacy=False))
    
    return jsonify({"events": result_events})
```

### Update ICS endpoints

Replace the file-based ICS endpoints with database-backed versions:

```python
@app.get("/<member_id>/calendar.ics")
def ics_member_calendar(member_id: str):
    """Get ICS feed for a specific family member."""
    # Check if member exists
    member = FamilyMember.query.filter_by(member_id=member_id).first()
    if not member:
        return "Member not found", 404
    
    # Generate ICS from database
    try:
        ics_data = get_member_ics(member_id)
        if not ics_data:
            ics_data = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n"
    except Exception as e:
        logger.error(f"Failed to generate ICS for {member_id}: {e}")
        return "Error generating calendar", 500
    
    # Handle download parameter
    disposition = "inline"
    if request.args.get("download") == "1":
        disposition = "attachment"
    
    return Response(
        ics_data,
        mimetype="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": f'{disposition}; filename="{member_id}_calendar.ics"',
        },
    )

@app.get("/family/calendar.ics")
def ics_family_calendar():
    """Get combined ICS feed for entire family."""
    try:
        ics_data = get_family_ics()
        if not ics_data:
            ics_data = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n"
    except Exception as e:
        logger.error(f"Failed to generate family ICS: {e}")
        return "Error generating calendar", 500
    
    disposition = "inline"
    if request.args.get("download") == "1":
        disposition = "attachment"
    
    return Response(
        ics_data,
        mimetype="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": f'{disposition}; filename="family_calendar.ics"',
        },
    )
```

### Add sync endpoint

Add a new endpoint to trigger sync (replaces background refresh in old system):

```python
@app.post("/api/admin/sync")
def api_admin_sync():
    """Trigger manual sync of all calendar sources."""
    try:
        sync_logs = sync_all_sources()
        
        result = {
            "synced_sources": len(sync_logs),
            "results": [log.to_dict() for log in sync_logs],
        }
        
        return jsonify(result), 200
    except Exception as e:
        logger.error(f"Sync failed: {e}")
        return jsonify({"error": str(e)}), 500
```

---

## Step 3: Background Sync

Update the background refresh scheduler to use the database sync engine:

```python
def refresh_all_calendars() -> None:
    """Refresh all calendar sources from WebCal feeds."""
    from sync_engine import sync_all_sources
    
    logger.info("Starting background refresh of all calendar sources")
    
    try:
        sync_logs = sync_all_sources()
        
        failed = sum(1 for log in sync_logs if log.status == "failed")
        partial = sum(1 for log in sync_logs if log.status == "partial")
        
        if failed > 0:
            logger.warning(f"Refresh complete: {failed} failed, {partial} partial")
        else:
            logger.info(f"Refresh complete: all sources successful")
        
    except Exception as e:
        logger.error(f"Background refresh failed: {e}")


def start_refresh_scheduler(interval_seconds: int = 3600) -> None:
    """Start background thread to refresh calendars periodically."""
    import threading
    
    def scheduler():
        while True:
            try:
                time.sleep(interval_seconds)
                refresh_all_calendars()
            except Exception as e:
                logger.error(f"Scheduler error: {e}")
    
    thread = threading.Thread(target=scheduler, daemon=True)
    thread.start()
    logger.info(f"Calendar refresh scheduler started (interval: {interval_seconds}s)")
```

---

## Step 4: Migration Path

### Phase 1: Parallel Operation (Week 1)

1. Deploy database code
2. Keep old file-based system running
3. Database reads from ICS files initially
4. Monitor sync logs in database

### Phase 2: Switchover (Week 2)

1. Verify ICS outputs match between file and database
2. Switch endpoints to read from database
3. Continue writing backup ICS files
4. Monitor for errors

### Phase 3: Database-Only (Week 3+)

1. Remove file-based ICS generation
2. Remove backup file writing
3. Archive old output files

---

## Step 5: Testing Integration

Create a test script to verify compatibility:

```python
# test_db_integration.py

import json
from pathlib import Path
from db_init import configure_database, create_test_data
from ics_generator import generate_member_ics, generate_family_ics
from flask import Flask

app = Flask(__name__)
configure_database(app, "sqlite:///:memory:")

with app.app_context():
    # Create test data
    reuben, toby, source = create_test_data()
    
    # Sync a test calendar
    from sync_engine import sync_calendar_source
    sync_log = sync_calendar_source(source)
    print(f"Sync result: {sync_log.status}")
    print(f"Events imported: {sync_log.events_imported}")
    
    # Generate ICS feeds
    reuben_ics = generate_member_ics(reuben)
    family_ics = generate_family_ics()
    
    print(f"Reuben ICS size: {len(reuben_ics)} bytes")
    print(f"Family ICS size: {len(family_ics)} bytes")
    
    # Verify ICS parsing
    from icalendar import Calendar
    reuben_cal = Calendar.from_ical(reuben_ics)
    family_cal = Calendar.from_ical(family_ics)
    
    reuben_events = list(reuben_cal.walk("VEVENT"))
    family_events = list(family_cal.walk("VEVENT"))
    
    print(f"Reuben calendar events: {len(reuben_events)}")
    print(f"Family calendar events: {len(family_events)}")
```

---

## Step 6: Environment Configuration

Set database URL for deployment:

```bash
# SQLite (development)
export SQLALCHEMY_DATABASE_URL="sqlite:///family_calendar.db"

# PostgreSQL (production)
export SQLALCHEMY_DATABASE_URL="postgresql://user:pass@localhost/family_calendar"

# Run server
python family_calendar_server.py --config family_config.json
```

---

## Backward Compatibility Checklist

- [ ] ICS endpoint `/family/calendar.ics` produces identical output
- [ ] ICS endpoint `/<member_id>/calendar.ics` produces identical output
- [ ] API `/api/members` returns same schema
- [ ] API `/api/events` returns same event format
- [ ] Privacy settings work identically
- [ ] Event deduplication prevents duplicates
- [ ] Existing ICS subscribers see no changes

---

## Monitoring & Debugging

### Check sync status

```python
from db_models import CalendarSource, SyncLog

source = CalendarSource.query.first()
logs = SyncLog.query.filter_by(source_id=source.id).order_by(
    SyncLog.sync_started_at.desc()
).limit(5).all()

for log in logs:
    print(f"{log.created_at}: {log.status} - {log.events_imported} events")
```

### Verify ICS output

```python
from ics_generator import get_member_ics, get_family_ics
from icalendar import Calendar

# Check member ICS
ics = get_member_ics("reuben")
cal = Calendar.from_ical(ics)
events = list(cal.walk("VEVENT"))
print(f"Events in feed: {len(events)}")

for event in events[:3]:
    print(f"  {event.get('summary')} - {event.get('dtstart')}")
```

### Database queries

```python
from db_models import Event, FamilyMember
from datetime import datetime, timedelta, timezone

# Recent events
now = datetime.now(timezone.utc)
week_ago = now - timedelta(days=7)

recent = Event.query.filter(
    Event.start_time >= week_ago
).order_by(Event.start_time.desc()).limit(10).all()

print(f"Recent events: {len(recent)}")

# Events per member
for member in FamilyMember.query.all():
    total_events = Event.query.filter_by(
        member_id=member.id
    ).count()
    print(f"{member.name}: {total_events} events")
```

---

## Rollback Plan

If issues arise:

1. **Keep old family_config.json and output/ directory**
2. **Deploy old fam ilyily_calendar_server.py code**
3. **API falls back to file-based responses**
4. **Database continues running for debugging**
5. **Review sync logs to find root cause**

---

## Performance Tips

1. **Index event queries:** Indexes on `start_time` and `source_id` created automatically
2. **Connection pooling:** Configured with 5-20 connections
3. **Batch syncs:** Large calendars processed in batches
4. **Query optimization:** Use `order_by(Event.start_time)` for month/week views
5. **Caching:** Consider caching ICS feeds (5min TTL) if many subscribers

---

## Next Steps

1. Install database dependencies: `pip install -r requirements.txt`
2. Create database: `python db_init.py`
3. Load existing config: Run `FamilyCalendarManager.load_config()`
4. Test endpoints locally
5. Deploy to staging with PostgreSQL
6. Verify ICS output matches production
7. Monitor sync logs for errors
8. Gradually migrate subscribers
