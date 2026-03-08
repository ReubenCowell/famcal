---
name: merge-ics-feed-service
description: "Generate a modular Python service that merges public ICS URLs into one hosted feed with scheduled refresh."
argument-hint: "URLs, interval, framework preference, deployment target"
agent: agent
---
Create a modular Python script that merges multiple public iCal/ICS calendar URLs into one hosted ICS feed.

Required implementation details:
- Use `requests` to fetch source ICS files.
- Use `icalendar` to parse and merge `VEVENT` entries.
- Remove duplicate events by `UID`.
- Save the merged output to `merged_calendar.ics`.
- Serve the file from a stable URL using a lightweight web framework (`Flask` by default).
- Run periodic refresh in the background (default every hour).

Output format:
1. Return one complete Python script in a single code block.
2. Include install and run instructions at the top of the script.
3. After the code, include:
- How to change refresh interval.
- How to deploy on a small server (for example Fly.io and DigitalOcean).

Quality constraints:
- Keep functions separated for fetching, parsing/merging, saving, serving, and scheduling.
- Use clear error handling and logging.
- Keep the subscription URL path stable across restarts.

If the user provides extra constraints, apply them without removing these core requirements.
