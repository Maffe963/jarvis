"""Sign in once to an online account (Spotify, Google) and keep Jarvis's access fresh afterwards.

Uses the standard browser sign-in for desktop apps: Jarvis opens the provider's page, you approve, and the
provider sends you back to a one-off page on this PC (127.0.0.1) that hands Jarvis the result. Tokens are
saved in memory/tokens (never uploaded) and refreshed automatically.
"""
import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

TOKENS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory", "tokens")


class NotConnected(Exception):
    """The account hasn't been signed in yet (or access was revoked)."""


def _path(name: str) -> str:
    return os.path.join(TOKENS_DIR, f"{name}.json")


def _post(url: str, fields: dict, basic_auth: tuple[str, str] | None = None) -> dict:
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if basic_auth:
        headers["Authorization"] = "Basic " + base64.b64encode(f"{basic_auth[0]}:{basic_auth[1]}".encode()).decode()
    req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{e.code}: {e.read().decode('utf-8', 'replace')[:300]}") from None


def _save(name: str, tokens: dict) -> None:
    os.makedirs(TOKENS_DIR, exist_ok=True)
    tokens = dict(tokens)
    tokens["expires_at"] = time.time() + int(tokens.get("expires_in", 3600)) - 60
    with open(_path(name), "w", encoding="utf-8") as f:
        json.dump(tokens, f, indent=2)


def authorize(name: str, auth_url: str, token_url: str, client_id: str, scopes: list[str], port: int,
              client_secret: str = "", extra: dict | None = None) -> None:
    """Run the browser sign-in and save the tokens. Blocks until you approve (or 5 minutes pass)."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(16)
    redirect = f"http://127.0.0.1:{port}/callback"
    result: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if query.get("state", [""])[0] != state:
                self.send_error(400)
                return
            result.update({k: v[0] for k, v in query.items()})
            ok = "code" in result
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(("<h2>" + ("Connected! You can close this tab and go back to Jarvis."
                                        if ok else f"Not connected: {result.get('error', 'unknown error')}")
                              + "</h2>").encode())

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", port), Handler)
    server.timeout = 300
    params = {"client_id": client_id, "response_type": "code", "redirect_uri": redirect, "scope": " ".join(scopes),
              "state": state, "code_challenge_method": "S256", "code_challenge": challenge, **(extra or {})}
    webbrowser.open(f"{auth_url}?{urllib.parse.urlencode(params)}")
    print("A browser tab opened: sign in and approve Jarvis there.")
    server.handle_request()
    server.server_close()
    if "code" not in result:
        raise RuntimeError(f"Sign-in didn't finish ({result.get('error', 'no answer within 5 minutes')}).")
    fields = {"grant_type": "authorization_code", "code": result["code"], "redirect_uri": redirect,
              "client_id": client_id, "code_verifier": verifier}
    if client_secret:
        fields["client_secret"] = client_secret
    _save(name, _post(token_url, fields))


_refresh_lock = threading.Lock()


def access_token(name: str, token_url: str, client_id: str, client_secret: str = "") -> str:
    """A valid access token, refreshing it if needed. Raises NotConnected if never signed in."""
    with _refresh_lock:
        try:
            with open(_path(name), "r", encoding="utf-8") as f:
                tokens = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            raise NotConnected(name) from None
        if time.time() < tokens.get("expires_at", 0):
            return tokens["access_token"]
        if not tokens.get("refresh_token"):
            raise NotConnected(name)
        fields = {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": client_id}
        if client_secret:
            fields["client_secret"] = client_secret
        try:
            fresh = _post(token_url, fields)
        except RuntimeError as e:
            if "invalid_grant" in str(e):  # revoked, or (Google "testing" apps) expired after 7 days
                raise NotConnected(name) from None
            raise
        fresh.setdefault("refresh_token", tokens["refresh_token"])  # providers may not resend it
        _save(name, fresh)
        return fresh["access_token"]


def api(method: str, url: str, token: str, body: dict | None = None, params: dict | None = None) -> dict | None:
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    data = json.dumps(body).encode() if body is not None else (b"" if method in ("POST", "PUT") else None)
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else None
