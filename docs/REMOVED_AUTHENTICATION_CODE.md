# Removed Authentication Code

This file contains the password authentication code that was removed from the Family Calendar Server. It's preserved here in case you want to re-implement authentication in the future.

## Server Configuration Changes

### ServerConfig Dataclass
```python
@dataclass
class ServerConfig:
    """Server configuration."""
    refresh_interval_seconds: int = 3600
    host: str = "0.0.0.0"
    port: int = 8000
    domain: str | None = None
    password_hash: str = ""  # REMOVED
    secret_key: str = ""      # REMOVED
```

### Config Loading
```python
# In load_config():
self.server_config = ServerConfig(
    refresh_interval_seconds=server_settings.get("refresh_interval_seconds", 3600),
    host=server_settings.get("host", "0.0.0.0"),
    port=server_settings.get("port", 8000),
    domain=server_settings.get("domain"),
    password_hash=server_settings.get("password_hash", ""),      # REMOVED
    secret_key=server_settings.get("secret_key", "")            # REMOVED
)
```

### Config Saving
```python
# In save_config():
"server_settings": {
    "refresh_interval_seconds": self.server_config.refresh_interval_seconds,
    "host": self.server_config.host,
    "port": self.server_config.port,
    "domain": self.server_config.domain or "",
    "password_hash": self.server_config.password_hash,  # REMOVED
    "secret_key": self.server_config.secret_key         # REMOVED
}
```

## Flask App Authentication Code

### Imports
```python
import secrets
from flask import Flask, Response, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
```

### App Initialization
```python
def create_app(manager: FamilyCalendarManager, fetch_timeout: int) -> Flask:
    """Create Flask application."""
    app = Flask(__name__)
    app.json.sort_keys = False
    
    # Use a persistent secret key so sessions survive across Gunicorn workers and restarts
    if not manager.server_config.secret_key:
        manager.server_config.secret_key = secrets.token_hex(32)
        manager.save_config()
    app.secret_key = os.getenv("SECRET_KEY", manager.server_config.secret_key)
```

### Authentication Middleware
```python
# ===== Authentication =====
PUBLIC_PATHS = {"/login", "/static"}

@app.before_request
def require_login():
    """Require password for all routes except login, static files, and ICS feeds."""
    if not manager.server_config.password_hash:
        return  # No password set, skip auth
    path = request.path
    if path.startswith("/static") or path == "/login":
        return
    # Allow ICS feed URLs without auth (for calendar app subscriptions)
    if path.endswith("/calendar.ics"):
        return
    if not session.get("authenticated"):
        return redirect(url_for("login"))
```

### Login Routes
```python
@app.route("/login", methods=["GET", "POST"])
def login():
    """Login page with password-only authentication."""
    if not manager.server_config.password_hash:
        return redirect("/")
    if request.method == "POST":
        password = request.form.get("password", "")
        if check_password_hash(manager.server_config.password_hash, password):
            session["authenticated"] = True
            return redirect("/")
        return render_template("login.html", error="Incorrect password. Please try again.")
    return render_template("login.html")

@app.get("/logout")
def logout():
    """Clear session and redirect to login."""
    session.clear()
    return redirect("/login")
```

### Password Management API
```python
@app.post("/api/admin/set-password")
def api_set_password():
    """Set or update the app password."""
    data = request.get_json()
    password = data.get("password", "").strip()
    if len(password) < 4:
        return jsonify({"success": False, "error": "Password must be at least 4 characters"}), 400
    manager.server_config.password_hash = generate_password_hash(password)
    manager.save_config()
    session["authenticated"] = True
    return jsonify({"success": True, "message": "Password updated"})

@app.post("/api/admin/remove-password")
def api_remove_password():
    """Remove the app password (make it public)."""
    manager.server_config.password_hash = ""
    manager.save_config()
    return jsonify({"success": True, "message": "Password removed"})
```

## Admin UI Changes

### Nav Bar (admin.html)
```html
<!-- REMOVED logout link -->
<nav class="top-nav">
    <span class="brand">&#128197; Family Calendar</span>
    <div class="nav-links">
        <a href="/">Calendar</a>
        <a href="/admin" class="active">Admin</a>
        <a href="/logout" id="logoutLink" style="display:none;">Logout</a>
        <button class="theme-toggle" title="Toggle theme">&#9684;</button>
    </div>
</nav>
```

### Password Protection Section (admin.html)
```html
<!-- Password Section -->
<div class="member-card" style="margin-bottom:var(--sp-6);">
    <div class="member-card-header">
        <div class="member-title"><div><h2>&#128274; Password Protection</h2></div></div>
    </div>
    <div class="member-card-body">
        <p id="pwStatus" style="margin-bottom:var(--sp-3);font-size:.875rem;color:var(--text-secondary);">Checking...</p>
        <div style="display:flex;gap:var(--sp-2);align-items:flex-end;flex-wrap:wrap;">
            <div class="form-group" style="margin-bottom:0;flex:1;min-width:200px;">
                <label for="fPassword">New Password</label>
                <input type="password" id="fPassword" placeholder="Enter new password" style="width:100%;padding:var(--sp-2) var(--sp-3);border:1px solid var(--border-strong);border-radius:var(--radius-sm);font-size:.875rem;">
            </div>
            <button class="btn btn-primary" onclick="setPassword()">Set Password</button>
            <button class="btn btn-danger" id="btnRemovePw" onclick="removePassword()" style="display:none;">Remove Password</button>
        </div>
    </div>
</div>
```

### JavaScript Functions (admin.html)
```javascript
loadData();
checkPasswordStatus();  // REMOVED: This call

async function checkPasswordStatus() {
    try {
        // Check if we can reach the login page (it redirects to / if no password)
        const res = await fetch('/login', { redirect: 'manual' });
        // If there's a password set, show the status
        if (res.type === 'opaqueredirect' || res.redirected) {
            document.getElementById('pwStatus').textContent = 'No password set. The app is publicly accessible.';
            document.getElementById('btnRemovePw').style.display = 'none';
            document.getElementById('logoutLink').style.display = 'none';
        } else {
            const html = await res.text();
            if (html.includes('Enter the family password')) {
                document.getElementById('pwStatus').innerHTML = '&#9989; Password is set. The app is protected.';
                document.getElementById('btnRemovePw').style.display = '';
                document.getElementById('logoutLink').style.display = '';
            } else {
                document.getElementById('pwStatus').textContent = 'No password set. The app is publicly accessible.';
                document.getElementById('btnRemovePw').style.display = 'none';
                document.getElementById('logoutLink').style.display = 'none';
            }
        }
    } catch {
        document.getElementById('pwStatus').textContent = 'No password set. The app is publicly accessible.';
    }
}

window.setPassword = async function() {
    const pw = document.getElementById('fPassword').value;
    if (!pw || pw.length < 4) { toast('Password must be at least 4 characters', 'error'); return; }
    try {
        const res = await fetch('/api/admin/set-password', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password: pw })
        });
        const r = await res.json();
        if (r.success) {
            toast('Password set!', 'success');
            document.getElementById('fPassword').value = '';
            checkPasswordStatus();
        } else toast(r.error, 'error');
    } catch { toast('Failed to set password', 'error'); }
};

window.removePassword = async function() {
    if (!confirm('Remove password? The app will be publicly accessible.')) return;
    try {
        const res = await fetch('/api/admin/remove-password', { method: 'POST' });
        const r = await res.json();
        if (r.success) { toast('Password removed', 'success'); checkPasswordStatus(); }
        else toast(r.error, 'error');
    } catch { toast('Failed to remove password', 'error'); }
};
```

## Login Template (templates/login.html)

The entire `templates/login.html` file was used for authentication and can be deleted.

## To Re-implement Authentication

1. Add back the removed imports at the top of `family_calendar_server.py`
2. Add `password_hash` and `secret_key` fields back to `ServerConfig` dataclass
3. Add the authentication middleware and routes to `create_app()`
4. Add the password protection UI section back to `templates/admin.html`
5. Add the JavaScript functions back to `templates/admin.html`
6. Create `templates/login.html` if needed

## Security Considerations

If you re-implement authentication:
- Use strong password hashing (werkzeug.security uses pbkdf2)
- Consider using environment variables for secrets
- Use HTTPS in production
- Consider adding rate limiting to prevent brute force
- Consider adding session timeouts
- Consider using proper OAuth or SSO instead of simple passwords
