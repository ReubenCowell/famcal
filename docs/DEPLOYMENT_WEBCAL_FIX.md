# Deployment Guide: WebCal URL Fix

## Quick Start

To deploy this fix to your DigitalOcean server (188.166.175.212):

### Step 1: Pull the Latest Code

```bash
ssh root@188.166.175.212
cd /home/famcal/famcal
git pull origin main
```

Or if you're using manual uploads:

```bash
# From your local machine
scp -r /Users/reubencowell/Desktop/TEST/famcal/family_calendar_server.py root@188.166.175.212:/home/famcal/famcal/
scp -r /Users/reubencowell/Desktop/TEST/famcal/templates/admin.html root@188.166.175.212:/home/famcal/famcal/templates/
```

### Step 2: Verify Permissions

```bash
ssh root@188.166.175.212
cd /home/famcal/famcal

# Check ownership
ls -la family_calendar_server.py templates/admin.html

# Fix if needed
chown -R famcal:famcal .
chmod 755 .
chmod 644 family_calendar_server.py templates/admin.html
```

### Step 3: Restart the Service

```bash
ssh root@188.166.175.212

# Restart the Flask service
sudo systemctl restart famcal

# Verify it started
sudo systemctl status famcal

# Check logs for any errors
sudo journalctl -u famcal -n 30
```

### Step 4: Test in Browser

1. Open admin page: https://cowellfamilycalendar.live/admin
2. Add a WebCal calendar to verify it displays correctly
3. Check that URL appears (not "No URL")
4. Wait 30-60 seconds for sync
5. Verify events appear and counter updates

## What Changed

### Backend (Python)

- `family_calendar_server.py`:
  - Fixed URL validation for webcal:// and webcals:// protocols
  - Added helper function `_has_valid_url()` to both API endpoints
  - Improved logging during calendar sync
  - Better error messages

### Frontend (JavaScript)

- `templates/admin.html`:
  - Fixed calendar display to show WebCal URLs correctly
  - Added "(syncing...)" indicator
  - Added "⚠ No URL" warning in red for invalid calendars
  - Added URL validation before form submission
  - Added auto-refresh of status every 5 seconds

## Rollback (if needed)

If something goes wrong, rollback is simple:

```bash
ssh root@188.166.175.212
cd /home/famcal/famcal

# Use git to revert to previous version
git log --oneline | head -5
git revert HEAD
sudo systemctl restart famcal

# Or manually restore from backup if you have one
cp family_calendar_server.py.bak family_calendar_server.py
sudo systemctl restart famcal
```

## Testing Checklist

After deployment, verify each of these works:

- [ ] Admin page loads without errors
- [ ] Can add a WebCal calendar
- [ ] WebCal URL displays in calendar list (not "No URL")
- [ ] Calendar shows "(syncing...)" while fetching
- [ ] After ~30s, status shows updated timestamp
- [ ] Events count increases from 0 to actual number
- [ ] Sources shows "1/1" (successful sync)
- [ ] Existing members/calendars still work
- [ ] Combined family feed still works
- [ ] Individual member feeds still work
- [ ] Browser console shows no JavaScript errors

## Troubleshooting

### "No URL" still shows after deployment

**Solution:** Clear browser cache and reload
```bash
# Hard refresh in browser
Cmd+Shift+R (Mac) or Ctrl+Shift+R (Linux/Windows)
```

### Service won't restart

**Check logs:**
```bash
sudo journalctl -u famcal -n 50
```

**If syntax error:**
```bash
cd /home/famcal/famcal
python3 -m py_compile family_calendar_server.py
```

### Events not syncing after 1 minute

**Check if calendar URL is reachable:**
```bash
curl -I "https://p164-caldav.icloud.com/published/2/MTc3MDg1..."
```

**Check app logs:**
```bash
sudo journalctl -u famcal -n 50 | grep "Fetching\|Successfully\|Failed"
```

### "Permission denied" errors

**Fix permissions:**
```bash
cd /home/famcal/famcal
chown -R famcal:famcal .
chmod 755 .
chmod 775 output
chmod 664 family_config.json
sudo systemctl restart famcal
```

## Verification Commands

Check if fix is deployed correctly:

```bash
# Check Python file has new code
grep "_has_valid_url" /home/famcal/famcal/family_calendar_server.py

# Check admin.html has auto-refresh
grep "refreshInterval" /home/famcal/famcal/templates/admin.html

# Check logs show proper syncing
sudo journalctl -u famcal -n 100 | grep "Fetching ICS calendar"
```

All three should return output indicating the fix is in place.

## Performance Impact

This update has **minimal performance impact**:

- ✅ No additional database queries
- ✅ Same sync frequency (1 hour default)
- ✅ Added 5-second status polling on admin page (only when page is open)
- ✅ ~2KB additional code in JavaScript
- ✅ No impact on ICS feed generation

## Rollout Plan

For safety, recommend this deployment order:

1. **Test locally first** (your dev environment)
2. **Deploy to production** (your DigitalOcean server)
3. **Monitor logs** for 24 hours
4. **Gather feedback** from users
5. **Document any issues** encountered

## Support

If you encounter issues:

1. Check [BUG_FIX_WEBCAL_URLS.md](BUG_FIX_WEBCAL_URLS.md) for detailed explanation
2. Review service logs: `sudo journalctl -u famcal -n 100`
3. Test manually: `curl "https://your-webcal-url/..."`
4. Verify permissions: `ls -la /home/famcal/famcal/`
