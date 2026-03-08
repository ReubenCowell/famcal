# Deploy to PythonAnywhere (Free)

PythonAnywhere offers free Flask hosting — no server to manage.

Your app will be available at `https://<your-username>.pythonanywhere.com`.

## 1. Create an account

Go to [pythonanywhere.com](https://www.pythonanywhere.com/) and sign up for a free **Beginner** account.

## 2. Upload your files

**Option A — via git (recommended):**

1. Go to the **Consoles** tab → start a **Bash** console
2. Run:
   ```bash
   cd ~
   git clone <your-repo-url> famcal
   ```

**Option B — via the web UI:**

1. Go to the **Files** tab
2. Navigate to `/home/<your-username>/`
3. Create a folder called `famcal`
4. Upload all the project files into it (including `templates/` and `static/` folders)

## 3. Set up the virtual environment

In a PythonAnywhere Bash console:

```bash
cd ~/famcal
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 4. Create the web app

1. Go to the **Web** tab
2. Click **Add a new web app**
3. Choose **Manual configuration** (not Flask — we need manual for WSGI)
4. Select **Python 3.10** (or whichever version matches your venv)
5. Click Next/Done

## 5. Configure WSGI

1. On the **Web** tab, find **WSGI configuration file** and click the link (e.g., `/var/www/<username>_pythonanywhere_com_wsgi.py`)
2. **Delete everything** in that file and replace it with:

   ```python
   import sys
   import os

   project_dir = '/home/<your-username>/famcal'
   if project_dir not in sys.path:
       sys.path.insert(0, project_dir)
   os.chdir(project_dir)
   os.environ['FAMILY_CONFIG'] = os.path.join(project_dir, 'family_config.json')

   from wsgi import application  # noqa
   ```

   Replace `<your-username>` with your actual PythonAnywhere username.

3. Click **Save**

## 6. Set the virtualenv path

On the **Web** tab, under **Virtualenv**, enter:

```
/home/<your-username>/famcal/.venv
```

## 7. Set static files

On the **Web** tab, under **Static files**, add:

| URL | Directory |
|-----|-----------|
| `/static/` | `/home/<your-username>/famcal/static` |

## 8. Reload

Click the green **Reload** button on the **Web** tab.

Visit `https://<your-username>.pythonanywhere.com` — your calendar is live!

## 9. Set up your calendars

Go to `https://<your-username>.pythonanywhere.com/admin` and add your family members and calendar URLs.

## Notes

- **Free tier limits**: one web app, your-username.pythonanywhere.com domain, and outbound HTTP only to a whitelist of sites. Google Calendar and Outlook URLs are on the whitelist. If a calendar URL is blocked, you may need a paid account ($5/mo).
- **Scheduled refresh**: Free tier doesn't run background threads reliably. Calendars refresh when the app restarts (daily on free tier) or when you click Refresh in admin. For automatic refreshes, go to the **Tasks** tab and add a scheduled task:
  ```bash
  cd ~/famcal && source .venv/bin/activate && python -c "from family_calendar_server import *; import pathlib; m=FamilyCalendarManager(pathlib.Path('family_config.json')); refresh_all_calendars(m, 30)"
  ```
- **Custom domain**: Available on paid accounts ($5/mo). Set `domain` in `family_config.json` to your custom domain.
- **Updating**: Pull new code (`git pull` in a Bash console) then click **Reload** on the Web tab.
