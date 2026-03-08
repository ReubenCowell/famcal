# Deploy to DigitalOcean

Deploy the Family Calendar Server on a DigitalOcean Droplet — a full Linux VPS with no restrictions.

**Cost**: $6/month (covered by GitHub Student Pack's $200 credit for ~33 months).

## 1. Create a Droplet

1. Log in to [cloud.digitalocean.com](https://cloud.digitalocean.com/)
2. Click **Create → Droplets**
3. Settings:
   - **Region**: Choose the closest to you
   - **Image**: Ubuntu 24.04 LTS
   - **Size**: Basic → Regular → **$6/mo** (1 vCPU, 1 GB RAM, 25 GB SSD)
   - **Authentication**: SSH key (recommended) or password
4. Click **Create Droplet**
5. Note the Droplet's IP address (e.g., `143.198.xxx.xxx`)

## 2. Connect via SSH

```bash
ssh root@<your-droplet-ip>
```

## 3. Initial server setup

```bash
# Create a non-root user
adduser famcal
usermod -aG sudo famcal

# Set up firewall
ufw allow OpenSSH
ufw allow 80
ufw allow 443
ufw enable

# Switch to the new user
su - famcal
```

## 4. Quick Setup (Automated)

For automatic installation of all dependencies and database setup:

```bash
cd ~
git clone https://github.com/ReubenCowell/famcal.git
cd famcal
chmod +x setup_production.sh
./setup_production.sh
```

This script will:
- ✓ Check and install all required system packages
- ✓ Set up Python virtual environment
- ✓ Install Python dependencies
- ✓ Configure PostgreSQL database
- ✓ Create `.env` file with database credentials
- ✓ Test the application

**Then skip to step 8 for systemd setup.**

---

## 4. Manual Setup (Alternative)

If you prefer to set up manually, follow steps 5-7:

### 5. Install dependencies

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv nginx certbot python3-certbot-nginx git postgresql postgresql-contrib
```

### 6. Clone and set up the app

```bash
cd ~
git clone https://github.com/ReubenCowell/famcal.git
cd famcal
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 7. Set up PostgreSQL database

```bash
# Create database and user
sudo -u postgres psql << 'EOF'
CREATE DATABASE famcal_db;
CREATE USER famcal_user WITH ENCRYPTED PASSWORD 'CHANGE_THIS_PASSWORD';
GRANT ALL PRIVILEGES ON DATABASE famcal_db TO famcal_user;
\c famcal_db
GRANT ALL ON SCHEMA public TO famcal_user;
\q
EOF
```

**Important:** Replace `CHANGE_THIS_PASSWORD` with a strong password. Save it — you'll need it for the next step.

---

## 8. Test it works (Manual setup only)

```bash
# Test with database enabled
export FAMCAL_USE_DATABASE=true
export SQLALCHEMY_DATABASE_URL="postgresql://famcal_user:CHANGE_THIS_PASSWORD@localhost:5432/famcal_db"
python family_calendar_server.py --config family_config.json
```

Open `http://<your-droplet-ip>:8000` in a browser. Press `Ctrl+C` to stop.

---

## 9. Set up Gunicorn as a systemd service

Create the service file:

```bash
sudo tee /etc/systemd/system/famcal.service << 'EOF'
[Unit]
Description=Family Calendar Server
After=network.target postgresql.service

[Service]
Type=simple
User=famcal
Group=famcal
WorkingDirectory=/home/famcal/famcal
Environment="PATH=/home/famcal/famcal/.venv/bin"
Environment="FAMCAL_USE_DATABASE=true"
Environment="FAMCAL_DATABASE_ICS=true"
Environment="SQLALCHEMY_DATABASE_URL=postgresql://famcal_user:CHANGE_THIS_PASSWORD@localhost:5432/famcal_db"
ExecStart=/home/famcal/famcal/.venv/bin/gunicorn wsgi:application -b 127.0.0.1:8000 -w 2 --timeout 60
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
```

**Important:** Replace `CHANGE_THIS_PASSWORD` with the password you set in step 6.

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable famcal
sudo systemctl start famcal
sudo systemctl status famcal
```

## 10. Set up Nginx reverse proxy

```bash
sudo tee /etc/nginx/sites-available/famcal << 'EOF'
server {
    listen 80;
    server_name <your-droplet-ip>;

    location /static/ {
        alias /home/famcal/famcal/static/;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF

sudo ln -s /etc/nginx/sites-available/famcal /etc/nginx/sites-enabled/
sudo rm /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx
```

Your calendar is now live at `http://<your-droplet-ip>/`

## 11. (Optional) Custom domain + HTTPS

If you have a domain name:

1. Point your domain's A record to your Droplet IP
2. Update the Nginx config — replace `<your-droplet-ip>` with your domain:
   ```bash
   sudo sed -i 's/<your-droplet-ip>/yourdomain.com/' /etc/nginx/sites-available/famcal
   sudo nginx -t && sudo systemctl restart nginx
   ```
3. Get a free SSL certificate:
   ```bash
   sudo certbot --nginx -d yourdomain.com
   ```
4. Update `domain` in `family_config.json` so ICS feed URLs use the right hostname:
   ```json
   "domain": "yourdomain.com"
   ```

## 12. Set up your calendars

Go to `http://<your-droplet-ip>/admin` (or `https://yourdomain.com/admin`) and add your family members and calendar URLs.

## Managing the server


| Task           | Command                                                    |
| ---------------- | ------------------------------------------------------------ |
| View logs      | `sudo journalctl -u famcal -f`                             |
| Restart server | `sudo systemctl restart famcal`                            |
| Stop server    | `sudo systemctl stop famcal`                               |
| Update code    | `cd ~/famcal && git pull && sudo systemctl restart famcal` |
| Check status   | `sudo systemctl status famcal`                             |

## Updating

```bash
cd ~/famcal
git pullhttps://github.com/ReubenCowell/famcal.git
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart famcal
```
