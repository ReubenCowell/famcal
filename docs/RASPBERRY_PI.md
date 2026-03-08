# Raspberry Pi Deployment

Deploy the Family Calendar Server on a Raspberry Pi for always-on local hosting.

## Requirements

- Raspberry Pi 3B or newer
- Raspberry Pi OS (Lite or Desktop)
- MicroSD card (8GB+, A1 rated recommended)
- 5V / 2.5A+ power supply (Pi 3B) or 5V / 3A USB-C (Pi 4/5)
- Network connection (Ethernet or Wi-Fi)

## Step-by-step

### 1. Copy files to your Pi

```bash
ssh pi@raspberrypi.local
cd ~
git clone <your-repo-url> famcal
cd famcal
```

### 2. Install Python and dependencies

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Test it runs

```bash
python family_calendar_server.py --config family_config.json
```

Open `http://raspberrypi.local:8000` in a browser to confirm. Press `Ctrl+C` to stop.

### 4. Install as a system service (auto-starts on boot)

```bash
sudo cp famcal.service /etc/systemd/system/famcal.service
sudo systemctl daemon-reload
sudo systemctl enable famcal
sudo systemctl start famcal
```

### 5. Check it's running

```bash
sudo systemctl status famcal
```

### 6. View logs

```bash
sudo journalctl -u famcal -f
```

## Or use the setup script

```bash
cd ~/famcal
./setup_pi.sh
sudo systemctl start famcal
```

This script copies files, installs dependencies, and registers the systemd service.

## Headless Setup (No Monitor)

If your Pi has no keyboard/monitor attached:

1. Flash Raspberry Pi OS Lite with [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
2. In the imager settings (gear icon), enable SSH, set your Wi-Fi credentials, and set a hostname
3. Insert the SD card and power on the Pi
4. Wait ~2 minutes, then SSH in:
   ```bash
   ssh pi@raspberrypi.local
   ```
5. Follow the steps above

## Accessing from other devices

Once running, the calendar is available to any device on your local network at:

| Page | URL |
|------|-----|
| Calendar | `http://raspberrypi.local:8000/` |
| Admin | `http://raspberrypi.local:8000/admin` |
| ICS feed | `http://raspberrypi.local:8000/<member_id>/calendar.ics` |

If `.local` doesn't resolve, use the Pi's IP address (find it with `hostname -I` on the Pi).
