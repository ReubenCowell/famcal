#!/bin/bash

# Quick start script for development/testing
# Run this to start the server quickly without systemd

cd "$(dirname "$0")"

# Check if virtual environment exists
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
    source .venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt
else
    source .venv/bin/activate
fi

# Check if config file exists
if [ ! -f "family_config.json" ]; then
    echo "Creating default config file..."
    cat > family_config.json << 'EOF'
{
  "family_members": {},
  "server_settings": {
    "refresh_interval_seconds": 3600,
    "host": "0.0.0.0",
    "port": 8000,
    "domain": ""
  }
}
EOF
fi

# Get local IP
LOCAL_IP=$(hostname -I | awk '{print $1}' 2>/dev/null || echo "localhost")

echo "🚀 Starting Family Calendar Server..."
echo "📍 Access at:"
echo "   http://localhost:8000"
echo "   http://$LOCAL_IP:8000"
echo ""
echo "   Admin: http://localhost:8000/admin"
echo "   Admin: http://$LOCAL_IP:8000/admin"
echo ""
echo "Press Ctrl+C to stop"
echo ""

# Database Configuration (Optional - Phase 1)
# Uncomment and configure these to enable database dual-write mode:
# export FAMCAL_USE_DATABASE=true
# export SQLALCHEMY_DATABASE_URL="postgresql://user:password@localhost:5432/famcal_db"
# For SQLite testing: export SQLALCHEMY_DATABASE_URL="sqlite:///famcal.db"

# Start the server
python family_calendar_server.py \
    --config family_config.json
