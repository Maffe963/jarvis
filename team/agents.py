"""A team of AI agents that Jarvis leads.

Each agent is its own Claude conversation with a role ("Researcher: finds and checks facts"), the read-and-research
tools background tasks get, plus its own: message a teammate, ask the user something, and finish with a report.
Agents work until they finish or are stopped; messages that arrive while one is busy wait until its current step
is done. Everything they do is reported to the dashboard (team/events.py).
"""
import queue
import threading
import time
import uuid

from team import approvals, events
from tools import system

MAX_AGENTS = 5
IDLE_TIMEOUT_S = 3 * 3600  # an agent waiting this long with nothing to do finishes on its own

_agents: dict[str, "Agent"] = {}
_lock = threading.Lock()

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
ALONE_GRACE_S = 60  # a waiting agent whose teammates have all finished wraps up after this long


def _short(text: str, n: int = 160) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


class Agent:
    def __init__(self, name: str, role: str, task: str) -> None:
        self.id = uuid.uuid4().hex[:6]
        self.name, self.role, self.task = name, role, task
        self.status = "starting"
        self.inbox: queue.Queue = queue.Queue()
        self.cancelled = threading.Event()
        self.log = events.ActivityLog(self.id)
        self.result = ""
        self.history: list[dict] = []
        self.started = time.time()

    def info(self) -> dict:
        return {"id": self.id, "name": self.name, "role": self.role, "task": self.task, "status": self.status,
                "steps": len(self.log), "result": self.result, "started": self.started}

    def set_status(self, status: str, note: str = "") -> None:
        self.status = status
        events.emit("status", agent=self.id, status=status, note=_short(note))

    # ----- the agent's own tools -----

    def _tools(self) -> dict:
        def send_message(inputs: dict) -> str:
            return deliver(self.name, inputs.get("to", ""), inputs.get("text", ""))

        def ask_user(inputs: dict) -> str:
            question = inputs.get("question", "")
            self.set_status("needs you", question)
            system.announce(f"Sir, {self.name} has a question: {_short(question, 200)}")
            events.emit("message", sender=self.id, to="user", text=question)
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
            try:
                return self.inbox.get(timeout=5)
            except queue.Empty:
                waited += 5
            others = [a for a in active() if a is not self]
            if others or self.status == "needs you":
                alone_since = None
            else:
                alone_since = alone_since if alone_since is not None else waited
                if waited - alone_since >= ALONE_GRACE_S:
                    break
        self.result = self.result or last_reply or "(finished without a report)"
        return None

    def run(self) -> None:
        import brain
        from tools import background

        team = ", ".join(f"{a.name} ({a.role})" for a in active() if a is not self) or "none yet"
        prompt = PROMPT.format(name=self.name, role=self.role, team=team)
        session = brain.ClaudeCodeSession(prompt, background.ALLOWED, self.cancelled, self.log, self._tools())
        message = f"Your task from the user (via Jarvis):\n{self.task}"
        try:
            while not self.cancelled.is_set():
                self.set_status("working", _short(message, 120))
                self.history.append({"role": "user", "content": message})
                try:
                    reply = session.ask(self.history, lambda q: approvals.ask(q, self.name))
                except brain.TaskInterrupted:
                    break
                self.history.append({"role": "assistant", "content": reply})
                if reply.strip():
                    events.emit("message", sender=self.id, to="jarvis", text=_short(reply, 400), kind="update")
                if self.cancelled.is_set():
                    break
                self.set_status("waiting" if self.status != "needs you" else "needs you", "waiting for a message")
                message = self._wait_for_message(reply)
                if message is None:
                    break
        except Exception as e:
            self.set_status("failed", str(e))
            self.result = f"It failed: {e}"
        finally:
            session.close()
        if self.status == "failed":
            _report(self)
        elif self.result:
            self.set_status("done", _short(self.result, 120))
            events.emit("result", agent=self.id, text=self.result)
            _report(self)
        elif self.status != "failed":
            self.set_status("stopped")


def _report(agent: Agent) -> None:
    phone = approvals._phone()
    failed = agent.status == "failed"
    if phone:
        phone.send(f"{'⚠️' if failed else '✅'} {agent.name} {'ran into a problem' if failed else 'finished'}:"
                   f"\n\n{agent.result}")
    headline = _short(agent.result.splitlines()[0], 160) if agent.result else ""
    system.announce(f"Sir, {agent.name} {'ran into a problem' if failed else 'has finished'}. {headline}"
                    + (" The full result is on your phone and the dashboard." if phone else " It's on the dashboard."))


def active() -> list[Agent]:
    with _lock:
        return [a for a in _agents.values() if a.status not in ("done", "stopped", "failed")]


def everyone() -> list[Agent]:
    with _lock:
        return list(_agents.values())


def find(name_or_id: str) -> Agent | None:
    wanted = name_or_id.lower().strip()
    agents = everyone()
    return next((a for a in agents if a.id == wanted), None) or \
        next((a for a in reversed(agents) if a.name.lower() == wanted), None) or \
        next((a for a in reversed(agents) if wanted and wanted in a.name.lower()), None)


def deliver(sender: str, to: str, text: str) -> str:
    """A message from an agent, the user or Jarvis to an agent (or to Jarvis/the user)."""
    sender_agent = find(sender)
    sender_id = sender_agent.id if sender_agent else sender.lower()
    if to.lower().strip() in ("jarvis", "user", "the user"):
        events.emit("message", sender=sender_id, to="jarvis", text=text)
        return "Sent to Jarvis."
    target = find(to)
    if target is None or target.status in ("done", "stopped", "failed"):
        names = ", ".join(a.name for a in active()) or "none"
        return f"No active teammate called '{to}'. Active: {names}."
    events.emit("message", sender=sender_id, to=target.id, text=text)
    who = sender_agent.name if sender_agent else ("the user (via Jarvis)" if sender_id in ("user", "jarvis") else sender)
    target.inbox.put(f"Message from {who}: {text}")
    if target.status == "needs you" and not sender_agent:
        target.set_status("working", "got an answer")
    return f"Sent to {target.name}."


# ---------- Jarvis's tools ----------

def create_agent(name: str, role: str, task: str) -> str:
    if len(active()) >= MAX_AGENTS:
        return f"There are already {MAX_AGENTS} agents working; stop one first."
    if find(name) and find(name).status not in ("done", "stopped", "failed"):
        return f"There's already an active agent called {name}; pick another name."
    agent = Agent(name.strip() or "Agent", role.strip(), task.strip())
    with _lock:
        _agents[agent.id] = agent
    events.emit("agent", agent=agent.id, name=agent.name, role=agent.role, task=agent.task)
    threading.Thread(target=agent.run, daemon=True, name=f"agent-{agent.name}").start()
    return f"Created {agent.name} [{agent.id}] ({agent.role}). It's working on it; the dashboard shows its progress."


def message_agent(agent: str, text: str) -> str:
    return deliver("user", agent, text)


def stop_agent(agent: str) -> str:
    targets = active() if agent.lower().strip() in ("all", "everyone", "everybody") else [find(agent)]
    targets = [a for a in targets if a is not None and a.status not in ("done", "stopped", "failed")]
    for a in targets:
        a.cancelled.set()
        a.inbox.put("(stopped)")
        a.set_status("stopped")
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


def open_dashboard() -> str:
    from dashboard import server

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
                       "Give each a short name and complete standalone instructions.",
        "input_schema": {"type": "object", "properties": {
            "name": {"type": "string", "description": "Short name, e.g. 'Researcher'"},
            "role": {"type": "string", "description": "One line: what this agent is responsible for"},
            "task": {"type": "string", "description": "Full instructions, including who to hand results to"}},
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
        "name": "open_dashboard",
        "description": "Open the live agent dashboard in the browser.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
]

TOOL_MAP = {"create_agent": create_agent, "message_agent": message_agent, "stop_agent": stop_agent,
            "team_status": team_status, "agent_result": agent_result, "open_dashboard": open_dashboard}
