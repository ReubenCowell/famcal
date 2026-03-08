#!/usr/bin/env python3
"""Test database initialization."""

from pathlib import Path
import logging
import os
import sys

logging.basicConfig(level=logging.DEBUG, format='%(levelname)s: %(message)s')

from family_calendar_server import create_app, FamilyCalendarManager, DATABASE_AVAILABLE, USE_DATABASE

print(f"DATABASE_AVAILABLE: {DATABASE_AVAILABLE}")
print(f"USE_DATABASE: {USE_DATABASE}")
print()

if not DATABASE_AVAILABLE:
    print("✗ Database modules not available - check imports")
    sys.exit(1)
    
if not USE_DATABASE:
    print("✗ Database not enabled - set FAMCAL_USE_DATABASE=true")
    sys.exit(1)

manager = FamilyCalendarManager(Path('family_config.json'))
print(f"✓ Loaded config: {len(manager.members)} members")

app = create_app(manager, 10)
print("✓ App created")

# Check database file
db_exists = os.path.exists('famcal.db')
print(f"✓ Database file exists: {db_exists}")

if db_exists:
    import sqlite3
    conn = sqlite3.connect('famcal.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = cursor.fetchall()
    
    if tables:
        print(f"✓ Database tables ({len(tables)}):")
        for t in tables:
            print(f"  - {t[0]}")
            # Count rows
            cursor.execute(f"SELECT COUNT(*) FROM {t[0]}")
            count = cursor.fetchone()[0]
            print(f"    ({count} rows)")
    else:
        print("✗ No tables found in database")
    
    conn.close()
else:
    print("✗ Database file not created")
