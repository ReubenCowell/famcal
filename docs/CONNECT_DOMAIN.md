# How to Connect a Domain to Your Family Calendar Server

This guide walks you through buying a domain, pointing it at your DigitalOcean Droplet, and setting up HTTPS — all in plain English.

> **Prerequisites:** Your server is already running on a DigitalOcean Droplet with Nginx set up (i.e. you've completed the steps in `DEPLOY_DIGITALOCEAN.md` up through step 8).

---

## Step 1: Buy a Domain Name

You need a domain name (e.g. `calendar.cowellfamily.com` or `famcal.xyz`). If you already own one, skip to Step 2.

Popular registrars:

- [Namecheap](https://www.namecheap.com) — cheap `.xyz`, `.me`, `.dev` domains (often under $5/year)
- [Cloudflare Registrar](https://www.cloudflare.com/products/registrar/) — at-cost pricing, no markup
- [Google Domains (Squarespace)](https://domains.squarespace.com/)
- [Porkbun](https://porkbun.com/) — very cheap TLDs

Pick a domain and purchase it. You'll need access to its **DNS settings** in the next step.

---

## Step 2: Point Your Domain at Your Server

You need to create a **DNS A record** that tells the internet "this domain goes to my server's IP address."

1. **Find your Droplet's IP address.** You can see this in the DigitalOcean dashboard, or by running on your server:

   ```
   curl -4 ifconfig.me
   ```
2. **Go to your domain registrar's DNS settings** (the website where you bought the domain).
3. **Add an A record:**


   | Field     | Value                                                                 |
   | ----------- | ----------------------------------------------------------------------- |
   | **Type**  | A                                                                     |
   | **Name**  | `@` (or leave blank — this means the root domain, e.g. `famcal.xyz`) |
   | **Value** | Your Droplet's IP address (e.g.`188.166.175.212`)                     |
   | **TTL**   | Automatic / 300                                                       |
4. **(Optional) Add a www subdomain too:**


   | Field     | Value           |
   | ----------- | ----------------- |
   | **Type**  | A               |
   | **Name**  | `www`           |
   | **Value** | Same Droplet IP |
   | **TTL**   | Automatic / 300 |
5. **Wait for DNS to propagate.** This usually takes 5–30 minutes, but can take up to 48 hours. You can check progress at [dnschecker.org](https://dnschecker.org/).

### How to verify it worked

From your local machine (not the server), run:

```bash
ping yourdomain.com
```

If it shows your Droplet's IP address, DNS is working.

---

## Step 3: Update Nginx to Use Your Domain

SSH into your server:

```bash
ssh root@<your-droplet-ip>
```

(or `ssh famcal@<your-droplet-ip>` if you set up a non-root user)

Edit the Nginx config:

```bash
sudo nano /etc/nginx/sites-available/famcal
```

Find the `server_name` line and replace the IP address with your domain:

```nginx
server {
    listen 80;
    server_name yourdomain.com www.yourdomain.com;

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
```

> **Important:** Replace `yourdomain.com` with your actual domain in both places.

Test and restart Nginx:

```bash
sudo nginx -t
sudo systemctl restart nginx
```

Visit `http://yourdomain.com` in your browser — you should see your calendar. (It will say "Not Secure" in the address bar — we'll fix that next.)

---

## Step 4: Get a Free SSL Certificate (HTTPS)

This gives you the padlock icon and encrypts all traffic. We use **Let's Encrypt** via Certbot — it's completely free.

1. **Make sure Certbot is installed** (it should be if you followed the deploy guide):

   ```bash
   sudo apt install -y certbot python3-certbot-nginx
   ```
2. **Run Certbot:**

   ```bash
   sudo certbot --nginx -d yourdomain.com -d www.yourdomain.com
   ```

   - It will ask for your email address (for renewal notices) — enter it.
   - Agree to the terms of service.
   - Choose whether to share your email with the EFF (optional).
   - Certbot will automatically edit your Nginx config to add SSL.

   > **Note:** If you didn't set up the `www` subdomain in Step 2, leave off the `-d www.yourdomain.com` part.
   >
3. **Verify it works:** Visit `https://yourdomain.com` — you should see a padlock and your calendar.
4. **Auto-renewal is set up automatically.** Certbot adds a systemd timer that renews the certificate before it expires. You can verify with:

   ```bash
   sudo certbot renew --dry-run
   ```

---

## Step 5: Update Your App Config

Edit your `family_config.json` on the server so the app generates correct ICS feed URLs:

```bash
nano ~/famcal/family_config.json
```

Update the `domain` field:

```json
"server_settings": {
    "refresh_interval_seconds": 3600,
    "host": "0.0.0.0",
    "port": 8000,
    "domain": "yourdomain.com",
    "password_hash": "..."
}
```

Restart the app:

```bash
sudo systemctl restart famcal
```

---

## Step 6: (Optional) Force HTTPS

To make sure nobody accidentally uses the insecure `http://` version, Certbot usually handles this automatically. If it didn't, add a redirect block to your Nginx config:

```bash
sudo nano /etc/nginx/sites-available/famcal
```

Make sure there's a server block like this that redirects HTTP to HTTPS:

```nginx
server {
    listen 80;
    server_name yourdomain.com www.yourdomain.com;
    return 301 https://$host$request_uri;
}
```

Then test and restart:

```bash
sudo nginx -t
sudo systemctl restart nginx
```

---

## Quick Checklist

- [ ] Domain purchased
- [ ] A record points to your Droplet IP
- [ ] `server_name` in Nginx updated to your domain
- [ ] Certbot SSL certificate installed
- [ ] `domain` field updated in `family_config.json`
- [ ] App restarted
- [ ] `https://yourdomain.com` loads your calendar with a padlock

---

## Troubleshooting


| Problem                                | Fix                                                                                                               |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| Site not loading after DNS change      | Wait longer (up to 48 hours). Check[dnschecker.org](https://dnschecker.org/).                                     |
| "Welcome to nginx" default page        | Make sure you removed the default site:`sudo rm /etc/nginx/sites-enabled/default && sudo systemctl restart nginx` |
| Certbot fails with "Could not connect" | DNS hasn't propagated yet, or port 80 is blocked. Check:`sudo ufw status` — port 80 and 443 must be open.        |
| Certbot fails with "too many requests" | You've hit Let's Encrypt rate limits. Wait an hour and try again.                                                 |
| ICS feed URLs still show the IP        | Update`domain` in `family_config.json` and restart: `sudo systemctl restart famcal`                               |
| "Connection refused" on HTTPS          | Port 443 not open:`sudo ufw allow 443 && sudo ufw reload`                                                         |
