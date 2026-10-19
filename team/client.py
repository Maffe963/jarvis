"""Jarvis's side of the agent team. The team runs in its own process (team_service.py) so it keeps going while
Jarvis is off; Jarvis starts it if it isn't running, leads it through these tools, and follows everything it does:
the service's events go to Jarvis's dashboard, announcements are said aloud, and when Jarvis comes back it says
what the team sent while it was away.
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from team import approvals, events, messages

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVICE_FILE = os.path.join(messages.FOLDER, "service.json")  # where the running service listens, and its key
MAX_AGENTS = 5


def code_version() -> str:
    """The git commit this copy of Jarvis is at, so a service left running from older code can be replaced."""
    try:
        with open(os.path.join(ROOT, ".git", "HEAD"), "r", encoding="utf-8") as f:
            head = f.read().strip()
        if head.startswith("ref: "):
            with open(os.path.join(ROOT, ".git", *head[5:].split("/")), "r", encoding="utf-8") as f:
                return f.read().strip()
        return head
    except OSError:
        return ""


def _service() -> dict | None:
    try:
        with open(SERVICE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _request(path: str, body: dict | None = None, timeout: float = 10):
    service = _service()
    if not service:
        raise ConnectionError("the team service isn't running")
    req = urllib.request.Request(
        f"http://127.0.0.1:{service['port']}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Team-Token": service["token"], "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def ping() -> dict | None:
    try:
        return _request("/ping", timeout=3)
    except (OSError, ValueError, ConnectionError):
        return None


def _launch() -> None:
    script = os.path.join(ROOT, "team_service.py")
    if os.name == "nt":
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        exe = pythonw if os.path.exists(pythonw) else sys.executable
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen([exe, script], cwd=ROOT, creationflags=flags, close_fds=True)
    else:
        subprocess.Popen([sys.executable, script], cwd=ROOT, start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


_start_lock = threading.Lock()


def ensure_running(wait_s: float = 20) -> bool:
    """Start the team service if it isn't running (or is running older code). It stays up after Jarvis exits."""
    with _start_lock:
        running, version = ping(), code_version()
        if running and running.get("version") == version:
            return True
        if running and not _stop(running):  # Jarvis was updated: restart on the new code (agents carry on)
            print("[team] the old team service didn't stop")
            return False
        _launch()
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            if (ping() or {}).get("version") == version:
                return True
            time.sleep(0.3)
        print("[team] the team service didn't start; see memory/team/service.log")
        return False


def _stop(running: dict) -> bool:
    """Ask the running service to stop; True once it has (it closes each agent's Claude Code first)."""
    try:
        _request("/shutdown", {})
    except (OSError, ValueError, ConnectionError):
        pass
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if (ping() or {}).get("pid") != running.get("pid"):
            return True
        time.sleep(0.25)
    return False


def restart_service() -> bool:
    """Stop the team service and start it again (agents carry on). True once a new one is running."""
    with _start_lock:
        running = ping()
        if running and not _stop(running):
            return False
    return ensure_running()


def call(function: str, **inputs):
    if not ensure_running():
        return "The agent team couldn't be started (see memory/team/service.log)."
    try:
        return _request("/call", {"name": function, "input": inputs}, timeout=30)["result"]
    except (OSError, ValueError, ConnectionError, KeyError) as e:
        return f"Couldn't reach the agent team: {e}"


def _standing_names() -> str:
    from team import standing

    return ", ".join(f"{name} ({spec.get('role', '')})" for name, spec in standing.definitions().items())


def _code_repos() -> list[str]:
    from team import coding

    return coding.repo_names()


def _tag(event: dict) -> dict:
    """The service numbers its events separately from Jarvis's own, so keep the dashboard from mixing them up."""
    return {**event, "id": f"t{event['id']}"} if "id" in event else event


def state() -> dict | None:
    """The team as the dashboard shows it, or None if the service isn't reachable."""
    try:
        data = _request("/state", timeout=5)
    except (OSError, ValueError, ConnectionError):
        return None
    data["events"] = [_tag(e) for e in data.get("events", [])]
    return data


# ---------- following the team ----------

def _names(names: list[str]) -> str:
    names = list(dict.fromkeys(names))
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _missed_summary(missed: list[dict]) -> str:
    finished = [m["from"] for m in missed if m["kind"] == "result"]
    questions = [m["from"] for m in missed if m["kind"] == "question"]
    others = [m["from"] for m in missed if m["kind"] not in ("result", "question")]
    parts = ["Sir, while I was away"]
    if finished:
        parts.append(f"{_names(finished)} finished;")
    if questions:
        parts.append(f"{_names(questions)} asked you {'a question' if len(questions) == 1 else 'questions'};")
    if others:
        parts.append(f"{_names(others)} sent {len(others)} message{'s' if len(others) != 1 else ''};")
    parts[-1] = parts[-1].rstrip(";") + "."
    return " ".join(parts) + " Ask me for the team's messages to hear them."


def follow(say) -> None:
    """In the background: start the team service if needed and pass on what it does. `say(text)` tells the user
    (aloud in voice mode); the service has already sent anything important to the phone itself."""
    def run():
        backoff = 2
        while True:
            if not ensure_running():
                time.sleep(30)
                continue
            try:
                service = _service()
                req = urllib.request.Request(f"http://127.0.0.1:{service['port']}/events?jarvis=1",
                                             headers={"X-Team-Token": service["token"]})
                with urllib.request.urlopen(req, timeout=60) as resp:
                    backoff = 2
                    for line in resp:
                        if not line.startswith(b"data: "):
                            continue
                        event = json.loads(line[6:])
                        if event["type"] == "missed":
                            if event["messages"]:
                                say(_missed_summary(event["messages"]))
                        elif event["type"] == "announce":
                            say(event["text"])
                        else:
                            events.publish(_tag(event))
            except Exception as e:
                print(f"[team] lost touch with the team service ({e}); reconnecting")
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)

    threading.Thread(target=run, daemon=True, name="team-follow").start()


# ---------- Jarvis's tools ----------

def create_agent(name: str, role: str, task: str, kind: str = "agent", repo: str = "") -> str:
    return call("create_agent", name=name, role=role, task=task, kind=kind, repo=repo)


def message_agent(agent: str, text: str, via: str = "jarvis") -> str:
    return call("message_agent", agent=agent, text=text, via=via)


def stop_agent(agent: str) -> str:
    return call("stop_agent", agent=agent)


def team_status() -> str:
    return call("team_status")


def agent_result(agent: str) -> str:
    return call("agent_result", agent=agent)


def team_messages(agent: str = "", limit: int = 30, updates: bool = False) -> str:
    return call("team_messages", agent=agent, limit=limit, updates=updates)


def answer_approval(approval: str, yes: bool) -> bool:
    """For the dashboard: an approval Jarvis is waiting on here, or one the team service is waiting on."""
    return approvals.decide(approval, yes) or bool(call("answer_approval", approval=approval, yes=yes) is True)


def set_dashboard_theme(theme: str) -> str:
    from dashboard import server

    return server.set_theme(theme)


def send_dashboard_link() -> str:
    from dashboard import server
    from tools import phone

    link = server.public_url()
    if not link:
        return ("The dashboard can only be opened on the PC right now. To open it from the phone, set up Tailscale "
                "and JARVIS_DASHBOARD_URL (see the README).")
    bot = phone._bot()
    if bot is None or bot.chat_id is None:
        return "Phone control isn't on, so I can't send the link to the phone."
    bot.send(f"🖥 Your agent dashboard: {link}\n\nKeep this link private: it controls Jarvis.")
    return "Sent the dashboard link to your phone."


def open_dashboard() -> str:
    from dashboard import server
    from tools import system

    url = server.url()
    if not url:
        return "The dashboard isn't running."
    system.open_url(url)
    return "Opened the agent dashboard."


text = {"type": "string"}
TOOLS = [
    {
        "name": "create_agent",
        "description": f"Start an AI agent with a role to work on part of a job (up to {MAX_AGENTS} at once). For "
                       "bigger jobs, create several with complementary roles (e.g. Researcher, Writer, Reviewer) and "
                       "say in each task who to hand off to; they can message each other. Agents can research, "
                       "read/write files, check calendar/email/jobs, but not use the screen or message people. "
                       "They keep working even after Jarvis is turned off, and each gets its own Telegram topic "
                       "the user can chat in. Work that writes files or runs commands is automatically checked by "
                       "a Reviewer agent (for safety, correctness and efficiency) before it's handed in, so don't "
                       "create a reviewer yourself. For programming, use kind 'coder' with a repo: a coder works "
                       "on its own branch, runs the tests, and opens a pull request that the Reviewer reviews and "
                       "merges. Split a big coding job into separate parts (e.g. backend, frontend, tests) with one "
                       "coder each; one coder is enough for a small change. Code repositories: "
                       f"{', '.join(_code_repos()) or 'none set up yet (code_repos.json)'}. "
                       "Give each a short name and complete standalone instructions. The user also has standing "
                       f"specialists who stay on the team for good: {_standing_names() or 'none yet'}. For their "
                       "subjects, pass the request to them with message_agent instead of creating a new agent.",
        "input_schema": {"type": "object", "properties": {
            "name": {"type": "string", "description": "Short name, e.g. 'Researcher' or 'Backend coder'"},
            "role": {"type": "string", "description": "One line: what this agent is responsible for"},
            "task": {"type": "string", "description": "Full instructions, including who to hand results to"},
            "kind": {"type": "string", "enum": ["agent", "coder"], "description": "'coder' for programming"},
            "repo": {"type": "string", "description": "For coders: which code repository"}},
            "required": ["name", "role", "task"]}
    },
    {
        "name": "message_agent",
        "description": "Pass the user's instructions or answers to an agent (by name or id).",
        "input_schema": {"type": "object", "properties": {"agent": text, "text": text}, "required": ["agent", "text"]}
    },
    {
        "name": "stop_agent",
        "description": "Stop an agent (by name or id), or 'all'.",
        "input_schema": {"type": "object", "properties": {"agent": text}, "required": ["agent"]}
    },
    {
        "name": "team_status",
        "description": "What each agent is doing, and anything waiting for the user's approval.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "agent_result",
        "description": "An agent's final result.",
        "input_schema": {"type": "object", "properties": {"agent": text}, "required": ["agent"]}
    },
    {
        "name": "team_messages",
        "description": "Read the messages agents sent and received: to and from each other, Jarvis and the user "
                       "(including on Telegram), also from while Jarvis was off. Optionally only one agent's.",
        "input_schema": {"type": "object", "properties": {
            "agent": {"type": "string", "description": "Only messages to or from this agent (name or id)"},
            "limit": {"type": "integer", "description": "How many of the latest messages (default 30)"},
            "updates": {"type": "boolean", "description": "Also include each agent's reply after every step"}},
            "required": []}
    },
    {
        "name": "set_dashboard_theme",
        "description": "Change the agent dashboard's look: 'command' (Command Center, a tactical war table, default), "
                       "'village' (a cartoon village-builder island), 'hud' (Stark-style holographic HUD), "
                       "'synthwave' (80s neon drive), 'galaxy' (Deep Space: a star, planets and a station) or "
                       "'matrix' (Mainframe: digital rain and server monoliths), 'abyss' (The Abyss: glowing "
                       "jellyfish in the deep sea), 'forge' (Dragon Forge: lava and a dragon egg) or 'aurora' "
                       "(snowy night under the northern lights).",
        "input_schema": {"type": "object", "properties": {"theme": text}, "required": ["theme"]}
    },
    {
        "name": "open_dashboard",
        "description": "Open the live agent dashboard in the browser on the PC.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "send_dashboard_link",
        "description": "Send the user's phone the link to the agent dashboard, to open it away from the PC.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
]

TOOL_MAP = {"create_agent": create_agent, "message_agent": message_agent, "stop_agent": stop_agent,
            "team_status": team_status, "agent_result": agent_result, "team_messages": team_messages,
            "open_dashboard": open_dashboard, "set_dashboard_theme": set_dashboard_theme,
            "send_dashboard_link": send_dashboard_link}
