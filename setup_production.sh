#!/bin/bash
set -e

# Family Calendar Production Setup Script
# Run this on your server to install and configure everything

echo "=========================================="
echo "Family Calendar Production Setup"
echo "=========================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    echo -e "${RED}✗ Please run as a regular user (not root)${NC}"
    echo "  Use: bash setup_production.sh"
    exit 1
fi

# 1. Check system packages
echo "=========================================="
echo "Step 1: Checking system packages"
echo "=========================================="

check_command() {
    if command -v $1 &> /dev/null; then
        echo -e "${GREEN}✓${NC} $1 installed"
        return 0
    else
        echo -e "${RED}✗${NC} $1 not found"
        return 1
    fi
}

MISSING_PACKAGES=()

if ! check_command python3; then
    MISSING_PACKAGES+=("python3")
fi

if ! check_command pip3; then
    MISSING_PACKAGES+=("python3-pip")
fi

if ! check_command psql; then
    MISSING_PACKAGES+=("postgresql postgresql-contrib")
fi

if ! check_command nginx; then
    MISSING_PACKAGES+=("nginx")
fi

if ! check_command certbot; then
    MISSING_PACKAGES+=("certbot python3-certbot-nginx")
fi

if ! check_command git; then
    MISSING_PACKAGES+=("git")
fi

if [ ${#MISSING_PACKAGES[@]} -ne 0 ]; then
    echo ""
    echo -e "${YELLOW}Missing packages detected. Install with:${NC}"
    echo "sudo apt update && sudo apt install -y ${MISSING_PACKAGES[@]}"
    echo ""
    read -p "Install now? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        sudo apt update
        sudo apt install -y ${MISSING_PACKAGES[@]}
    else
        echo -e "${RED}Cannot continue without required packages${NC}"
        exit 1
    fi
fi

echo ""

# 2. Check Python version
echo "=========================================="
echo "Step 2: Verifying Python version"
echo "=========================================="

PYTHON_VERSION=$(python3 --version | cut -d' ' -f2 | cut -d'.' -f1,2)
REQUIRED_VERSION="3.9"

if (( $(echo "$PYTHON_VERSION >= $REQUIRED_VERSION" | bc -l) )); then
    echo -e "${GREEN}✓${NC} Python $PYTHON_VERSION (>= $REQUIRED_VERSION required)"
else
    echo -e "${RED}✗${NC} Python $PYTHON_VERSION found, but $REQUIRED_VERSION+ required"
    exit 1
fi

echo ""

# 3. Set up virtual environment
echo "=========================================="
echo "Step 3: Setting up Python virtual environment"
echo "=========================================="

if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
    echo -e "${GREEN}✓${NC} Virtual environment created"
else
    echo -e "${GREEN}✓${NC} Virtual environment already exists"
fi

source .venv/bin/activate

echo "Installing Python dependencies..."
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
echo -e "${GREEN}✓${NC} Python packages installed"

echo ""

# 4. Check PostgreSQL
echo "=========================================="
echo "Step 4: Checking PostgreSQL"
echo "=========================================="

if sudo systemctl is-active --quiet postgresql; then
    echo -e "${GREEN}✓${NC} PostgreSQL service is running"
else
    echo -e "${YELLOW}⚠${NC} PostgreSQL service not running"
    read -p "Start PostgreSQL now? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        sudo systemctl start postgresql
        sudo systemctl enable postgresql
        echo -e "${GREEN}✓${NC} PostgreSQL started"
    fi
fi

echo ""

# 5. Set up database
echo "=========================================="
echo "Step 5: Database setup"
echo "=========================================="

read -p "Do you want to create/configure the database? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo ""
    echo "Enter database details:"
    read -p "Database name [famcal_db]: " DB_NAME
    DB_NAME=${DB_NAME:-famcal_db}
    
    read -p "Database user [famcal_user]: " DB_USER
    DB_USER=${DB_USER:-famcal_user}
    
    read -sp "Database password: " DB_PASS
    echo ""
    
    if [ -z "$DB_PASS" ]; then
        echo -e "${RED}✗${NC} Password cannot be empty"
        exit 1
    fi
    
    # Check if database exists
    DB_EXISTS=$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" 2>/dev/null || echo "")
    
    if [ "$DB_EXISTS" = "1" ]; then
        echo -e "${YELLOW}⚠${NC} Database '$DB_NAME' already exists"
        read -p "Drop and recreate (WARNING: destroys all data)? (y/n) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            sudo -u postgres psql -c "DROP DATABASE IF EXISTS $DB_NAME;"
            sudo -u postgres psql -c "DROP USER IF EXISTS $DB_USER;"
        else
            echo "Skipping database creation"
            DB_URL="postgresql://$DB_USER:$DB_PASS@localhost:5432/$DB_NAME"
        fi
    fi
    
    if [ "$DB_EXISTS" != "1" ] || [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "Creating database and user..."
        sudo -u postgres psql << EOF
CREATE DATABASE $DB_NAME;
CREATE USER $DB_USER WITH ENCRYPTED PASSWORD '$DB_PASS';
GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;
\c $DB_NAME
GRANT ALL ON SCHEMA public TO $DB_USER;
EOF
        echo -e "${GREEN}✓${NC} Database created: $DB_NAME"
        DB_URL="postgresql://$DB_USER:$DB_PASS@localhost:5432/$DB_NAME"
    fi
    
    # Test connection
    echo "Testing database connection..."
    if PGPASSWORD="$DB_PASS" psql -h localhost -U "$DB_USER" -d "$DB_NAME" -c "SELECT 1;" > /dev/null 2>&1; then
        echo -e "${GREEN}✓${NC} Database connection successful"
    else
        echo -e "${RED}✗${NC} Database connection failed"
        exit 1
    fi
    
    # Save connection string to .env file
    cat > .env << EOF
# Family Calendar Environment Configuration
# Generated by setup_production.sh on $(date)

# Database Configuration
FAMCAL_USE_DATABASE=true
SQLALCHEMY_DATABASE_URL=$DB_URL

# Application Configuration
FAMILY_CONFIG=family_config.json
LOG_LEVEL=INFO

# Optional website password gate (preferred over plain-text config password)
# FAMCAL_WEB_PASSWORD=change-me

# Optional override for Flask session signing key
# FAMCAL_SECRET_KEY=replace-with-long-random-value
EOF
    
    echo -e "${GREEN}✓${NC} Database configuration saved to .env"
    echo ""
fi

echo ""

# 6. Test application
echo "=========================================="
echo "Step 6: Testing application"
echo "=========================================="

if [ -f ".env" ]; then
    echo "Loading environment from .env..."
    export $(cat .env | grep -v '^#' | xargs)
fi

# Check if config exists
if [ ! -f "family_config.json" ]; then
    echo "Creating default family_config.json..."
    cat > family_config.json << 'EOF'
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
    echo -e "${GREEN}✓${NC} Default config created"
fi

echo "Testing application startup..."
timeout 5 python3 << 'EOF' 2>&1 | grep -E "✓|✗|ERROR|created"
from pathlib import Path
from family_calendar_server import create_app, FamilyCalendarManager, DATABASE_AVAILABLE, USE_DATABASE
import sys

try:
    manager = FamilyCalendarManager(Path('family_config.json'))
    app = create_app(manager, 10)
    
    print(f"✓ App created successfully")
    print(f"  - Database available: {DATABASE_AVAILABLE}")
    print(f"  - Database enabled: {USE_DATABASE}")
    
    # Verify database tables if enabled
    if DATABASE_AVAILABLE and USE_DATABASE:
        with app.app_context():
            from db_models import db, FamilyMember
            tables = db.engine.table_names()
            print(f"✓ Database tables: {len(tables)} tables created")
    
    sys.exit(0)
except Exception as e:
    print(f"✗ Error: {e}")
    sys.exit(1)
EOF

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓${NC} Application test passed"
else
    echo -e "${RED}✗${NC} Application test failed"
    echo "Check the error messages above"
    exit 1
fi

echo ""

# 7. Summary and next steps
echo "=========================================="
echo "Setup Complete!"
echo "=========================================="
echo ""
echo "Your Family Calendar is configured and ready."
echo ""
echo "Next steps:"
echo ""
echo "1. Update domain/password in family_config.json (or use FAMCAL_WEB_PASSWORD in .env):"
echo "   {\"server_settings\": {\"domain\": \"your-domain.com\", \"website_password\": \"your-password\"}}"
echo ""
echo "2. Set up systemd service:"
echo "   sudo cp famcal.service /etc/systemd/system/"
echo "   sudo nano /etc/systemd/system/famcal.service  # Update password"
echo "   sudo systemctl daemon-reload"
echo "   sudo systemctl enable famcal"
echo "   sudo systemctl start famcal"
echo ""
echo "3. Configure Nginx (see docs/DEPLOY_DIGITALOCEAN.md)"
echo ""
echo "4. Set up SSL with certbot:"
echo "   sudo certbot --nginx -d your-domain.com"
echo ""
echo "5. Access admin interface:"
echo "   http://your-server-ip/admin"
echo ""

if [ -f ".env" ]; then
    echo -e "${YELLOW}⚠ IMPORTANT:${NC} Your database credentials are in .env"
    echo "  Keep this file secure and never commit it to git"
    echo ""
fi

echo -e "${GREEN}All checks passed!${NC}"
