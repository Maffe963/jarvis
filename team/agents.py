"""A team of AI agents that Jarvis leads. They run in the team service (team_service.py), a process of their own,
so they keep working, and talking to each other, while Jarvis is off.

Each agent is its own Claude conversation with a role ("Researcher: finds and checks facts"), the read-and-research
tools background tasks get, plus its own: message a teammate, ask the user something, and finish with a report.
Agents work until they finish or are stopped; messages that arrive while one is busy wait until its current step
is done. Agents, their conversations and waiting messages are saved in memory/team/agents.json, so they carry on
after the service restarts. Every message goes in the team's message log (team/messages.py) and everything they do
is reported as events (team/events.py) for the dashboard, the team's Telegram bot and Jarvis.
"""
import json
import os
import threading
import time
import uuid

from team import approvals, events, messages
from tools import system

MAX_AGENTS = 5
IDLE_TIMEOUT_S = 3 * 3600  # an agent waiting this long with nothing to do finishes on its own
STATE_FILE = os.path.join(messages.FOLDER, "agents.json")
KEEP_FINISHED = 30
FINISHED = ("done", "stopped", "failed")

_agents: dict[str, "Agent"] = {}
_lock = threading.Lock()
_save_lock = threading.Lock()
_shutting_down = threading.Event()

PROMPT = """You are {name}, an AI agent on a small team led by Jarvis, the user's personal assistant.
Your role: {role}

Teammates right now: {team}. You can talk to them with send_message (address them by name), and to Jarvis
(who passes things on to the user) with to="jarvis". Use ask_user only for decisions that really need the user.
Work on your own with your tools; you can't use the screen, mouse, keyboard or apps, and you can't email or
message people. Keep messages to teammates short and useful (findings, hand-offs, questions). When your part is
complete, call finish with your final result: complete and well organised, plain text (no ** or #). After you
finish you stop, so make sure the result stands on its own. If you're waiting on a teammate, just end your turn;
their message will come to you. If your part was to hand something to a teammate and you've done that (and
nobody needs more from you), call finish with a short note of what you handed over."""
RESUMED = ("(The team service restarted while you were working, so your last step may not have finished. "
           "Carry on from where you left off.)")
ALONE_GRACE_S = 60  # a waiting agent whose teammates have all finished wraps up after this long
USER_IDS = ("user", "jarvis", "the user")


def _short(text: str, n: int = 160) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


class Agent:
    def __init__(self, name: str, role: str, task: str, saved: dict | None = None) -> None:
        saved = saved or {}
        self.id = saved.get("id") or uuid.uuid4().hex[:6]
        self.name, self.role, self.task = name, role, task
        self.status = saved.get("status", "starting")
        self.inbox: list[str] = list(saved.get("inbox", []))
        self._wake = threading.Condition()
        self.cancelled = threading.Event()
        self.log = events.ActivityLog(self.id, saved.get("log"))
        self.result = saved.get("result", "")
        self.history: list[dict] = list(saved.get("history", []))
        self.started = saved.get("started", time.time())
        self.session = None

    def info(self) -> dict:
        return {"id": self.id, "name": self.name, "role": self.role, "task": self.task, "status": self.status,
                "steps": len(self.log), "result": self.result, "started": self.started}

    def saved(self) -> dict:
        return {**self.info(), "inbox": list(self.inbox), "history": list(self.history), "log": list(self.log)[-200:]}

    def set_status(self, status: str, note: str = "") -> None:
        self.status = status
        events.emit("status", agent=self.id, status=status, note=_short(note))
        save()

    def put(self, text: str) -> None:
        with self._wake:
            self.inbox.append(text)
            self._wake.notify()
        save()

    def _next(self, timeout: float) -> str | None:
        with self._wake:
            if not self.inbox:
                self._wake.wait(timeout)
            return self.inbox.pop(0) if self.inbox else None

    # ----- the agent's own tools -----

    def _tools(self) -> dict:
        def send_message(inputs: dict) -> str:
            return deliver(self.id, inputs.get("to", ""), inputs.get("text", ""))

        def ask_user(inputs: dict) -> str:
            question = inputs.get("question", "")
            self.set_status("needs you", question)
            message = messages.add(self.name, self.id, "user", "user", question, kind="question")
            events.emit("message", sender=self.id, to="user", text=question, msg=message["id"])
            system.announce(f"Sir, {self.name} has a question: {_short(question, 200)}")
            return ("Your question was sent to the user. End your turn now; their answer will arrive as a message. "
                    "Meanwhile don't repeat the question.")

        def finish(inputs: dict) -> str:
            self.result = inputs.get("report", "").strip() or "(no report)"
            self.cancelled.set()  # stops after this step
            return "Finished. Your report has been handed to Jarvis."

        def team_list(_: dict) -> str:
            return "\n".join(f"{a.name} ({a.role}) - {a.status}" for a in active() if a is not self) or "Just you."

        text = {"type": "string"}
        return {
            "send_message": ({"name": "send_message", "description": "Send a short message to a teammate (by name) "
                              "or to 'jarvis'. They receive it after their current step.",
                              "input_schema": {"type": "object", "properties": {"to": text, "text": text},
                                               "required": ["to", "text"]}}, send_message),
            "ask_user": ({"name": "ask_user", "description": "Ask the user a question (only for decisions that need "
                          "them). Their answer comes back as a message.",
                          "input_schema": {"type": "object", "properties": {"question": text},
                                           "required": ["question"]}}, ask_user),
            "finish": ({"name": "finish", "description": "Hand in your final result and stop.",
                        "input_schema": {"type": "object", "properties": {"report": text}, "required": ["report"]}},
                       finish),
            "team": ({"name": "team", "description": "Who else is on the team and what they're doing.",
                      "input_schema": {"type": "object", "properties": {}, "required": []}}, team_list),
        }

    # ----- running -----

    def _wait_for_message(self, last_reply: str) -> str | None:
        """The next message, or None if it's time to stop: idle for hours, or left waiting after everyone else on
        the team has finished (nobody is going to message it), unless it's waiting on the user."""
        waited, alone_since = 0.0, None
        while waited < IDLE_TIMEOUT_S and not self.cancelled.is_set():
            message = self._next(timeout=5)
            if message is not None:
                return message
            waited += 5
            others = [a for a in active() if a is not self]
            if others or self.status == "needs you":
                alone_since = None
            else:
                alone_since = alone_since if alone_since is not None else waited
                if waited - alone_since >= ALONE_GRACE_S:
                    break
        if not self.cancelled.is_set():  # stopped isn't finished
            self.result = self.result or last_reply or "(finished without a report)"
        return None

    def run(self, resumed: bool = False) -> None:
        import brain
        from tools import background

        team = ", ".join(f"{a.name} ({a.role})" for a in active() if a is not self) or "none yet"
        prompt = PROMPT.format(name=self.name, role=self.role, team=team)
        self.session = brain.ClaudeCodeSession(prompt, background.ALLOWED, self.cancelled, self.log, self._tools())
        if not resumed or not self.history:
            message = f"Your task from the user (via Jarvis):\n{self.task}"
        elif self.status in ("starting", "working"):
            message = RESUMED  # the history it carries on from is replayed to the new Claude Code process
        else:
            message = None  # it was waiting for a message; keep waiting
        last_reply = next((t["content"] for t in reversed(self.history) if t["role"] == "assistant"), "")
        try:
            while not self.cancelled.is_set():
                if message is None:
                    message = self._wait_for_message(last_reply)
                    if message is None or self.cancelled.is_set():
                        break
                self.history.append({"role": "user", "content": message})
                self.set_status("working", _short(message, 120))
                try:
                    reply = self.session.ask(self.history, lambda q: approvals.ask(q, self.name, self.id))
                except brain.TaskInterrupted:
                    break
                self.history.append({"role": "assistant", "content": reply})
                last_reply, message = reply, None
                if reply.strip():
                    logged = messages.add(self.name, self.id, "Jarvis", "jarvis", reply, kind="update")
                    events.emit("message", sender=self.id, to="jarvis", text=_short(reply, 400), kind="update",
                                msg=logged["id"])
                if self.cancelled.is_set():
                    break
                self.set_status("waiting" if self.status != "needs you" else "needs you", "waiting for a message")
        except Exception as e:
            if _shutting_down.is_set():
                return  # the service is restarting; this agent carries on afterwards
            self.set_status("failed", str(e))
            self.result = f"It failed: {e}"
        finally:
            self.session.close()
        if _shutting_down.is_set():
            return
        if self.status == "failed":
            _report(self)
        elif self.result:
            logged = messages.add(self.name, self.id, "Jarvis", "jarvis", self.result, kind="result")
            self.set_status("done", _short(self.result, 120))
            events.emit("result", agent=self.id, text=self.result, msg=logged["id"])
            _report(self)
        elif self.status not in FINISHED:
            self.set_status("stopped")


def _report(agent: Agent) -> None:
    failed = agent.status == "failed"
    headline = _short(agent.result.splitlines()[0], 160) if agent.result else ""
    system.announce(f"Sir, {agent.name} {'ran into a problem' if failed else 'has finished'}. {headline}")


def active() -> list[Agent]:
    with _lock:
        return [a for a in _agents.values() if a.status not in FINISHED]


def everyone() -> list[Agent]:
    with _lock:
        return list(_agents.values())


def find(name_or_id: str) -> Agent | None:
    wanted = name_or_id.lower().strip()
    agents = everyone()
    return next((a for a in agents if a.id == wanted), None) or \
        next((a for a in reversed(agents) if a.name.lower() == wanted), None) or \
        next((a for a in reversed(agents) if wanted and wanted in a.name.lower()), None)


def deliver(sender: str, to: str, text: str, via: str = "") -> str:
    """A message from an agent (by id or name), the user or Jarvis to an agent (or to Jarvis/the user).
    `via`: where the user wrote it (jarvis, dashboard, telegram)."""
    sender_agent = None if sender.lower() in USER_IDS else find(sender)
    sender_id = sender_agent.id if sender_agent else sender.lower()
    sender_name = sender_agent.name if sender_agent else ("User" if sender_id == "user" else "Jarvis")
    if to.lower().strip() in USER_IDS:
        logged = messages.add(sender_name, sender_id, "Jarvis", "jarvis", text, via=via)
        events.emit("message", sender=sender_id, to="jarvis", text=text, msg=logged["id"], via=via)
        if sender_agent:
            system.announce(f"Sir, {sender_agent.name} says: {_short(text, 200)}")
        return "Sent to Jarvis."
    target = find(to)
    if target is None or target.status in FINISHED:
        names = ", ".join(a.name for a in active()) or "none"
        return f"No active teammate called '{to}'. Active: {names}."
    logged = messages.add(sender_name, sender_id, target.name, target.id, text, via=via)
    events.emit("message", sender=sender_id, to=target.id, text=text, msg=logged["id"], via=via)
    where = {"telegram": " on Telegram", "dashboard": " on the dashboard"}.get(via, " via Jarvis")
    who = sender_agent.name if sender_agent else f"the user{where}"
    target.put(f"Message from {who}: {text}")
    if target.status == "needs you" and not sender_agent:
        target.set_status("working", "got an answer")
    return f"Sent to {target.name}."


# ---------- saving and restoring ----------

def save() -> None:
    if _shutting_down.is_set():
        return
    agents = everyone()
    finished = [a for a in agents if a.status in FINISHED][-KEEP_FINISHED:]
    kept = [a.saved() for a in agents if a.status not in FINISHED or a in finished]
    os.makedirs(messages.FOLDER, exist_ok=True)
    with _save_lock:
        temp = STATE_FILE + ".tmp"
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(kept, f)
        os.replace(temp, STATE_FILE)


def restore() -> int:
    """Load the saved team and set its unfinished agents working again. Returns how many resumed."""
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return 0
    resumed = []
    with _lock:
        for item in saved:
            agent = Agent(item["name"], item["role"], item["task"], item)
            _agents[agent.id] = agent
            if agent.status not in FINISHED:
                resumed.append(agent)
    for agent in resumed:
        threading.Thread(target=agent.run, args=(True,), daemon=True, name=f"agent-{agent.name}").start()
    return len(resumed)


def shutdown() -> None:
    """Stop for a restart: save everything as it is, so unfinished agents carry on when the service starts again."""
    save()
    _shutting_down.set()
    for agent in active():
        if agent.session is not None:
            agent.session.close()


# ---------- what Jarvis (and the Telegram bot) can do, through the team service ----------

def create_agent(name: str, role: str, task: str) -> str:
    if len(active()) >= MAX_AGENTS:
        return f"There are already {MAX_AGENTS} agents working; stop one first."
    if find(name) and find(name).status not in FINISHED:
        return f"There's already an active agent called {name}; pick another name."
    agent = Agent(name.strip() or "Agent", role.strip(), task.strip())
    with _lock:
        _agents[agent.id] = agent
    events.emit("agent", agent=agent.id, name=agent.name, role=agent.role, task=agent.task)
    save()
    threading.Thread(target=agent.run, daemon=True, name=f"agent-{agent.name}").start()
    return (f"Created {agent.name} [{agent.id}] ({agent.role}). It's working on it; the dashboard and its "
            "Telegram topic show its progress.")


def message_agent(agent: str, text: str, via: str = "jarvis") -> str:
    return deliver("user", agent, text, via)


def stop_agent(agent: str) -> str:
    targets = active() if agent.lower().strip() in ("all", "everyone", "everybody") else [find(agent)]
    targets = [a for a in targets if a is not None and a.status not in FINISHED]
    for a in targets:
        a.cancelled.set()  # its own thread reports it stopped, once it is
        a.put("(stopped)")
    return f"Stopped {', '.join(a.name for a in targets)}." if targets else f"No active agent like '{agent}'."


def team_status() -> str:
    agents = everyone()
    if not agents:
        return "No agents yet."
    lines = []
    for a in agents:
        last = a.log[-1].split(": ", 1)[-1][:80] if a.log else "no actions yet"
        lines.append(f"[{a.id}] {a.name} ({a.role}): {a.status}, {len(a.log)} steps; last: {last}")
    waiting = approvals.pending()
    if waiting:
        lines.append("Waiting for your approval: " + "; ".join(f"{w['who']}: {w['question']}" for w in waiting))
    return "\n".join(lines)


def agent_result(agent: str) -> str:
    a = find(agent)
    if a is None:
        return f"No agent like '{agent}'."
    return f"{a.name} ({a.status}):\n{a.result}" if a.result else f"{a.name} hasn't finished yet ({a.status})."


def team_messages(agent: str = "", limit: int = 30, updates: bool = False) -> str:
    found = messages.recent(agent, max(1, min(int(limit or 30), 200)), include_updates=updates)
    if found:
        messages.mark_seen(found[-1]["id"])
    return messages.describe(found) or ("No messages yet." if not agent else f"No messages to or from '{agent}'.")


def answer_approval(approval: str, yes: bool) -> bool:
    return approvals.decide(approval, yes)
