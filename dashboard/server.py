"""The live agent dashboard: a page on this PC (http://127.0.0.1:8765) showing Jarvis and its agents.

The agents run in the team service (team_service.py); the page shows them through team/client.py, merged with
Jarvis's own events (the command box, theme changes).

Every request must carry this run's secret token (it's in the link Jarvis opens), so other websites open in
the browser can't send Jarvis commands through it. Only this PC can connect.
"""
import json
import os
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from team import approvals, client, events

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("JARVIS_DASHBOARD_PORT", "8765"))
TOKEN = secrets.token_urlsafe(24)
HISTORY_FILE = os.path.join(os.path.dirname(HERE), "memory", "dashboard_history.json")

_server: ThreadingHTTPServer | None = None
_respond = None  # main.respond, so the command box works like talking to Jarvis
_history: list[dict] = []
_busy = threading.Lock()


SETTINGS_FILE = os.path.join(os.path.dirname(HERE), "memory", "dashboard.json")
STATIC = {"/scene3d.js": "text/javascript; charset=utf-8"}  # other files the page loads (fixed list, nothing else)
THEMES = {"command": "Command Center", "village": "Village", "hud": "Stark HUD", "synthwave": "Synthwave",
          "galaxy": "Deep Space", "matrix": "Mainframe", "abyss": "The Abyss", "forge": "Dragon Forge", "aurora": "Aurora"}
NICKNAMES = {"village": ("clash", "clash of clans", "coc", "town", "castle"),
             "hud": ("iron man", "stark", "jarvis", "hologram"),
             "command": ("default", "normal", "original", "classic", "war room", "tactical", "war table"),
             "synthwave": ("synth", "neon", "retro", "outrun", "80s", "eighties", "vaporwave", "retrowave"),
             "galaxy": ("space", "galaxy", "planets", "cosmos", "solar system", "stars", "universe"),
             "matrix": ("matrix", "hacker", "code", "terminal", "cyber", "digital rain"),
             "abyss": ("ocean", "underwater", "sea", "deep sea", "water", "submarine", "jellyfish"),
             "forge": ("volcano", "lava", "fire", "dragon", "fantasy", "medieval"),
             "aurora": ("snow", "ice", "winter", "arctic", "frost", "northern lights", "frozen")}


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


class Handler(BaseHTTPRequestHandler):
    def _authorized(self) -> bool:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        given = self.headers.get("X-Jarvis-Token") or query.get("token", [""])[0]
        host_ok = self.headers.get("Host", "").split(":")[0] in ("127.0.0.1", "localhost")
        return host_ok and secrets.compare_digest(given, TOKEN)

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

    def do_POST(self):
        if not self._authorized():
            self._send(403, b"", "text/plain")
            return
        length = min(int(self.headers.get("Content-Length", 0)), 100_000)
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
        elif path == "/approve":
            self._json({"ok": client.answer_approval(body.get("approval", ""), bool(body.get("yes")))})
        else:
            self._json({"error": "unknown"}, 404)

    def log_message(self, *args) -> None:
        pass


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
    print(f"[dashboard] {url()}")
    return url()
