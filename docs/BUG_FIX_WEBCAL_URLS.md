# Bug Fix: WebCal URL Display Issue

## Problem Summary

When adding a WebCal calendar (`webcal://` or `webcals://` URLs) through the admin interface, the calendar would display inconsistently:

1. **Initial state:** Admin page shows "No URL" even though URL was successfully saved
2. **During sync:** Events appear briefly, then disappear or show errors
3. **User experience:** UI appears unstable with flickering or missing data
4. **Confusion:** Users can't tell if the calendar is working because the URL is hidden

## Root Cause

The bug was in the `/api/members` and `/api/<member_id>/status` endpoints in `family_calendar_server.py`:

```python
# BUGGY CODE
"has_url": bool(cal.url and cal.url.startswith("http")),
```

This check only recognized URLs starting with `http://` or `https://`, but **NOT** `webcal://` or `webcals://` protocols.

When `has_url` returned `False`, the admin template displayed:
```javascript
${cal.has_url ? esc(cal.url) : '<em>No URL</em>'}
```

**Result:** WebCal URLs were saved in config but never displayed, making it appear like the configuration failed.

## Why This Caused Confusion

1. User adds calendar with WebCal URL → saved successfully to config
2. Backend starts syncing in background
3. Admin page shows "No URL" (because `has_url = False` for webcal://)
4. User thinks configuration failed
5. Meanwhile, events are syncing but UI suggests failure
6. UI shows inconsistent state (URL missing but events appearing)

## The Fix

### 1. Backend Validation Function (family_calendar_server.py)

Added a helper function that correctly validates all supported protocols:

```python
def _has_valid_url(url: str) -> bool:
    """Check if URL is valid (supports http, https, webcal, webcals)."""
    if not url:
        return False
    url_lower = url.lower()
    return url_lower.startswith(("http://", "https://", "webcal://", "webcals://"))
```

### 2. Updated Both API Endpoints

- `/api/members` endpoint
- `/api/<member_id>/status` endpoint

Now use proper protocol detection:

```python
"has_url": _has_valid_url(cal.url),  # Correctly handles all protocols
```

### 3. Improved Logging (family_calendar_server.py)

Added detailed logging when fetching calendars:

```python
logging.info("Fetching ICS calendar: %s for %s from %s", 
             calendar_source.name, member.name, calendar_source.url)
logging.info("Successfully merged %s for %s (%d events, %d duplicates skipped)",
             calendar_source.name, member.name, merged_events, duplicates_skipped)
```

This helps debug issues and track sync progress.

### 4. Frontend Improvements (templates/admin.html)

#### Visual Feedback
- Shows "(syncing...)" indicator when sources are being fetched
- Displays warning for missing URLs: **"⚠ No URL configured"** (in red)
- Color-coded status for clarity

#### Validation
- Frontend now validates URL format before submission
- Prevents saving calendars with empty or invalid URLs
- Shows error message if protocol is incorrect

#### Auto-Refresh
- Added 5-second auto-refresh of status data
- Shows real-time sync progress without refreshing entire page
- Prevents perception of "stuck" or stale data

```javascript
// Auto-refresh status every 5 seconds
refreshInterval = setInterval(() => {
    fetchStatus();
}, 5000);
```

## Testing Verification

To verify the fix works:

1. **Add a WebCal calendar:**
   - Go to Admin page
   - Click "+ Add Calendar"
   - Paste your WebCal URL: `webcal://p164-caldav.icloud.com/published/2/MTc3...`
   - Select "ICS URL" as source type
   - Click "Add Calendar"

2. **Check that URL displays correctly:**
   - URL should appear in calendar list (not "No URL")
   - Under "Update:", should show "syncing..." after a few seconds
   - After sync completes (~30 seconds), should show "Updated: [timestamp]"

3. **Verify events are fetched:**
   - "Events:" counter should increase from 0 to actual event count
   - "Sources:" should change from "0/1" to "1/1"

4. **Check logs for proper sync progress:**
   ```bash
   sudo journalctl -u famcal -n 50
   ```

   Should show:
   ```
   INFO: Fetching ICS calendar: home for Toby from webcal://p164-caldav.icloud.com/...
   INFO: Successfully merged home for Toby (15 events, 0 duplicates skipped)
   ```

## Files Modified

1. **family_calendar_server.py**
   - Fixed URL validation in `/api/members` endpoint
   - Fixed URL validation in `/api/<member_id>/status` endpoint
   - Improved logging in `merge_member_calendars()`
   - Enhanced error messages for invalid URLs

2. **templates/admin.html**
   - Improved `renderMembers()` to handle all protocol types
   - Added visual feedback for syncing status
   - Added URL validation before form submission
   - Added auto-refresh timer for real-time status updates
   - Better error indicators for missing URLs

## Backwards Compatibility

✅ **100% compatible** - No changes to:
- ICS feed URLs or format
- Member/calendar data structure
- API response structure
- End-user functionality

Existing subscribers will see no change in their feeds.

## Improvements Beyond Bug Fix

The fix also includes several hardening improvements:

1. **Better error messages:** Users now see clear explanations if something goes wrong
2. **Visual sync indicators:** Shows when calendars are being synchronized
3. **Real-time updates:** Admin page reflects status changes without manual refresh
4. **URL validation:** Prevents invalid URLs from being saved
5. **Comprehensive logging:** Helps troubleshoot sync issues

## Known Limitations

- Auto-refresh is client-side (5 seconds) - server-side push would be better but requires more complexity
- No email notifications if sync fails - could be added in future
- No retry mechanism for failed syncs - uses background scheduler instead

## Future Enhancements

Potential improvements for the future:

1. **Retry logic:** Automatically retry failed syncs with backoff
2. **Sync history:** Show last N sync attempts and results
3. **Per-calendar refresh:** Allow refreshing specific calendars instead of all
4. **Batch operations:** Add/update multiple calendars at once
5. **Web hooks:** Notify external systems when calendars sync
