#!/usr/bin/env python3
"""gads_auth.py — one-time OAuth setup for the Google Ads connector.

Run: py scripts\\gads_auth.py
Needs GADS_CLIENT_ID and GADS_CLIENT_SECRET already in .env (Desktop-app
OAuth client from Google Cloud console). Opens your browser, you approve
the Google Ads scope, and the refresh token is written straight into .env.
The token is never printed to the console.
"""
import http.server
import json
import secrets
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
PORT = 8765
SCOPE = "https://www.googleapis.com/auth/adwords"


def load_env():
    env = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def save_env_key(key, value):
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    hit = False
    for i, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == key:
            lines[i] = f"{key}={value}"
            hit = True
    if not hit:
        lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    env = load_env()
    cid = env.get("GADS_CLIENT_ID")
    secret = env.get("GADS_CLIENT_SECRET")
    if not cid or not secret:
        sys.exit("Add GADS_CLIENT_ID and GADS_CLIENT_SECRET to .env first "
                 "(Google Cloud console → Credentials → OAuth client ID → Desktop app).")

    state = secrets.token_urlsafe(16)
    redirect = f"http://127.0.0.1:{PORT}/"
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent", "state": state,
    })

    result = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            ok = q.get("state", [""])[0] == state and "code" in q
            if ok:
                result["code"] = q["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h2>%s</h2>You can close this tab." %
                             (b"Authorised - back to the terminal." if ok else b"Auth failed - check terminal."))

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()

    print("Opening browser for Google sign-in (approve the Google Ads scope)...")
    webbrowser.open(auth_url)
    server_thread_deadline = 300
    import time
    t0 = time.time()
    while "code" not in result and time.time() - t0 < server_thread_deadline:
        time.sleep(0.5)
    server.server_close()
    if "code" not in result:
        sys.exit("Timed out after 5 minutes without an authorisation code.")

    body = urllib.parse.urlencode({
        "client_id": cid, "client_secret": secret, "code": result["code"],
        "grant_type": "authorization_code", "redirect_uri": redirect,
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=body)
    with urllib.request.urlopen(req, timeout=30) as resp:
        tok = json.loads(resp.read().decode())

    refresh = tok.get("refresh_token")
    if not refresh:
        sys.exit("No refresh token returned (re-run; 'prompt=consent' should force one).")
    save_env_key("GADS_REFRESH_TOKEN", refresh)
    print("OK: GADS_REFRESH_TOKEN saved to .env (not displayed).")
    print("Next: py scripts\\gads.py stats")


if __name__ == "__main__":
    main()
