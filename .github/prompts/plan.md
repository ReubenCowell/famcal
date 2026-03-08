Plan my task.

## Goal
Upgrade the family calendar web app to a modern, Outlook-inspired experience while preserving current behavior:
- Dynamic family members (no hardcoded people)
- Busy/tentative/free inferred from source event fields (`STATUS`, `TRANSP`)
- Per-calendar privacy mode (show details vs show only "Busy")

## Scope
1. UI redesign for viewer and admin pages
2. Calendar month grid view (primary), list view (secondary)
3. Family-member color coding and filter pills
4. Better navigation and subscription-link UX
5. Backend/API adjustments only where needed for UI performance and correctness

## Implementation Plan
1. Audit current architecture
- Confirm active server entrypoint (`family_calendar_server.py`) and active templates.
- Verify API responses used by frontend (`/api/members`, `/api/<member_id>/events`, status endpoints).
- Identify stale legacy files and ensure new UI points to the active routes.

2. Lock data contract for frontend
- Standardize event payload fields: `summary`, `start`, `end`, `availability`, `status`, `location`, `description`.
- Ensure availability mapping is deterministic:
  - `STATUS=TENTATIVE` -> tentative
  - `STATUS=CANCELLED` -> cancelled
  - `TRANSP=TRANSPARENT` -> free
  - else -> busy
- Add optional fields needed for month grid rendering (all-day detection, normalized ISO timestamps, member id/name/color when in combined mode).

3. Add calendar-oriented API surface
- Add endpoint for month-range events (single member and combined family view) to avoid client overfetch.
- Accept query params: `start`, `end`, optional `member_ids`.
- Keep existing endpoints backward compatible.

4. Build Outlook-style frontend system
- Define a cohesive design token layer in CSS variables (surface, borders, typography, accent blues, status colors).
- Implement top navigation shell with clear sections: `Family Calendar`, `My Calendars`, `Family Members`.
- Implement header controls: month label, prev/next, today button, view switcher.
- Implement member pills with color chips and toggle state.

5. Implement month grid calendar view
- Render a 7-column month grid with leading/trailing days.
- Place events as compact colored bars with truncation and hover details.
- Distinguish busy/tentative/free/cancelled via visual system and labels.
- Handle overlapping/many events with "+N more" pattern.

6. Integrate list view and details ergonomics
- Keep list view as fallback/alternate view for small screens and accessibility.
- Add optional right-side/day panel for selected date events.
- Add copy-subscription URL UX with feedback state.

7. Upgrade admin UX
- Improve add/edit member and calendar flows with clearer forms, validation, and empty states.
- Keep privacy toggle explanation explicit:
  - "Hide details, but preserve event busy/tentative status from source calendar."
- Add immediate refresh action and per-member status visibility.

8. Mobile and responsiveness pass
- Ensure month grid remains usable on tablet/phone (horizontal scroll or adaptive compact mode).
- Ensure controls are touch-friendly and readable.

9. Verification and regression testing
- Functional checks:
  - Add/remove members dynamically
  - Add/edit/remove calendar sources
  - Generate stable per-member ICS URLs
  - Preserve busy/tentative/free from source events
- UI checks:
  - Desktop and mobile rendering
  - Color and status consistency
  - Keyboard and basic accessibility checks
- Ops checks:
  - Start script and systemd service still run correctly
  - No breaking changes for Raspberry Pi deployment

10. Keep readme.md updated with DETAILED but simple instructions for setting up the project and using it on a new raspberry pi

## Acceptance Criteria
1. No family members are hardcoded anywhere.
2. Busy/tentative/free/cancelled displayed from source event metadata, not manual config.
3. Viewer supports an Outlook-style month grid with member filtering and polished navigation.
4. Admin workflow is straightforward for non-technical family use.
5. Per-member ICS feed links remain stable and subscribable.
6. UI works on desktop and mobile.

## Deliverables
1. Updated templates and styles for viewer/admin interfaces.
2. Any required backend API additions for month-grid rendering.
3. Verified scripts/service compatibility for Raspberry Pi.
4. Final validation notes after manual testing.
