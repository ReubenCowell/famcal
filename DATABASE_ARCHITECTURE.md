# Database Architecture - Family Calendar System

## Executive Summary

This document outlines the database backend architecture for the Family Calendar application, designed to replace file-based persistence while maintaining full backward compatibility with the existing ICS feed generator.

**Key Constraint:** The ICS endpoints (`/<member_id>/calendar.ics` and `/family/calendar.ics`) must produce identical output before and after database migration.

---

## Current System Analysis

### Existing Data Flow

```
1. WebCal/ICS Feed (External)
   ↓
2. fetch_calendar_data() → HTTP fetch + protocol conversion
   ↓
3. parse_calendar_data() → icalendar.Calendar object
   ↓
4. merge_member_calendars() → Deduplication + privacy controls
   ↓
5. apply_privacy_to_event() → Event filtering based on settings
   ↓
6. File storage: output/{member_id}_calendar.ics
   ↓
7. ICS endpoint serves file directly
   ↓
8. Frontend API: /api/events → parses ICS files to JSON
```

### Current Storage

- **Config:** `family_config.json` - member/calendar definition
- **Events:** `output/{member_id}_calendar.ics` - generated ICS files
- **State:** In-process `MemberStatus` objects

### Problems with Current Approach

1. **Event Querying Cost:** Frontend must parse ICS files to extract/filter events
2. **Duplication Detection:** Must re-parse all events on every refresh
3. **No Event History:** Can't track changes or sync state
4. **Limited Querying:** Can't efficiently filter by date, member, source
5. **Race Conditions:** File-based locking is fragile
6. **Scalability:** Large calendars require large ICS re-parsing

---

## Proposed Database Architecture

### Design Principles

1. **ICS Feed Integrity:** Database is read-only for ICS generation - no behavior changes
2. **Event Traceability:** Track event origin (which feed, sync time, external ID)
3. **Duplicate Prevention:** Unique constraints on external event IDs
4. **Atomic Operations:** Database transactions ensure consistency
5. **Performance:** Indexed queries for date ranges, member filtering
6. **Migration Safety:** Old file-based system runs alongside during transition

### Technology Stack

- **Database:** PostgreSQL 13+ (or SQLite for development)
- **ORM:** SQLAlchemy 2.0+ with Flask-SQLAlchemy
- **Migrations:** Alembic for schema versioning
- **Connection:** psycopg2 (PostgreSQL) or built-in sqlite3

### Schema Design

Tables:
1. **calendar_sources** - WebCal feeds and their configuration
2. **events** - Parsed calendar events with deduplication keys
3. **sync_logs** - Track sync history and errors
4. **event_occurrences** - Recurring event instances (if needed)
5. **member_calendars** - Junction table (member → sources)

---

## Database Schema

```sql
-- Calendar sources (WebCal feeds)
CREATE TABLE calendar_sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Configuration
    name VARCHAR(255) NOT NULL,
    feed_url TEXT NOT NULL,
    source_type VARCHAR(20) NOT NULL DEFAULT 'ics', -- ics, caldav
    
    -- Privacy settings
    show_details BOOLEAN DEFAULT TRUE,
    busy_text VARCHAR(255) DEFAULT 'Busy',
    show_location BOOLEAN DEFAULT FALSE,
    
    -- Credentials (for CalDAV)
    caldav_username VARCHAR(255),
    caldav_password VARCHAR(255),
    
    -- Sync tracking
    last_sync_at TIMESTAMP WITH TIME ZONE,
    last_sync_status VARCHAR(20) DEFAULT 'pending', -- pending, success, failed, partial
    last_sync_error TEXT,
    
    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(feed_url)
);

-- Family members
CREATE TABLE family_members (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    name VARCHAR(255) NOT NULL UNIQUE,
    member_id VARCHAR(255) NOT NULL UNIQUE, -- Internal ID for URLs
    color VARCHAR(7) NOT NULL, -- Hex color
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Junction: Members can subscribe to multiple sources
CREATE TABLE member_calendar_subscriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    member_id UUID NOT NULL REFERENCES family_members(id) ON DELETE CASCADE,
    source_id UUID NOT NULL REFERENCES calendar_sources(id) ON DELETE CASCADE,
    
    -- Member-specific overrides
    show_details BOOLEAN, -- NULL = use source default
    busy_text VARCHAR(255),
    show_location BOOLEAN, -- NULL = use source default
    
    subscribed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(member_id, source_id)
);

-- Calendar events (normalized)
CREATE TABLE events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- External identifiers for deduplication
    source_id UUID NOT NULL REFERENCES calendar_sources(id) ON DELETE CASCADE,
    external_event_id VARCHAR(500), -- UID from ICS
    external_event_hash VARCHAR(64), -- SHA256(summary+start+end+location) for UID-less events
    
    -- Event data
    title VARCHAR(255) NOT NULL,
    description TEXT,
    location VARCHAR(500),
    
    -- Timing
    start_time TIMESTAMP WITH TIME ZONE NOT NULL,
    end_time TIMESTAMP WITH TIME ZONE,
    all_day BOOLEAN DEFAULT FALSE,
    
    -- ICS event properties
    ics_uid VARCHAR(500), -- From UID field
    ics_transp VARCHAR(20) DEFAULT 'OPAQUE', -- OPAQUE (busy), TRANSPARENT (free)
    ics_status VARCHAR(20) DEFAULT 'CONFIRMED', -- CONFIRMED, TENTATIVE, CANCELLED
    ics_raw BYTEA, -- Original VEVENT binary (for debug/recovery)
    
    -- Tracking
    last_modified_in_source TIMESTAMP WITH TIME ZONE,
    synced_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    
    -- Deduplication constraint
    UNIQUE(source_id, external_event_id),
    UNIQUE(source_id, external_event_hash)
);

-- Sync history and debugging
CREATE TABLE sync_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    source_id UUID NOT NULL REFERENCES calendar_sources(id) ON DELETE CASCADE,
    sync_started_at TIMESTAMP WITH TIME ZONE NOT NULL,
    sync_completed_at TIMESTAMP WITH TIME ZONE,
    
    status VARCHAR(20) NOT NULL DEFAULT 'in_progress', -- in_progress, success, failed, partial
    
    -- Statistics
    http_status_code INTEGER,
    events_found INTEGER,
    events_imported INTEGER,
    events_updated INTEGER,
    events_deleted INTEGER,
    duplicates_skipped INTEGER,
    parse_errors INTEGER,
    
    error_message TEXT,
    error_details TEXT, -- JSON array of per-event errors
    
    -- Debug
    fetched_bytes INTEGER,
    duration_ms INTEGER,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for performance
CREATE INDEX idx_events_source_external_id 
    ON events(source_id, external_event_id);

CREATE INDEX idx_events_source_start_time 
    ON events(source_id, start_time);

CREATE INDEX idx_events_start_time 
    ON events(start_time);

CREATE INDEX idx_events_end_time 
    ON events(end_time);

CREATE INDEX idx_sync_logs_source_started 
    ON sync_logs(source_id, sync_started_at DESC);

CREATE INDEX idx_member_subs_member 
    ON member_calendar_subscriptions(member_id);

CREATE INDEX idx_member_subs_source 
    ON member_calendar_subscriptions(source_id);
```

---

## Migration Strategy

### Phase 1: Database Layer (Parallel Run)
- PostgreSQL database created
- SQLAlchemy models defined
- Database initialized with empty tables
- File system still primary source

### Phase 2: Data Ingestion
- New sync process feeds WebCal → Database
- ICS file generation reads from database instead of temporary merge
- File system continues to be authoritative backup

### Phase 3: Switchover
- Monitor database sync for 7+ days
- Verify ICS output matches file-based output exactly
- Switch ICS generation to database-only
- Deprecate file-based ICS files

### Phase 4: Full Migration
- Frontend API uses database directly
- File format deprecated for internal use
- Maintain file export for backup compatibility

---

## Implementation Approach

### 1. Database Module (`db.py`)
- SQLAlchemy models
- Connection pooling
- Migration support

### 2. Sync Engine (`sync_engine.py`)
- Robust WebCal fetching
- Event parsing and normalization
- Deduplication logic
- Atomic database updates

### 3. ICS Generator Changes
- Read events from database instead of previous merge
- Apply privacy controls during generation
- Output format IDENTICAL to before

### 4. API Changes (`family_calendar_server.py`)
- Update event querying to use database
- Keep all endpoints identical
- Response schema unchanged

---

## Backward Compatibility Guarantees

### ICS Feed Output

**Before/After:** Identical

- Event UID unchanged
- Title (with privacy masking if needed) identical
- DateTime format unchanged
- Timezone components preserved
- Event ordering deterministic

### API Endpoints

**Before/After:** Identical response schemas

- `/api/members` - same response
- `/api/events?start=X&end=Y` - same events array
- `/api/<member_id>/events` - same filtering
- `/<member_id>/calendar.ics` - binary-identical output

### Configuration

**Before/After:** Same format

- `family_config.json` format preserved
- All privacy settings kept
- Event deduplication logic unchanged

---

## Key Design Decisions

### 1. Why PostgreSQL?
- ACID compliance for consistent state
- JSON support for flexible metadata
- Excellent query performance
- Migration tools (Alembic)
- Reliable replication for backups

### 2. Why SQLAlchemy?
- Type-safe ORM for Python
- Works with Flask seamlessly
- Vendor-agnostic (can switch to other DBs)
- Excellent query builder

### 3. Why Deduplication at Database Level?
- Prevents duplicates at source
- Single source of truth
- Simplifies ICS generation
- Reduces memory usage

### 4. Why Store Raw ICS Event?
- Debug problematic events
- Migration rollback capability
- Audit trail for changes

### 5. Why Track Every Sync?
- Diagnose feed problems
- Monitor reliability
- Audit history of changes

---

## Performance Considerations

### Query Optimization

Events are indexed by:
- `source_id + external_id` (dedup check)
- `source_id + start_time` (date range queries)
- `start_time` (all events in period)

Monthly calendar queries:
```sql
SELECT * FROM events 
WHERE start_time >= '2026-03-01'::timestamp AND end_time <= '2026-04-01'::timestamp
LIMIT 5000;
```
Expected: <50ms with index

### Connection Pooling

- Min pool size: 5
- Max pool size: 20
- Automatic reconnection on failure
- Query timeout: 30s

### Batch Operations

- Sync operations use bulk inserts (10,000 events/batch)
- Each batch in its own transaction
- Rollback on any error

---

## Monitoring & Reliability

### Metrics to Track

1. **Sync Health**
   - Last successful sync per source
   - Sync duration trends
   - Error rates

2. **Data Quality**
   - Event counts by source
   - Duplicate detection rate
   - Parse error rate

3. **Performance**
   - Query latency (p50, p95, p99)
   - Connection pool utilization
   - Database size growth

### Alerting

Alert if:
- Sync fails 3+ consecutive times
- Query latency > 500ms
- Database size > 5GB
- Connection pool exhausted

---

## Data Integrity Safeguards

1. **Unique Constraints**
   - `source_id + external_id`  (true primary key for feeds)
   - Calendar source URLs unique
   - Member IDs unique

2. **Foreign Key Constraints**
   - Delete source → Delete all events
   - Delete member → Delete all subscriptions

3. **Transaction Safety**
   - All sync operations in transactions
   - Rollback on any error
   - No partial data

4. **Application-Level Validation**
   - Event fields validated on insert
   - Date ranges validated
   - Privacy settings validated

---

## Rollback Plan

If database implementation fails:

1. **Keep files in `output/`** - Always generated as backup
2. **ICS generation reads files** if database unavailable
3. **Sync marks source failed** - alerts admin
4. **API fallback to file parsing** - slower but available

This ensures calendar access is never lost due to database issues.

---

## Next Steps

1. Set up PostgreSQL database
2. Create SQLAlchemy models
3. Write migration scripts
4. Implement sync engine
5. Update ICS generation
6. Run parallel tests (file vs DB)
7. Deploy with dual-write for verification
8.Monitor metrics for 7+ days
9. Switchover when confident
