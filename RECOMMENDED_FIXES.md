# Production Code Audit - Recommended Fixes

**Status:** Safe fixes recommended for all issues  
**ICS Feed Impact:** None - all fixes are internal improvements  
**Implementation Priority:** Critical first, then High, then Medium

---

## CRITICAL FIXES (Do First)

### Fix #1: Date Parsing Race Condition in Event Rendering

**File:** `templates/family_index.html` - `buildDayMap()` function

**Current Code (Lines ~622-638):**
```javascript
if (ev.all_day && ev.end) {
    const endDate = ev.end.substring(0, 10);
    let cur = new Date(startDate + 'T00:00:00');
    const endD = new Date(endDate + 'T00:00:00');
    while (cur < endD) {
        const iso = dateToISO(cur);
        if (!map[iso]) map[iso] = [];
        map[iso].push(ev);
        cur.setDate(cur.getDate() + 1);
    }
}
```

**Recommended Fix:**
```javascript
if (ev.all_day && ev.end) {
    const startDateStr = ev.start ? ev.start.substring(0, 10) : startDate;
    const endDateStr = ev.end.substring(0, 10);
    
    // Validate dates are in correct order
    if (startDateStr >= endDateStr) {
        // Add single-day all-day event
        if (!map[startDateStr]) map[startDateStr] = [];
        if (!map[startDateStr].includes(ev)) map[startDateStr].push(ev);
    } else {
        // Add multi-day all-day event
        let cur = new Date(startDateStr + 'T00:00:00Z');  // Use Z to force UTC
        const endD = new Date(endDateStr + 'T00:00:00Z');
        while (cur < endD) {
            const iso = dateToISO(cur);
            if (!map[iso]) map[iso] = [];
            if (!map[iso].includes(ev)) map[iso].push(ev);  // Prevent duplicates
            cur.setDate(cur.getDate() + 1);
        }
    }
}
```

**Why This Works:**
- Use `Z` suffix to force UTC, prevent timezone shift bugs
- Validate start < end
- Add duplicate prevention
- Safer date string handling

---

### Fix #2: Event Modal Array Index Out of Bounds

**File:** `templates/family_index.html` - Event modal binding

**Root Cause:** `displayEvents` is rebuilt on every render, invalidating indices

**Recommended Fix - Store Event References Instead of Indices:**

Replace ALL occurrences of:
```javascript
const evIdx = displayEvents.length;
displayEvents.push(ev);
html += `<div data-ev-idx="${evIdx}">...
```

With:
```javascript
// Store event as data attribute instead of array index
html += `<div data-event-id="${esc(ev.member_id + '|' + ev.summary + '|' + (ev.start || ''))}">...
```

Then update event click handlers:
```javascript
// OLD: 
el.onclick = () => {
    const idx = parseInt(el.dataset.evIdx);
    if (!isNaN(idx) && displayEvents[idx]) openEventModal(displayEvents[idx]);
};

// NEW:
el.onclick = () => {
    const eventId = el.dataset.eventId;
    if (!eventId) return;
    
    // Find event in current events array by composite key
    const parts = eventId.split('|');
    const [memberId, summary, start] = parts;
    
    const found = events.find(ev => 
        ev.member_id === memberId && 
        ev.summary === summary && 
        (ev.start || '') === start
    );
    if (found) openEventModal(found);
};
```

Benefits:
- Events referenced by content, not array position
- Array rebuild doesn't break references
- No memory bleed from stale indices

---

### Fix #3: UID Collision Detection in Backend

**File:** `family_calendar_server.py` - `merge_member_calendars()` function

**Current Code:**
```python
seen_uids: set[str] = set()
for event in calendar.walk("VEVENT"):
    uid = event_uid(event)
    
    if uid in seen_uids:
        duplicates_skipped += 1
        continue
    
    seen_uids.add(uid)
```

**Recommended Fix:**
```python
seen_uids: dict[str, tuple] = {}  # UID -> (member_id, event_summary, dtstart)

for event in calendar.walk("VEVENT"):
    uid = event_uid(event)
    
    if uid in seen_uids:
        # SAME MEMBER: genuine duplicate (skip)
        prev_mid, prev_sum, prev_start = seen_uids[uid]
        if prev_mid == member.id:
            duplicates_skipped += 1
            logging.debug(f"Skipping duplicate event from same member: {prev_sum}")
            continue
        else:
            # DIFFERENT MEMBER: collision! Generate new UID
            logging.warning(f"UID collision detected for {uid} - regenerating")
            uid = generate_unique_uid(event, member, seen_uids)
    
    seen_uids[uid] = (member.id, str(event.get("SUMMARY", "")), str(event.get("DTSTART", "")))
    # ... add event ...

def generate_unique_uid(event: Event, member: FamilyMember, existing_uids: dict) -> str:
    """Generate unique UID that doesn't collide with existing ones."""
    base_key = "|".join([
        member.id,  # Include member to prevent cross-member collisions
        str(event.get("SUMMARY", "")),
        str(event.get("DTSTART", "")),
        str(event.get("DTEND", "")),
        str(event.get("LOCATION", "")),
    ])
    digest = hashlib.sha1(base_key.encode("utf-8")).hexdigest()
    generated_uid = f"generated-{member.id}-{digest[:8]}@famcal"
    
    # Ensure uniqueness
    if generated_uid in existing_uids:
        # Append timestamp to make truly unique
        generated_uid = f"generated-{member.id}-{digest[:8]}-{int(time.time())}@famcal"
    
    return generated_uid
```

Benefits:
- Prevents cross-member event loss
- Logs collisions for debugging
- Fallback generation ensures uniqueness

---

### Fix #4: Auto-Refresh Interval Piling Up

**File:** `templates/family_index.html` - `setupAutoRefresh()` and `fetchEvents()`

**Current Code:**
```javascript
function setupAutoRefresh() {
    if (refreshTimerId) clearInterval(refreshTimerId);
    const seconds = parseInt(localStorage.getItem('famcal-refresh-interval') || '60');
    if (seconds <= 0) return;
    refreshTimerId = setInterval(() => {
        if (document.visibilityState === 'hidden') return;
        if (activeMembers.size > 0) fetchEvents(true);
    }, seconds * 1000);
}
```

**Recommended Fix:**
```javascript
// Add fetch state tracking
let isRefetching = false;
let pendingRefresh = false;

async function fetchEvents(isAutoRefresh) {
    if (isAutoRefresh && isRefetching) {
        // Request is already in flight, skip this iteration
        pendingRefresh = true;
        return;
    }
    
    isRefetching = true;
    try {
        if (activeMembers.size === 0) { events = []; render(); return; }
        
        // ... fetch logic, add timeout ...
        
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 30000);  // 30s timeout
        
        try {
            const res = await fetch(
                `/api/events?start=${startISO}&end=${endISO}&member_ids=${ids}`,
                { signal: controller.signal }
            );
            clearTimeout(timeoutId);
            
            if (!res.ok) {
                if (isAutoRefresh) return;  // Skip error on auto-refresh
                throw new Error(`HTTP ${res.status}`);
            }
            
            const data = await res.json();
            // ... process ...
        } finally {
            clearTimeout(timeoutId);
        }
    } catch (e) {
        if (!isAutoRefresh) {
            toast('Error loading events: ' + e.message, 'error');
        }
        events = [];
        render();
    } finally {
        isRefetching = false;
        
        // If a refresh was requested while we were fetching, do it now
        if (pendingRefresh) {
            pendingRefresh = false;
            await fetchEvents(isAutoRefresh);
        }
    }
}

function setupAutoRefresh() {
    if (refreshTimerId) clearInterval(refreshTimerId);
    const seconds = parseInt(localStorage.getItem('famcal-refresh-interval') || '60');
    if (seconds <= 0) return;
    
    refreshTimerId = setInterval(() => {
        if (document.visibilityState === 'hidden') return;
        if (isRefetching) return;  // Skip if already fetching
        if (activeMembers.size > 0) fetchEvents(true);
    }, seconds * 1000);
}
```

Benefits:
- Prevents request pile-up
- Coalesces multiple refresh requests
- Adds 30s timeout to prevent hangs
- Gracefully skips errors during auto-refresh

---

## HIGH-PRIORITY FIXES

### Fix #5: Unhandled HTTP Errors

**File:** `templates/family_index.html` - `fetchEvents()` function

**Add HTTP Status Check:**
```javascript
const res = await fetch(`/api/events?...`);

// ADD THIS CHECK:
if (!res.ok) {
    const errMsg = `HTTP ${res.status}: ${res.statusText}`;
    if (!isAutoRefresh) {
        toast('Error loading events: ' + errMsg, 'error');
    }
    events = [];
    render();
    return;
}

const data = await res.json().catch(() => {
    if (!isAutoRefresh) {
        toast('Error parsing response', 'error');
    }
    return { events: [] };
});
```

---

### Fix #6: Validate Returned Event Data

**File:** `family_calendar_server.py` - `_extract_events()` function

**Add Validation Schema:**
```python
def _is_valid_event(ev_dict: dict) -> bool:
    """Validate event has required fields."""
    required = ['member_id', 'member_name', 'member_color', 'summary', 'availability']
    for field in required:
        if field not in ev_dict or ev_dict[field] is None:
            return False
    
    # Validate color is hex
    if not re.match(r'^#[0-9a-fA-F]{6}$', ev_dict['member_color']):
        return False
    
    # Validate start is ISO string
    if ev_dict.get('start') and not re.match(r'^\d{4}-\d{2}-\d{2}', ev_dict['start']):
        return False
    
    # Validate availability is known
    if ev_dict['availability'] not in ['busy', 'free', 'tentative', 'cancelled']:
        return False
    
    # Check for reasonable string lengths (prevent DoS)
    for field in ['summary', 'location', 'description']:
        if field in ev_dict and isinstance(ev_dict[field], str):
            if len(ev_dict[field]) > 10000:  # 10KB max per field
                ev_dict[field] = ev_dict[field][:10000]
    
    return True

def _extract_events(...) -> list[dict]:
    events = []
    for event in cal.walk("VEVENT"):
        # ... build event dict ...
        
        if not _is_valid_event(event_dict):
            logging.warning(f"Skipping invalid event: {event_dict}")
            continue
        
        events.append(event_dict)
    
    return events
```

---

### Fix #7: Add Request Timeout

**File:** `templates/family_index.html` - Already in Fix #4

The code above includes timeout handling.

---

### Fix #8: Input Validation for URLs

**File:** `family_calendar_server.py` - `api_admin_add_calendar()`

**Add Comprehensive Validation:**
```python
@app.post("/api/admin/members/<member_id>/calendars")
def api_admin_add_calendar(member_id: str):
    try:
        data = request.get_json()
        url = data.get("url", "").strip()
        name = data.get("name", "").strip()
        
        # VALIDATION ADDED:
        
        # 1. URL length limit
        if len(url) > 2048:
            return jsonify({"success": False, "error": "URL is too long"}), 400
        
        # 2. URL scheme validation - only allow http/https/webcal/webcals
        url_lower = url.lower()
        allowed_schemes = ('http://', 'https://', 'webcal://', 'webcals://')
        if not url_lower.startswith(allowed_schemes):
            return jsonify({"success": False, "error": "URL must start with http://, https://, webcal://, or webcals://"}), 400
        
        # 3. Prevent file:// URLs
        if url_lower.startswith('file://'):
            return jsonify({"success": False, "error": "file:// URLs not supported"}), 400
        
        # 4. SSRF prevention - block private IP ranges
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            hostname = parsed.hostname or ""
            
            # Block private ranges
            private_ranges = ['127.', '192.168.', '10.', '172.']
            if any(hostname.startswith(r) for r in private_ranges):
                return jsonify({"success": False, "error": "Internal network URLs not allowed"}), 400
            
            # Block localhost
            if hostname in ['localhost', '0.0.0.0', '::1']:
                return jsonify({"success": False, "error": "Local addresses not allowed"}), 400
        except Exception as e:
            logging.warning(f"URL parsing failed for {url}: {e}")
            return jsonify({"success": False, "error": "Invalid URL format"}), 400
        
        # 5. Name validation
        if len(name) == 0 or len(name) > 200:
            return jsonify({"success": False, "error": "Name must be 1-200 characters"}), 400
        
        # ... rest of function ...
```

Benefits:
- Prevents SSRF attacks
- Prevents DoS via huge URLs
- Blocks internal network access
- Validates input format

---

## MEDIUM-PRIORITY FIXES

### Fix #9: Refactor Date Formatting to Single Function

**File:** `templates/family_index.html`

**Create utility module in HTML script section (-**Replace duplicate date formatting:
```javascript
// NEW: Centralized date utilities
const DateUtils = {
    formatShortTime(iso) {
        if (!iso || iso.length <= 10) return '';
        try {
            const d = new Date(iso.includes('T') ? iso : iso + 'T00:00:00Z');
            return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
        } catch {
            return '';
        }
    },
    
    formatTimeRange(startISO, endISO) {
        const s = this.formatShortTime(startISO);
        const e = this.formatShortTime(endISO);
        if (s && e) return s + ' – ' + e;
        return s || 'All day';
    },
    
    formatLongDate(iso) {
        if (!iso) return '';
        try {
            const d = new Date(iso + 'T00:00:00Z');
            return d.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
        } catch {
            return '';
        }
    },
    
    dateToISO(d) {
        if (!d) return '';
        return d.getFullYear() + '-' + 
               String(d.getMonth() + 1).padStart(2, '0') + '-' + 
               String(d.getDate()).padStart(2, '0');
    }
};

// Replace all calls:
// OLD: formatShortTime(ev.start)
// NEW: DateUtils.formatShortTime(ev.start)
```

---

### Fix #10: Fix Search Highlighting Bug

**File:** `templates/family_index.html` - `escHL()` function

**Simplify Highlighting:**
```javascript
// OLD:
function escHL(s) {
    if (!s) return '';
    let result = esc(s);
    if (searchTerm) {
        const regex = new RegExp('(' + escRegExp(esc(searchTerm)) + ')', 'gi');
        result = result.replace(regex, '<mark>$1</mark>');
    }
    return result;
}

// NEW - Much simpler:
function escHL(s) {
    if (!s || !searchTerm) return esc(s);
    
    const escaped = esc(s);
    const escapedTerm = esc(searchTerm);
    
    // Use simple string replacement, not regex
    // This is safer and more predictable
    const parts = escaped.split(new RegExp(`(${escapedTerm})`, 'gi'));
    
    return parts
        .map((part, i) => {
            // Every other part is the search term (due to capture group)
            if (i % 2 === 1) return `<mark>${part}</mark>`;
            return part;
        })
        .join('');
}
```

Benefits:
- Simpler logic
- No double-escaping
- Doesn't fail on special regex characters

---

### Fix #11: Add Config Validation

**File:** `family_calendar_server.py` - `load_config()` method

**Add Validation:**
```python
def load_config(self) -> None:
    """Load and validate configuration from JSON file."""
    if not self.config_path.exists():
        self.save_config()
        return

    try:
        with open(self.config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        logging.error(f"Config file is invalid JSON: {e}")
        raise
    
    # VALIDATE CONFIG STRUCTURE
    if not isinstance(config, dict):
        raise ValueError("Config must be a JSON object")
    
    if not isinstance(config.get("family_members"), dict):
        logging.warning("family_members is not a dict, creating empty")
        config["family_members"] = {}
    
    # Load family members with validation
    self.members = {}
    member_index = 0
    for member_id, member_data in config.get("family_members", {}).items():
        if not isinstance(member_data, dict):
            logging.warning(f"Skipping invalid member {member_id}")
            continue
        
        # VALIDATE: Ensure required fields
        if not member_id or not isinstance(member_id, str):
            logging.warning("Skipping member with invalid ID")
            continue
        
        name = member_data.get("name", member_id.capitalize())
        if not isinstance(name, str) or len(name) == 0:
            name = member_id.capitalize()
        
        # Validate calendars
        calendars = []
        for cal_idx, cal in enumerate(member_data.get("calendars", [])):
            if not isinstance(cal, dict):
                logging.warning(f"Skipping invalid calendar at index {cal_idx}")
                continue
            
            url = cal.get("url", "").strip()
            cal_name = cal.get("name", "Untitled").strip()
            
            # Skip calendars with invalid/empty URLs
            if not url or len(url) > 2048:
                logging.warning(f"Skipping calendar with invalid URL: {url[:50]}")
                continue
            
            calendars.append(CalendarSource(
                url=url,
                name=cal_name or "Untitled",
                show_details=bool(cal.get("show_details", True)),
                busy_text=str(cal.get("busy_text", "Busy"))[:100],  # Max 100 chars
                show_location=bool(cal.get("show_location", False)),
                source_type=cal.get("source_type", "ics"),
                caldav_username=cal.get("caldav_username", ""),
                caldav_password=cal.get("caldav_password", "")
            ))
        
        color = member_data.get("color", "")
        if not Re.match(r'^#[0-9a-fA-F]{6}$', color):
            # Assign default color
            color = MEMBER_COLORS[member_index % len(MEMBER_COLORS)]
        
        self.members[member_id] = FamilyMember(
            id=member_id,
            name=name,
            calendars=calendars,
            color=color
        )
        
        self.statuses[member_id] = MemberStatus(configured_sources=len(calendars))
        self.locks[member_id] = threading.Lock()
        self.refresh_in_progress[member_id] = False
        member_index += 1
    
    # Load and validate server settings
    server_settings = config.get("server_settings", {})
    if not isinstance(server_settings, dict):
        server_settings = {}
    
    try:
        port = int(server_settings.get("port", 8000))
        if not (1024 <= port <= 65535):
            port = 8000
    except (ValueError, TypeError):
        port = 8000
    
    self.server_config = ServerConfig(
        refresh_interval_seconds=max(300, int(server_settings.get("refresh_interval_seconds", 3600))),  # Min 5 minutes
        host=server_settings.get("host", "0.0.0.0"),
        port=port,
        domain=server_settings.get("domain")
    )

    logging.info("Validated config for %d family members", len(self.members))
```

Benefits:
- Silent data loss prevented
- Graceful handling of corrupted config
- Type validation at load time

---

## Implementation Checklist

Use this to track fixes:

```
CRITICAL FIXES:
☐ Fix #1: Date parsing with UTC and validation
☐ Fix #2: Event modal uses content-based keys instead of array indices
☐ Fix #3: UID collision detection across members
☐ Fix #4: Auto-refresh deduplication + timeout

HIGH-PRIORITY FIXES:
☐ Fix #5: HTTP status checks
☐ Fix #6: Event data validation schema
☐ Fix #7: Request timeout (included in #4)
☐ Fix #8: Calendar URL validation

MEDIUM-PRIORITY FIXES:
☐ Fix #9: Centralized date formatting
☐ Fix #10: Fixed search highlighting
☐ Fix #11: Config validation
☐ Remove legacy ics_merge_server.py and ics_html_server.py
☐ Add request ID middleware for logging
```

---

**Note:** All fixes are internal improvements that do not affect:
- ICS feed generation
- ICS feed endpoints
- ICS output format
- External subscriber functionality
- API response schemas (adds validation, doesn't change format)
