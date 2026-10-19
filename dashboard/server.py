"""The live agent dashboard: a page on this PC (http://127.0.0.1:8765) showing Jarvis and its agents.

The agents run in the team service (team_service.py); the page shows them through team/client.py, merged with
Jarvis's own events (the command box, theme changes).

Every request must carry the dashboard's secret token (it's in the link Jarvis opens), so other websites open in
the browser can't send Jarvis commands through it. It only listens on this PC; to open it from your phone when
you're out, put it on your private Tailscale network (`tailscale serve --bg 8765`) and set JARVIS_DASHBOARD_URL to
the address Tailscale gives you. The token then stays the same across restarts (memory/dashboard_token, delete it
to make a new one) so the link keeps working; Telegram's /dashboard sends it to your phone.
"""
import hashlib
import http.cookies
import json
import os
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from dashboard import insights
from team import approvals, client, events

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("JARVIS_DASHBOARD_PORT", "8765"))
TOKEN_FILE = os.path.join(os.path.dirname(HERE), "memory", "dashboard_token")
HISTORY_FILE = os.path.join(os.path.dirname(HERE), "memory", "dashboard_history.json")
PUBLIC_URL = os.environ.get("JARVIS_DASHBOARD_URL", "").strip().rstrip("/")  # e.g. https://my-pc.tail1234.ts.net
PASSWORD = os.environ.get("JARVIS_DASHBOARD_PASSWORD", "").strip()  # lets any browser sign in, without the secret link
SESSIONS_FILE = os.path.join(os.path.dirname(HERE), "memory", "dashboard_sessions.json")
SESSION_DAYS = 30
MAX_FAILURES = 5          # wrong passwords allowed per FAILURE_WINDOW_S from one address before it's paused
MAX_ALL_FAILURES = 30     # from everywhere together; past that the password pauses for everyone (the /dashboard link
FAILURE_WINDOW_S = 15 * 60  # from Telegram still works, so you're never locked out)
MAX_UPLOAD_BYTES = 50_000_000  # biggest file the dashboard takes


def _token() -> str:
    """A new secret each run, unless the dashboard is reachable from elsewhere: then one that lasts, so the link
    on your phone keeps working."""
    if not PUBLIC_URL:
        return secrets.token_urlsafe(24)
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            saved = f.read().strip()
        if len(saved) >= 32:
            return saved
    except FileNotFoundError:
        pass
    token = secrets.token_urlsafe(32)
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(token)
    return token


TOKEN = _token()
HOOK_TOKEN_FILE = os.path.join(os.path.dirname(HERE), "memory", "hook_token")  # this run's TOKEN, for claude_hooks.py
HOSTS = {"127.0.0.1", "localhost"} | ({urllib.parse.urlparse(PUBLIC_URL).hostname} if PUBLIC_URL else set())

_server: ThreadingHTTPServer | None = None
_respond = None  # main.respond, so the command box works like talking to Jarvis
_history: list[dict] = []
_busy = threading.Lock()


SETTINGS_FILE = os.path.join(os.path.dirname(HERE), "memory", "dashboard.json")
STATIC = {"/scene3d.js": "text/javascript; charset=utf-8", "/panels.js": "text/javascript; charset=utf-8"}  # other files the page loads (fixed list, nothing else)
THEMES = {"command": "Command Center", "village": "Village", "hud": "Stark HUD", "synthwave": "Synthwave",
          "galaxy": "Deep Space", "matrix": "Mainframe", "abyss": "The Abyss", "forge": "Dragon Forge", "aurora": "Aurora",
          "zen": "Zen Garden", "clockwork": "Clockwork Workshop", "mars": "Mars Colony"}
NICKNAMES = {"village": ("clash", "clash of clans", "coc", "town", "castle"),
             "hud": ("iron man", "stark", "jarvis", "hologram"),
             "command": ("default", "normal", "original", "classic", "war room", "tactical", "war table"),
             "synthwave": ("synth", "neon", "retro", "outrun", "80s", "eighties", "vaporwave", "retrowave"),
             "galaxy": ("space", "galaxy", "planets", "cosmos", "solar system", "stars", "universe"),
             "matrix": ("matrix", "hacker", "code", "terminal", "cyber", "digital rain"),
             "abyss": ("ocean", "underwater", "sea", "deep sea", "water", "submarine", "jellyfish"),
             "forge": ("volcano", "lava", "fire", "dragon", "fantasy", "medieval"),
             "aurora": ("snow", "ice", "winter", "arctic", "frost", "northern lights", "frozen"),
             "zen": ("japan", "japanese", "garden", "japanese garden", "sakura", "cherry blossom", "zen", "calm", "peaceful", "light"),
             "clockwork": ("steampunk", "brass", "gears", "victorian", "clock", "workshop"),
             "mars": ("mars", "red planet", "colony", "space base", "martian", "nasa", "rover")}


def theme() -> str:
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            value = json.load(f).get("theme", "command")
    except (FileNotFoundError, json.JSONDecodeError):
        value = "command"
    return value if value in THEMES else "command"


def set_theme(name: str) -> str:
    """Switch the dashboard's look (any open dashboard changes at once)."""
    filler = {"mode", "theme", "style", "look", "the", "dashboard", "skin", "view"}  # "village mode" -> "village"
    wanted = " ".join(w for w in name.lower().split() if w not in filler).strip() or name.lower().strip()
    key = next((k for k, label in THEMES.items() if wanted in (k, label.lower()) or wanted in label.lower()
                or wanted in NICKNAMES.get(k, ())), None)
    if key is None:
        return f"No dashboard theme like '{name}'. Themes: {', '.join(THEMES.values())}."
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump({"theme": key}, f)
    events.emit("theme", theme=key)
    return f"Dashboard theme set to {THEMES[key]}."


def url() -> str | None:
    return f"http://127.0.0.1:{PORT}/?token={TOKEN}" if _server else None


def public_url() -> str | None:
    """The link that works from your phone, if the dashboard is set up for that."""
    return f"{PUBLIC_URL}/?token={TOKEN}" if _server and PUBLIC_URL else None


def snapshot() -> dict:
    team = client.state() or {"agents": [], "approvals": [], "events": [], "max_agents": client.MAX_AGENTS}
    mine = [e for e in events.recent() if e["type"] != "theme"]
    return {"agents": team["agents"], "approvals": approvals.pending() + team["approvals"],
            "events": sorted(mine + team["events"], key=lambda e: e["time"])[-400:],
            "max_agents": team["max_agents"], "theme": theme(), "themes": THEMES}


def _command(text: str) -> None:
    """Run a typed command through Jarvis, as if spoken, reporting the reply as an event."""
    global _history
    events.emit("jarvis", direction="in", text=text)
    with _busy:
        try:
            _history, reply = _respond(_history, text, lambda q: approvals.ask(q, "Jarvis"),
                                       lambda said: events.emit("jarvis", direction="out", text=said),
                                       history_file=HISTORY_FILE)
        except Exception as e:
            reply = f"Something went wrong: {e}"
    if reply:
        events.emit("jarvis", direction="out", text=reply)
    elif reply is None:
        events.emit("jarvis", direction="out", text="Stopped.")


# ---------- signing in with a password (when JARVIS_DASHBOARD_PASSWORD is set) ----------

_failures: list[tuple[float, str]] = []  # (when, from which address)
_login_lock = threading.Lock()


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


PASSWORD_ID = _hash("jarvis-dashboard:" + PASSWORD)[:16]  # sign-ins made with an earlier password stop working


def _sessions() -> dict:
    """{sha256 of a session key: {"expires", "password"}}. Only hashes are stored, so the file can't sign anyone in."""
    try:
        with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
            found = json.load(f)
        return {k: v for k, v in found.items() if isinstance(v, dict) and v.get("expires", 0) > time.time()}
    except (FileNotFoundError, json.JSONDecodeError, AttributeError, OSError):
        return {}


def _save_sessions(sessions: dict) -> None:
    tmp = SESSIONS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sessions, f)
    os.replace(tmp, SESSIONS_FILE)  # never half-written while someone is being checked


def _new_session() -> str:
    key = secrets.token_urlsafe(32)
    with _login_lock:
        sessions = _sessions()
        sessions[_hash(key)] = {"expires": time.time() + SESSION_DAYS * 86400, "password": PASSWORD_ID}
        _save_sessions(sessions)
    return key


def _end_session(key: str) -> None:
    with _login_lock:
        sessions = _sessions()
        if sessions.pop(_hash(key), None) is not None:
            _save_sessions(sessions)


def _check_password(given: str, address: str) -> str:
    """'ok', 'wrong' or 'paused' (too many wrong tries lately from that address, or from everywhere)."""
    with _login_lock:
        now = time.time()
        _failures[:] = [f for f in _failures if now - f[0] < FAILURE_WINDOW_S]
        if len(_failures) >= MAX_ALL_FAILURES or sum(1 for _, a in _failures if a == address) >= MAX_FAILURES:
            return "paused"
        if PASSWORD and secrets.compare_digest(given.encode(), PASSWORD.encode()):
            return "ok"
        _failures.append((now, address))
    time.sleep(1)  # slows guessing down
    return "wrong"


LOGIN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Jarvis sign in</title><style>
body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: radial-gradient(circle at 50% 40%, #12202e, #05080c);
       font: 15px/1.4 "Segoe UI", system-ui, sans-serif; color: #dce9f5; }
form { width: min(340px, calc(100% - 32px)); padding: 26px; border-radius: 14px; background: #0c141ccc; border: 1px solid #23415a;
       box-shadow: 0 0 40px #4fe3ff22; }
h1 { margin: 0 0 4px; font-size: 20px; letter-spacing: .25em; color: #4fe3ff; } p { margin: 0 0 18px; color: #7d97ad; font-size: 13px; }
input { width: 100%; box-sizing: border-box; padding: 11px 13px; border-radius: 8px; border: 1px solid #23415a; background: #060b10; color: #fff; font: inherit; }
button { width: 100%; margin-top: 12px; padding: 11px; border: 0; border-radius: 8px; background: #4fe3ff; color: #021018; font: 700 15px inherit; cursor: pointer; }
.err { color: #ff6b7d; font-size: 13px; margin-top: 10px; min-height: 18px; }
</style></head><body><form method="post" action="/login"><h1>J.A.R.V.I.S.</h1><p>Sign in to your dashboard.</p>
<input type="password" name="password" placeholder="Dashboard password" autofocus autocomplete="current-password">
<button>Sign in</button><div class="err">{error}</div></form></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _host_ok(self) -> bool:
        return self.headers.get("Host", "").split(":")[0].lower() in HOSTS

    def _session_key(self) -> str:
        cookie = http.cookies.SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except http.cookies.CookieError:
            return ""
        key = cookie.get("jarvis_session")
        return key.value if key else ""

    def _signed_in(self) -> bool:
        key = self._session_key() if PASSWORD else ""
        return bool(key) and (_sessions().get(_hash(key)) or {}).get("password") == PASSWORD_ID

    def _address(self) -> str:
        """Who's asking: behind Tailscale Funnel every request comes from this PC, with the visitor's address added."""
        if self.client_address[0] in ("127.0.0.1", "::1"):
            forwarded = self.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            return forwarded or "this PC"
        return self.client_address[0]

    def _authorized(self) -> bool:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        given = self.headers.get("X-Jarvis-Token") or query.get("token", [""])[0]
        if not self._host_ok():
            return False
        return secrets.compare_digest(given.encode(), TOKEN.encode()) or self._signed_in()

    def _login(self) -> None:
        try:
            length = max(0, min(int(self.headers.get("Content-Length", 0) or 0), 4000))
        except ValueError:
            length = 0
        form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        result = _check_password(form.get("password", [""])[0], self._address())
        if result != "ok":
            error = ("Too many wrong tries. Wait 15 minutes, or open the link from /dashboard in Telegram."
                     if result == "paused" else "That's not the password.")
            if result == "wrong":
                events.emit("jarvis", direction="out", text="Someone typed a wrong dashboard password.")
            self._send(401, LOGIN_PAGE.replace("{error}", error).encode(), "text/html; charset=utf-8")
            return
        secure = "; Secure" if PUBLIC_URL.startswith("https://") else ""
        self.send_response(303)
        self.send_header("Set-Cookie", f"jarvis_session={_new_session()}; Path=/; HttpOnly; SameSite=Strict; "
                                       f"Max-Age={SESSION_DAYS * 86400}{secure}")
        self.send_header("Location", "/")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send(self, code: int, body: bytes, kind: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data).encode(), "application/json")

    def do_GET(self):
        if not self._authorized():
            if PASSWORD and self._host_ok() and urllib.parse.urlparse(self.path).path in ("/", "/login"):
                self._send(200, LOGIN_PAGE.replace("{error}", "").encode(), "text/html; charset=utf-8")
            else:
                self._send(403, b"Open the dashboard from Jarvis (say 'open the dashboard').", "text/plain")
            return
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif path in STATIC:
            with open(os.path.join(HERE, path.lstrip("/")), "rb") as f:
                self._send(200, f.read(), STATIC[path])
        elif path == "/state":
            self._json(snapshot())
        elif path == "/events":
            self._stream()
        elif path == "/insights":
            tab = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("tab", [""])[0]
            try:
                team = (client.state() or {}) if tab in insights.SECTIONS else {}
                self._json(insights.get(tab, team))
            except KeyError:
                self._json({"error": "unknown tab"}, 404)
            except Exception as e:  # one tab failing never takes the dashboard down
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)
        else:
            self._send(404, b"", "text/plain")

    def _stream(self) -> None:
        """Server-sent events: the page gets each new event the moment it happens."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        listener = events.subscribe()
        try:
            while True:
                try:
                    event = listener.get(timeout=15)
                    payload = f"data: {json.dumps(event)}\n\n"
                except Exception:
                    payload = ": keep-alive\n\n"
                self.wfile.write(payload.encode())
                self.wfile.flush()
        except (ConnectionError, OSError):
            pass
        finally:
            events.unsubscribe(listener)

    def _upload(self) -> None:
        """A file attached in the command box (or an agent's message box): saved where files from the phone go.
        The body is the raw file; its name comes in the X-File-Name header (URL-encoded). Only as
        application/octet-stream, which other websites can't send here without the browser asking first."""
        from telegram_bot import incoming_path

        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/octet-stream":
            self._json({"error": "send the file as application/octet-stream"}, 415)
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._json({"error": "missing Content-Length"}, 411)
            return
        if length < 0 or length > MAX_UPLOAD_BYTES:
            self.close_connection = True  # don't read it
            self._json({"error": f"files can be up to {MAX_UPLOAD_BYTES // 1_000_000} MB"}, 413)
            return
        name = urllib.parse.unquote(self.headers.get("X-File-Name", "")) or "file"
        path = incoming_path(name)
        left = length
        try:
            with open(path, "wb") as f:
                while left > 0:
                    chunk = self.rfile.read(min(left, 1 << 20))
                    if not chunk:
                        raise ConnectionError("the upload stopped part-way")
                    f.write(chunk)
                    left -= len(chunk)
        except (OSError, ConnectionError) as e:
            try:
                os.remove(path)
            except OSError:
                pass
            self._json({"error": f"couldn't save it: {e}"}, 500)
            return
        events.emit("jarvis", direction="in", text=f"📎 {os.path.basename(path)} ({length / 1_000_000:.1f} MB)")
        self._json({"path": path, "name": os.path.basename(path), "size": length})

    def do_POST(self):
        if PASSWORD and self._host_ok() and urllib.parse.urlparse(self.path).path == "/login":
            self._login()
            return
        if PASSWORD and self._host_ok() and urllib.parse.urlparse(self.path).path == "/logout":
            if self._session_key():
                _end_session(self._session_key())
            self.send_response(303)
            self.send_header("Set-Cookie", "jarvis_session=; Path=/; Max-Age=0")
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self._authorized() and urllib.parse.urlparse(self.path).path == "/upload":
            self._upload()
            return
        if not self._authorized() or (not self.headers.get("Content-Type", "").startswith("application/json")):
            self._send(403, b"", "text/plain")
            return
        try:
            length = max(0, min(int(self.headers.get("Content-Length", 0) or 0), 100_000))
        except ValueError:
            length = 0
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._json({"error": "bad request"}, 400)
            return
        path = urllib.parse.urlparse(self.path).path
        if path == "/command" and body.get("text", "").strip():
            if _respond is None:
                self._json({"error": "commands aren't available in this mode"}, 400)
                return
            threading.Thread(target=_command, args=(body["text"].strip(),), daemon=True).start()
            self._json({"ok": True})
        elif path == "/message":
            self._json({"result": client.message_agent(body.get("agent", ""), body.get("text", ""), "dashboard")})
        elif path == "/stop":
            self._json({"result": client.stop_agent(body.get("agent", ""))})
        elif path == "/theme":
            self._json({"result": set_theme(body.get("theme", ""))})
        elif path == "/announce" and body.get("text", "").strip():  # claude_hooks.py: the user's Claude Code needs them
            text = body["text"].strip()[:300]
            events.emit("jarvis", direction="out", text=f"🔔 {text}")
            if speaker is not None:
                threading.Thread(target=speaker, args=(text,), daemon=True).start()
            self._json({"ok": True})
        elif path == "/approve":
            self._json({"ok": client.answer_approval(body.get("approval", ""), bool(body.get("yes")))})
        else:
            self._json({"error": "unknown"}, 404)

    def log_message(self, *args) -> None:
        pass


speaker = None  # voice mode's speak(), so notices from the user's own Claude Code sessions are said aloud


def start(respond=None) -> str | None:
    """Start the dashboard (once). `respond` is main.respond, for the command box."""
    global _server, _respond, _history
    if _server is not None:
        return url()
    _respond = respond
    try:
        from memory.conversation import load_history

        _history = load_history(HISTORY_FILE)
        _server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as e:
        print(f"[dashboard] couldn't start on port {PORT}: {e}")
        return None
    _server.daemon_threads = True
    threading.Thread(target=_server.serve_forever, daemon=True, name="dashboard").start()
    try:
        with open(HOOK_TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(TOKEN)
    except OSError:
        pass
    print(f"[dashboard] {url()}")
    return url()
