# Plan: Family Calendar Feature Pack (8 Features)

**TL;DR:** Add 8 enhancements to the family calendar app. All are frontend-heavy with minimal backend additions (1 new endpoint + query param tweak on 2 existing routes). The app's existing CSS custom properties and vanilla JS architecture make most features straightforward — except multi-day spanning, which requires a significant `renderGrid()` refactor.

---

## Phase 1: Foundation & Quick Wins (all parallel, no interdependencies)

### Step 1 — Dark Mode (toggle + auto-detect)
- Add `[data-theme="dark"]` overrides for all design tokens in `static/style.css` (`:root` vars at L2-40 already power the entire color scheme)
- Add `@media (prefers-color-scheme: dark)` fallback when no explicit preference
- Add sun/moon toggle button in top nav across all 3 templates (`family_index.html`, `admin.html`, `login.html`)
- Persist to `localStorage` (`light` | `dark` | `auto`); apply `data-theme` on `<html>`
- Extract shared toggle JS to `static/theme.js` to avoid duplication across templates

**Files to modify:**
- `static/style.css` — dark theme token overrides (~40 lines)
- `templates/family_index.html` — toggle button + theme.js include
- `templates/admin.html` — toggle button + theme.js include
- `templates/login.html` — toggle button + theme.js include
- `static/theme.js` *(new)* — shared toggle logic

### Step 2 — Event Click/Tap Detail Modal
- Add a modal overlay in `templates/family_index.html` — currently event chips open the day panel via `selectDay()`; instead, chip clicks open a centered modal with: title, full date/time, location, full description, member name + color dot, availability badge
- Close on overlay click, Escape, or close button
- Day-cell clicks still open the day panel (only event chip clicks open modal)
- Mobile: 95% width

**Files to modify:**
- `templates/family_index.html` — modal HTML + JS event handling (~60 lines)
- `static/style.css` — modal styles + dark mode variant (~30 lines)

### Step 3 — Print-Friendly Stylesheet
- Expand the existing minimal `@media print` block at `style.css` L716 (~5 lines currently)
- Add: `print-color-adjust: exact` to preserve event colors, solid cell borders, page margins, avoid breaks inside cells, show calendar title/date at top, clean list view formatting
- Hide: top nav, toolbar, member pills, feed bar, day panel, tooltips, modal, buttons, theme toggle

**Files to modify:**
- `static/style.css` — expand `@media print` block (~50 lines)

### Step 4 — Auto-Refresh Improvement
- Enhance the existing `setInterval(fetchEvents, 60000)` at `family_index.html` L140:
  - Pause when tab hidden (`document.visibilityState`)
  - Only re-render if data changed (compare JSON hash)
  - Show subtle "Updated" fade indicator + "last updated" timestamp in toolbar
  - Add manual refresh button with spin animation
  - Configurable interval (30s / 1m / 5m / off) stored in `localStorage`

**Files to modify:**
- `templates/family_index.html` — visibility API integration, change detection, refresh indicator, settings (~40 lines)
- `static/style.css` — subtle animation styles (~10 lines)

---

## Phase 2: Complex Frontend Features (parallel with each other, after Phase 1 for modal dependency)

### Step 5 — Search/Filter Events
- Add search input in toolbar area of `templates/family_index.html`
- Client-side filter on already-fetched `events` array — match `summary`, `location`, `description` (case-insensitive, debounced 300ms)
- Highlight matches with `<mark>`, show result count, clear button
- Persist search across view switches (month/week/list)

**Files to modify:**
- `templates/family_index.html` — search input + filter logic + highlight (~50 lines)
- `static/style.css` — search bar styles + highlight styling (~15 lines)

### Step 6 — Multi-Day Event Spanning in Month View *(most complex)*
- Currently `buildDayMap()` (`family_index.html` L427) places duplicate chips on each day. Refactor `renderGrid()` (L306) to:
  - Identify multi-day all-day events and allocate horizontal "lanes"
  - Render as bars spanning columns via CSS `grid-column: start / end`
  - Split at week-row boundaries
  - Single-day events render below the spanning lane area
- Tapping a spanning bar opens the Step 2 modal
- Fallback: "continuation chips" (`→ Event Name`) if full spanning proves too complex

**Files to modify:**
- `templates/family_index.html` — major refactor of `renderGrid()` + lane allocation algorithm (~120 lines changed)
- `static/style.css` — spanning bar styles, lane heights, responsive adjustments (~40 lines)

---

## Phase 3: Backend + Frontend

### Step 7 — Color Picker in Admin *(depends on new backend endpoint)*
**Backend:**
- Add `PUT /api/admin/members/<member_id>` in `family_calendar_server.py` to update member `name` and `color` (validate hex `#RRGGBB`). No such endpoint exists today — only calendar-level PUT at L923.

**Frontend:**
- Add `<input type="color">` + preset swatches (12 colors from `MEMBER_COLORS` at L42) per member card in `admin.html`
- On change → call PUT → update chip + toast

**Files to modify:**
- `family_calendar_server.py` — new `PUT /api/admin/members/<member_id>` route (~30 lines)
- `templates/admin.html` — color picker UI + API call (~40 lines)
- `static/style.css` — color picker component styles (~10 lines)

### Step 8 — iCal Export / Download Button
**Backend:**
- Add `?download=1` query param to existing `member_calendar_feed()` (L722) and `family_calendar_feed()` (L676) — switches `Content-Disposition` from `inline` to `attachment` (~6 lines each)

**Frontend:**
- Download button in feed bar of `family_index.html` + per-member in `admin.html`
- Triggers `window.location.href = '/<id>/calendar.ics?download=1'`

**Files to modify:**
- `family_calendar_server.py` — add `download` query param handling (~6 lines per route)
- `templates/family_index.html` — download button in feed bar (~15 lines)
- `templates/admin.html` — download button per member (~10 lines)

---

## All Files Summary

| File | Steps |
|------|-------|
| `static/style.css` | 1, 2, 3, 4, 5, 6, 7 |
| `templates/family_index.html` | 1, 2, 4, 5, 6, 8 |
| `templates/admin.html` | 1, 7, 8 |
| `templates/login.html` | 1 |
| `family_calendar_server.py` | 7, 8 |
| `static/theme.js` *(new)* | 1 |

## Key Existing Code to Build On

- CSS custom properties at `style.css` `:root` (L2-40) → dark mode overrides
- `showTooltip()`/`hideTooltip()` in `family_index.html` → replace with modal
- `buildDayMap()` in `family_index.html` (~L427) → extend for multi-day spanning
- `renderGrid()` in `family_index.html` (~L306) → refactor for spanning bars
- `setInterval(fetchEvents, 60000)` at `family_index.html` L140 → enhance
- `MEMBER_COLORS` array at `family_calendar_server.py` L42 → preset swatches
- `member_calendar_feed()` at `family_calendar_server.py` L722 → add download param
- `family_calendar_feed()` at `family_calendar_server.py` L676 → add download param
- `esc()` function for XSS safety in both templates

---

## Verification

1. **Dark mode** — toggle cycles light/dark/auto; persists across refresh; all 3 pages consistent; status colors accessible
2. **Event modal** — click event chip in month/week/list/day-panel → modal with full details; close via overlay/Escape/×; mobile responsive
3. **Multi-day spanning** — multi-day all-day event spans columns in month view; wraps at week boundaries; single-day events below; "+N more" still works
4. **Search/filter** — type partial name → matching events shown with highlights; clear → all events restored; works across views
5. **Print** — Cmd+P → clean layout, event colors preserved, no UI chrome
6. **Auto-refresh** — tab hidden pauses polling; returning shows "Updated" briefly; no re-render when data unchanged
7. **Color picker** — pick color in admin → saved → calendar view shows events in new color after refresh
8. **iCal download** — click download → browser downloads `.ics`; opens in Apple Calendar / Google Calendar import

---

## Decisions

- **No build tools or frameworks** — stays vanilla JS/CSS
- **Step 6 is highest risk** — significant `renderGrid()` refactor; fallback: continuation chips
- **Search is client-side only** — filters loaded events, no new API
- **Shared theme.js** — avoids duplicating toggle logic across 3 templates
- **Color picker** needs a new backend endpoint since no member-level PUT exists
- **iCal export** reuses existing feed routes via query parameter
