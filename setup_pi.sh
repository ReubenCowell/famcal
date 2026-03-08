#!/bin/bash

# Family Calendar Server - Raspberry Pi Setup Script
# This script sets up the calendar server on a Raspberry Pi

set -e

echo "🍓 Family Calendar Server - Raspberry Pi Setup"
echo "=============================================="
echo ""

# Check if running as root
if [ "$EUID" -eq 0 ]; then 
    echo "❌ Please do not run this script as root (without sudo)"
    exit 1
fi

# Set installation directory
INSTALL_DIR="$HOME/famcal"
SERVICE_NAME="famcal"

echo "📁 Installation directory: $INSTALL_DIR"
echo ""

# Create installation directory if it doesn't exist
if [ ! -d "$INSTALL_DIR" ]; then
    echo "Creating installation directory..."
    mkdir -p "$INSTALL_DIR"
fi

# Copy files to installation directory
echo "📋 Copying files..."
cp family_calendar_server.py "$INSTALL_DIR/"
cp wsgi.py "$INSTALL_DIR/"
cp requirements.txt "$INSTALL_DIR/"
cp famcal.service "$INSTALL_DIR/"
cp start_server.sh "$INSTALL_DIR/"
cp -r templates "$INSTALL_DIR/"
cp -r static "$INSTALL_DIR/"

# Copy calendar URLs file if it doesn't exist
if [ ! -f "$INSTALL_DIR/family_config.json" ]; then
    cat > "$INSTALL_DIR/family_config.json" << 'EOF'
{
  "family_members": {},
  "server_settings": {
    "refresh_interval_seconds": 3600,
    "host": "0.0.0.0",
    "port": 8000,
    "domain": "",
    "website_password": "",
    "secret_key": ""
  }
}
EOF
    echo "✏️  Default family_config.json created"
else
    echo "ℹ️  Keeping existing family_config.json"
fi

# Update Python and pip
echo ""
echo "📦 Updating system packages..."
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv

# Create virtual environment
echo ""
echo "🐍 Setting up Python virtual environment..."
cd "$INSTALL_DIR"
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi

# Activate virtual environment and install dependencies
echo "📥 Installing Python dependencies..."
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate

# Install systemd service
echo ""
echo "⚙️  Installing systemd service..."
sudo cp famcal.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"

echo ""
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "1. Start the service: sudo systemctl start $SERVICE_NAME"
echo "2. Open the admin page: http://$(hostname -I | awk '{print $1}'):8000/admin"
echo "3. Add family members and their calendar URLs"
echo "4. Check status: sudo systemctl status $SERVICE_NAME"
echo "5. View logs: sudo journalctl -u $SERVICE_NAME -f"
echo ""
echo "Access your calendar server at:"
echo "  http://$(hostname -I | awk '{print $1}'):8000"
echo "  or http://raspberrypi.local:8000"
echo ""
