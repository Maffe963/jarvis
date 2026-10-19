"""The agent team's own process: runs Jarvis's agents whether or not Jarvis is on.

    .venv/Scripts/pythonw.exe team_service.py     # Jarvis starts it by itself when needed

It runs the agents (team/agents.py), keeps them in memory/team/ so they carry on after a restart, runs the team's
Telegram bot (each agent gets its own topic in your team group, see team/telegram_team.py), and serves a small API
on this PC that Jarvis uses to lead the team, follow along and read every message. Only one runs at a time; it
keeps running when Jarvis turns off. Output goes to memory/team/service.log.
"""
import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

from team import agents, approvals, client, events, messages  # noqa: E402
from tools import system  # noqa: E402

LOG_FILE = os.path.join(messages.FOLDER, "service.log")
MAX_LOG_BYTES = 1_000_000
TOKEN = secrets.token_urlsafe(24)
VERSION = client.code_version()

# What Jarvis can ask the service to do: name -> (function, the inputs it takes)
CALLS = {
    "create_agent": (lambda name, role, task, kind="agent", repo="": agents.create_agent(
        name, role, task, kind if kind in agents.KINDS else "agent", repo), ("name", "role", "task", "kind", "repo")),
    "message_agent": (agents.message_agent, ("agent", "text", "via")),
    "stop_agent": (agents.stop_agent, ("agent",)),
    "team_status": (agents.team_status, ()),
    "agent_result": (agents.agent_result, ("agent",)),
    "team_messages": (agents.team_messages, ("agent", "limit", "updates")),
    "answer_approval": (agents.answer_approval, ("approval", "yes")),
}


def state() -> dict:
    return {"agents": [a.info() for a in agents.everyone()], "approvals": approvals.pending(),
            "events": events.recent()[-400:], "max_agents": agents.MAX_AGENTS}


class Handler(BaseHTTPRequestHandler):
    def _authorized(self) -> bool:
        host_ok = self.headers.get("Host", "").split(":")[0] in ("127.0.0.1", "localhost")
        return host_ok and secrets.compare_digest(self.headers.get("X-Team-Token", ""), TOKEN)

    def _json(self, data, code: int = 200) -> None:
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/ping":  # no token needed: just says it's here and which code it runs
            self._json({"ok": True, "version": VERSION, "pid": os.getpid()})
        elif not self._authorized():
            self._json({"error": "forbidden"}, 403)
        elif url.path == "/state":
            self._json(state())
        elif url.path == "/events":
            self._stream(urllib.parse.parse_qs(url.query).get("jarvis") == ["1"])
        else:
            self._json({"error": "unknown"}, 404)

    def do_POST(self):
        if not self._authorized():
            self._json({"error": "forbidden"}, 403)
            return
        try:
            body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 200_000)) or b"{}")
        except ValueError:
            self._json({"error": "bad request"}, 400)
            return
        path = urllib.parse.urlparse(self.path).path
        if path == "/call" and body.get("name") in CALLS:
            function, takes = CALLS[body["name"]]
            inputs = body.get("input") or {}
            try:
                result = function(**{k: inputs[k] for k in takes if k in inputs})
            except Exception as e:
                result = f"Team service error: {e}"
            self._json({"result": result})
        elif path == "/shutdown":
            self._json({"ok": True})
            threading.Thread(target=_shutdown, daemon=True).start()
        else:
            self._json({"error": "unknown"}, 404)

    def _stream(self, jarvis: bool) -> None:
        """Server-sent events. Jarvis's own connection first gets what it missed while it was away, and every
        message to it after that counts as seen."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        listener = events.subscribe()
        try:
            if jarvis:
                missed = messages.for_jarvis(messages.seen())
                self._write({"type": "missed", "messages": missed})
                if missed:
                    messages.mark_seen(missed[-1]["id"])
            while True:
                try:
                    event = listener.get(timeout=15)
                except Exception:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    continue
                self._write(event)
                if jarvis and event.get("msg") and event["type"] in ("message", "result"):
                    messages.mark_seen(event["msg"])
        except (ConnectionError, OSError):
            pass
        finally:
            events.unsubscribe(listener)

    def _write(self, event: dict) -> None:
        self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
        self.wfile.flush()

    def log_message(self, *args) -> None:
        pass


def _shutdown() -> None:
    """Stop (e.g. after Jarvis updated itself, so the new code takes over). Agents carry on after the restart."""
    print("[team] shutting down")
    agents.shutdown()
    time.sleep(0.5)
    _forget()
    os._exit(0)


def _forget() -> None:
    try:
        with open(client.SERVICE_FILE, "r", encoding="utf-8") as f:
            if json.load(f).get("pid") == os.getpid():
                os.remove(client.SERVICE_FILE)
    except (OSError, ValueError):
        pass


def _redirect_output() -> None:
    os.makedirs(messages.FOLDER, exist_ok=True)
    if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > MAX_LOG_BYTES:
        os.replace(LOG_FILE, LOG_FILE + ".old")
    log = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = log


_lock_file = None


def _claim_single_instance() -> bool:
    """False if another team service is running (two would run every agent twice). The lock goes with the process."""
    global _lock_file
    os.makedirs(messages.FOLDER, exist_ok=True)
    _lock_file = open(os.path.join(messages.FOLDER, "service.lock"), "a+")
    try:
        if os.name == "nt":
            import msvcrt

            _lock_file.seek(0)
            msvcrt.locking(_lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(_lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def main() -> None:
    if "--console" not in sys.argv:
        _redirect_output()
    if not _claim_single_instance():
        print("[team] already running")
        return
    print(f"[team] starting {time.strftime('%Y-%m-%d %H:%M:%S')}")
    os.makedirs(messages.FOLDER, exist_ok=True)
    messages.trim()
    approvals.phone_prompts = False  # the team bot asks in the agent's own topic instead
    # There's no voice here: announcements go to Jarvis (which says them, if it's on) and the dashboard.
    system.set_announcer(lambda msg: (print(f"[team] {msg}"), events.emit("announce", text=msg)))

    server = ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("JARVIS_TEAM_PORT", "0") or 0)), Handler)
    server.daemon_threads = True
    with open(client.SERVICE_FILE, "w", encoding="utf-8") as f:
        json.dump({"port": server.server_address[1], "token": TOKEN, "pid": os.getpid(), "version": VERSION}, f)

    from team import telegram_team

    telegram_team.start()  # before restoring, so it sees the resumed agents' messages
    print(f"[team] resumed {agents.restore()} agent(s); listening on port {server.server_address[1]}")
    print(f"[team] standing agents: {agents.sync_standing()}")
    from team import standing

    def run_job(name: str, text: str) -> None:
        agent = next((a for a in agents.active() if a.kind == "standing" and a.name == name), None)
        if agent:
            agent.put(text)

    standing.scheduler(run_job)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        agents.shutdown()
        _forget()


if __name__ == "__main__":
    main()
