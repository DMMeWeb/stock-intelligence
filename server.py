import hashlib
import hmac
import json
import mimetypes
import os
import subprocess
import threading
import time
from datetime import datetime, timedelta
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs
try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None


ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
ENV_PATH = ROOT / ".env"
LONDON = ZoneInfo("Europe/London") if ZoneInfo else None

STATUS = {
    "running": False,
    "lastStartedAt": None,
    "lastFinishedAt": None,
    "lastSuccess": None,
    "lastMessage": "Not synced by server yet.",
    "nextRunAt": None,
}
STATUS_LOCK = threading.Lock()


def load_env():
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def env(name, default=""):
    return os.environ.get(name, default).strip()


def now_london():
    return datetime.now(LONDON) if LONDON else datetime.now()


def iso_london(dt):
    return dt.isoformat(timespec="seconds")


def next_sync_time():
    sync_time = env("SYNC_TIME", "08:00")
    if ":" not in sync_time:
        sync_time = "08:00"
    hour_text, minute_text = sync_time.split(":", 1)
    now = now_london()
    target = now.replace(hour=int(hour_text), minute=int(minute_text), second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def sign(value):
    secret = env("APP_SESSION_SECRET") or env("APP_PASSWORD") or "dev-secret"
    return hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


def session_value():
    value = str(int(time.time()))
    return f"{value}.{sign(value)}"


def valid_session(raw):
    if not raw or "." not in raw:
        return False
    value, signature = raw.rsplit(".", 1)
    if not hmac.compare_digest(signature, sign(value)):
        return False
    try:
        issued_at = int(value)
    except ValueError:
        return False
    return time.time() - issued_at < 60 * 60 * 24 * 14


def get_status():
    with STATUS_LOCK:
        return dict(STATUS)


def update_status(**kwargs):
    with STATUS_LOCK:
        STATUS.update(kwargs)


def run_sync():
    with STATUS_LOCK:
        if STATUS["running"]:
            return False
        STATUS["running"] = True
        STATUS["lastStartedAt"] = iso_london(now_london())
        STATUS["lastMessage"] = "Sync running..."

    try:
        result = subprocess.run(
            ["python3", "scripts/sync-shopify.py"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=300,
        )
        success = result.returncode == 0
        message = result.stdout.strip() if success else (result.stderr.strip() or result.stdout.strip())
        if len(message) > 1200:
            message = message[-1200:]
        update_status(
            running=False,
            lastFinishedAt=iso_london(now_london()),
            lastSuccess=success,
            lastMessage=message or ("Sync complete." if success else "Sync failed."),
            nextRunAt=iso_london(next_sync_time()),
        )
    except Exception as exc:
        update_status(
            running=False,
            lastFinishedAt=iso_london(now_london()),
            lastSuccess=False,
            lastMessage=str(exc),
            nextRunAt=iso_london(next_sync_time()),
        )
    return True


def scheduler_loop():
    while True:
        target = next_sync_time()
        update_status(nextRunAt=iso_london(target))
        wait_seconds = max(1, (target - now_london()).total_seconds())
        time.sleep(wait_seconds)
        threading.Thread(target=run_sync, daemon=True).start()
        time.sleep(60)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def do_HEAD(self):
        self.send_response(200 if self.is_authenticated() else 303)
        if not self.is_authenticated():
            self.send_header("Location", "/login")
        self.end_headers()

    def is_authenticated(self):
        jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        morsel = jar.get("stock_session")
        return valid_session(morsel.value if morsel else "")

    def send_text(self, status, body, content_type="text/plain; charset=utf-8", extra_headers=None):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, location):
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def do_GET(self):
        if self.path == "/login":
            return self.send_login()

        if self.path == "/logout":
            self.send_response(303)
            self.send_header("Location", "/login")
            self.send_header("Set-Cookie", "stock_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
            self.end_headers()
            return

        path = self.path.split("?", 1)[0]
        if path == "/styles.css" or path.startswith("/assets/"):
            return self.send_public_file(path)

        if not self.is_authenticated():
            return self.redirect("/login")

        if self.path == "/api/status":
            return self.send_json(get_status())

        if path == "/":
            path = "/index.html"
        return self.send_public_file(path)

    def send_public_file(self, path):
        file_path = (PUBLIC / path.lstrip("/")).resolve()
        if not str(file_path).startswith(str(PUBLIC.resolve())) or not file_path.is_file():
            return self.send_text(404, "Not found")
        mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path == "/login":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            password = parse_qs(body).get("password", [""])[0]
            if password and hmac.compare_digest(password, env("APP_PASSWORD", "")):
                self.send_response(303)
                self.send_header("Location", "/")
                self.send_header("Set-Cookie", f"stock_session={session_value()}; Path=/; HttpOnly; SameSite=Lax")
                self.end_headers()
                return
            return self.send_login("Incorrect password.")

        if not self.is_authenticated():
            return self.send_json({"error": "Not authenticated"}, 401)

        if self.path == "/api/sync":
            started = run_sync()
            return self.send_json({"started": started, **get_status()})

        return self.send_json({"error": "Not found"}, 404)

    def send_json(self, payload, status=200):
        self.send_text(status, json.dumps(payload), "application/json; charset=utf-8")

    def send_login(self, error=""):
        message = f"<p class=\"error\">{error}</p>" if error else ""
        html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Stock Intelligence Login</title>
    <link rel="icon" type="image/png" href="/assets/faune-favicon.png">
    <link rel="apple-touch-icon" href="/assets/faune-favicon.png">
    <link rel="stylesheet" href="/styles.css">
  </head>
  <body class="login-page">
    <main class="login-shell">
      <img class="login-logo" src="/assets/faune-logo.png" alt="Faune">
      <section class="login-panel">
        <h1>Stock Intelligence</h1>
        <form method="post" action="/login">
          <label>Password
            <input name="password" type="password" autofocus required>
          </label>
          {message}
          <button class="button" type="submit">Enter</button>
        </form>
      </section>
    </main>
  </body>
</html>"""
        self.send_text(200, html, "text/html; charset=utf-8")


def main():
    load_env()
    try:
        update_status(nextRunAt=iso_london(next_sync_time()))
        threading.Thread(target=scheduler_loop, daemon=True).start()
    except Exception as exc:
        update_status(lastMessage=f"Scheduler failed to start: {exc}")
        print(f"Scheduler failed to start: {exc}", flush=True)
    if env("SYNC_ON_STARTUP", "").lower() in {"1", "true", "yes"}:
        threading.Thread(target=run_sync, daemon=True).start()
    try:
        port = int(env("PORT", "4173"))
    except ValueError:
        port = 4173
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Stock Intelligence running on 0.0.0.0:{port}", flush=True)
    print(f"Next Shopify sync: {get_status()['nextRunAt']}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
