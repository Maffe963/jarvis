"""A team of AI agents that Jarvis leads. They run in the team service (team_service.py), a process of their own,
so they keep working, and talking to each other, while Jarvis is off.

Each agent is its own Claude conversation with a role ("Researcher: finds and checks facts"), the read-and-research
tools background tasks get, plus its own: message a teammate, ask the user something, and finish with a report.
Agents work until they finish or are stopped; messages that arrive while one is busy wait until its current step
is done. Agents, their conversations and waiting messages are saved in memory/team/agents.json, so they carry on
after the service restarts. Every message goes in the team's message log (team/messages.py) and everything they do
is reported as events (team/events.py) for the dashboard, the team's Telegram bot and Jarvis.

Work that changes things (files written, project commands run) is checked before it's handed in: the agent can't
finish until a Reviewer agent, started automatically when needed, has reviewed it for safety, correctness and
efficiency and approved it (or it has had MAX_REVIEW_ROUNDS tries, in which case the open concerns go in its report).

Coders are agents just for programming (team/coding.py): each works in its own copy of a repository on its own
branch, runs the repository's checks, and opens a pull request; the Reviewer reviews pull requests and merges the
good ones into the main branch.

Standing agents (team/standing.py, defined in standing_agents.json) are specialists that stay on the team for good:
they never finish, keep a notebook, run on a schedule, and don't count towards MAX_AGENTS.
"""
import json
import os
import threading
import time
import uuid

from team import approvals, coding, events, messages, standing
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
STOP_NOTE = "(stopped)"  # wakes a waiting agent when it's stopped; never a job
QUIET = "[quiet]"  # a standing agent's whole reply when a job found nothing worth telling the user: nothing is posted

# ----- review -----
# using any of these means the work gets reviewed
CHANGING_TOOLS = ("write_file", "run_project_command", "write_code", "edit_code", "delete_code", "commit", "push")
KINDS = ("agent", "coder")  # what Jarvis can start; the Reviewer starts by itself, standing agents from their file
TEMPORARY = ("agent", "coder", "reviewer")  # agents that finish (not standing ones)
STANDING_HISTORY = 40  # turns a standing agent keeps; older ones live on in its notebook and the message log
SCHEDULED_REPLAY = 4  # turns replayed for a scheduled job (its notebook carries the rest), instead of the usual 12
STANDING_PROMPT = """You are {name}, one of the user's standing specialists on a team led by Jarvis, the user's
personal assistant. You stay on the team for good: each message (from the user, from Jarvis, from a teammate, or a
scheduled job) is a new job. Do it, reply, and wait for the next one. Your replies are posted in your own Telegram
topic, so write for a phone: plain text (no ** or #), short paragraphs, lists where they help.
Your role: {role}

Your instructions:
{task}

Your notebook, your long-term memory (what you've learned about the user and their goals, decisions, follow-ups):
{notebook}

Keep the notebook current with notebook_add, and tidy it with notebook_rewrite when it gets long; anything not in
it is forgotten after a while. Other agents on the team: {team}; message them with send_message, or Jarvis with
to="jarvis". Use ask_user only for what really needs the user. You can't use the screen, mouse, keyboard or apps.
You give information and analysis, not professional financial, legal or tax advice: say so when it matters, and
never present a guess as a fact. When a scheduled job finds nothing worth the user's attention, reply with exactly
[quiet] and nothing else: that reply isn't posted, so the user only hears from you when it matters. Today is {today}."""
MAX_REVIEW_ROUNDS = 3
REVIEWER_NAME = "Reviewer"
REVIEWER_ROLE = "Reviews teammates' code and files for safety, correctness and efficiency"
REVIEWER_TOOLS = {"read_file", "list_files", "find_files", "get_project_output", "web_search", "list_folder_access"}
REVIEWER_TASK = """Review the work your teammates send you. Each review request says what they did and which files
they wrote or commands they ran. For each request:
1. Read every file named with read_file (and get_project_output for commands they ran). Never approve unread work.
2. SAFETY first. Look for: deleting or overwriting anything the task didn't need; shell or PowerShell commands that
   could damage the PC, change system settings or run downloaded code; passwords, API keys or tokens written into
   files or output; building commands, SQL, HTML or file paths from untrusted text (injection); sending personal
   data anywhere; infinite loops or anything that could fill the disk.
3. CORRECTNESS: does it do what the task asked? Real bugs, missed edge cases, wrong paths, code that can't run.
4. EFFICIENCY: needless work, repeated or nested loops over big data, many calls where one would do, reading
   whole huge files when a part would do, code much longer or more complicated than it needs to be.
Ignore style and personal preference. Then call review_verdict once: approved=true only if there is no safety
problem and no real bug. Otherwise list each problem as: file, what's wrong, the exact fix. Be brief and specific.
Code from coders comes as a pull request ("<repository> #<number>"): read it with view_pull_request (its diff,
checks and whether it can merge), use read_pr_file when you need a whole file, and run_pr_check to run the tests
and lint yourself. Post your findings on the pull request with comment_on_pull_request too. When it's good, call
merge_pull_request first; only once it says it merged, call review_verdict with approved=true. If it couldn't merge
(failing checks, conflicts, changed since you looked, the user said no), call review_verdict with approved=false and
the reason, so the coder can fix it (if CI is still running, wait a minute and try again). list_pull_requests shows
what's open.
You can't change files yourself. If a teammate disagrees, weigh their reason fairly. When nobody has sent you
anything new, just end your turn and wait; finish only when asked, with a short summary of what you reviewed."""
CODER_GUIDE = """

You're a coder. Your workspace is your own copy (a git worktree) of the {repo} repository, on your own branch
{branch}, made from the latest {base}. Use the code tools, with paths relative to the repository root: list_code,
search_code and read_code to understand the code before changing it; edit_code for changes (write_code for new
files); run_check to run the tests and lint, and fix what fails. Keep the change to what your task needs and match
the style of the code around it. Then commit (a clear message), push, and open_pull_request (what changed, why, and
how you checked it). Then call request_review with files=["{repo} #<pull request number>"] and a short summary. The
Reviewer reviews the pull request and merges it into {base} when it's good; if it asks for changes, make them,
commit and push (the pull request updates), and request_review again. Finish once it's merged, with the pull request
link in your report. If other coders work on this repository too, stay in your own area and message them before
touching shared files. You can't push to {base} yourself or change anything outside your workspace."""


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
        self._nudged = False  # reminded to get unreviewed work reviewed before wrapping up
        self.cancelled = threading.Event()
        self.log = events.ActivityLog(self.id, saved.get("log"))
        self.result = saved.get("result", "")
        self.history: list[dict] = list(saved.get("history", []))
        self.started = saved.get("started", time.time())
        self.kind = saved.get("kind", "agent")  # or "coder" or "reviewer"
        self.workspace: dict | None = saved.get("workspace")  # a coder's worktree and branch
        self.parent: str = saved.get("parent", "")  # a helper: the standing agent that started it, and gets its result
        self.toolkits: list[str] = list(saved.get("toolkits", []))  # a helper: its parent's work tools
        # rounds asked for; how much of the log the last request covered, and how much an approval has cleared
        self.review: dict = saved.get("review") or {"rounds": 0, "requested": 0, "cleared": 0, "notes": ""}
        self.session = None

    def info(self) -> dict:
        ws = self.workspace or {}
        return {"id": self.id, "name": self.name, "role": self.role, "task": self.task, "status": self.status,
                "steps": len(self.log), "result": self.result, "started": self.started, "kind": self.kind,
                "repo": ws.get("repo", ""), "branch": ws.get("branch", ""), "parent": self.parent, "doing": self.log[-1][:160] if self.log else ""}

    def saved(self) -> dict:
        return {**self.info(), "inbox": list(self.inbox), "history": list(self.history), "log": list(self.log),
                "review": dict(self.review), "ending": self.cancelled.is_set(), "workspace": self.workspace,
                "toolkits": list(self.toolkits)}

    def unreviewed_changes(self) -> bool:
        """Whether it wrote files or ran project commands since its work was last approved."""
        return any(entry.split(":", 1)[0] in CHANGING_TOOLS for entry in list(self.log)[self.review["cleared"]:])

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
            if self.parent:  # the user talks to the specialist, which answers for its helpers
                deliver(self.id, self.parent, f"Question from your helper {self.name}: {question}")
                return (f"Your question went to {self.parent}, who'll answer (asking the user if needed). End your "
                        "turn now; the answer comes back as a message.")
            self.set_status("needs you", question)
            message = messages.add(self.name, self.id, "user", "user", question, kind="question")
            events.emit("message", sender=self.id, to="user", text=question, msg=message["id"])
            system.announce(f"Sir, {self.name} has a question: {_short(question, 200)}")
            return ("Your question was sent to the user. End your turn now; their answer will arrive as a message. "
                    "Meanwhile don't repeat the question.")

        def finish(inputs: dict) -> str:
            report = inputs.get("report", "").strip() or "(no report)"
            if self.kind != "reviewer" and self.unreviewed_changes():
                if self.review["rounds"] < MAX_REVIEW_ROUNDS:
                    return ("Not yet: you wrote files or ran commands, so your work must be reviewed first. Call "
                            "request_review with the files and what you did, end your turn, and finish once the "
                            "Reviewer approves (fix what it asks for and request another review if it doesn't).")
                report += f"\n\nNote: the Reviewer still had concerns after {MAX_REVIEW_ROUNDS} rounds: " + \
                    (self.review["notes"] or "(none given)")
            self.result = report
            self.cancelled.set()  # stops after this step
            return "Finished. Your report has been handed to Jarvis."

        def request_review(inputs: dict) -> str:
            reviewer = ensure_reviewer()
            if reviewer is None:
                self.review["cleared"] = len(self.log)
                return ("No Reviewer could be started (the team is full), so you may finish; say in your report that "
                        "the work wasn't reviewed.")
            self.review.update(rounds=self.review["rounds"] + 1, requested=len(self.log))
            save()
            files = inputs.get("files") or []
            deliver(self.id, reviewer.id, f"Review request (round {self.review['rounds']} of {MAX_REVIEW_ROUNDS}) "
                                          f"from {self.name}.\nWhat I did: {inputs.get('summary', '')}\n"
                                          f"Files and commands: {', '.join(files) if files else '(none listed)'}")
            return f"Sent to {reviewer.name}. End your turn now; its verdict will come to you as a message."

        def review_verdict(inputs: dict) -> str:
            author = find(inputs.get("author", ""))
            if author is None or author is self or author.status in FINISHED:
                return f"No active teammate called '{inputs.get('author', '')}'."
            approved, notes = bool(inputs.get("approved")), inputs.get("notes", "").strip()
            author.review["notes"] = notes
            if approved:  # covers what it had done when it asked, not anything it changes afterwards
                author.review["cleared"] = author.review["requested"]
            save()
            deliver(self.id, author.id, ("APPROVED. " if approved else "CHANGES NEEDED. ") + (notes or ""))
            return f"Sent your verdict to {author.name}."

        def view_file(inputs: dict):
            from team import video

            try:
                return video.view_file(inputs.get("path", ""))
            except Exception as e:  # not allowed, missing, unreadable: tell the agent
                return f"Couldn't show that: {e}"

        def team_list(_: dict) -> str:
            return "\n".join(f"{a.name} ({a.role}) - {a.status}" for a in active() if a is not self) or "Just you."

        def logged(name: str, handler):  # so the dashboard shows these steps and the review gate sees changes
            def run(inputs: dict) -> str:
                result = handler(inputs)
                self.log.append(f"{name}: {_step_summary(result)}")
                return result
            return run

        extra = {}
        if self.kind == "standing":
            extra = standing.tools_for(self, standing.definition(self.name))
        elif self.kind == "coder" and self.workspace:
            extra = coding.coder_tools(self.workspace)
        elif self.kind == "reviewer":
            extra = coding.reviewer_tools(lambda q: approvals.ask(q, self.name, self.id))
        elif self.toolkits:  # a standing agent's helper: the same work tools (its video tools, say)
            extra = standing.tools_for(self, {"toolkits": self.toolkits}, notebook=False)
        extra = {name: (schema, logged(name, handler)) for name, (schema, handler) in extra.items()}

        text = {"type": "string"}
        common = {
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
            "view_file": ({"name": "view_file", "description": "Open a file the user sent or any file on the PC: "
                           "see an image (screenshot, photo), frames from across a video, or read a plain text "
                           "file (notes, CSV, JSON, logs, code).",
                           "input_schema": {"type": "object", "properties": {"path": text},
                                            "required": ["path"]}}, view_file),
        } | ({
            "review_verdict": ({"name": "review_verdict", "description": "Give a teammate your review: approved or "
                                "not, with each problem and its fix.",
                                "input_schema": {"type": "object", "properties": {
                                    "author": text, "approved": {"type": "boolean"}, "notes": text},
                                    "required": ["author", "approved", "notes"]}}, review_verdict),
        } if self.kind == "reviewer" else {
            "request_review": ({"name": "request_review", "description": "Ask the Reviewer to check files you "
                                "wrote or commands you ran (needed before you can finish such work).",
                                "input_schema": {"type": "object", "properties": {
                                    "files": {"type": "array", "items": text,
                                              "description": "Full paths of files written, and commands run"},
                                    "summary": {"type": "string", "description": "What you did and why"}},
                                    "required": ["files", "summary"]}}, request_review),
        })
        if self.kind == "standing":  # never finishes and isn't reviewed; just its jobs
            common = {k: v for k, v in common.items() if k not in ("finish", "request_review")}
        return extra | common

    # ----- running -----

    def _helping(self) -> bool:
        """A helper whose specialist is still on the team, or with its own renders or transcripts running: more
        will come (the specialist's reply, or a job's result), so it isn't left alone."""
        if not self.parent:
            return False
        from team import video

        with video._jobs_lock:
            if any(j["agent"] == self.name and j["status"] == "running" for j in video._jobs.values()):
                return True
        parent = find(self.parent)
        return parent is not None and parent.status not in FINISHED

    def _wait_for_message(self, last_reply: str) -> str | None:
        """The next message, or None if it's time to stop: idle for hours, or left waiting after everyone else on
        the team has finished (nobody is going to message it), unless it's waiting on the user."""
        waited, alone_since = 0.0, None
        while waited < IDLE_TIMEOUT_S and not self.cancelled.is_set():
            message = self._next(timeout=5)
            if message is not None:
                return message
            waited += 5
            others = [a for a in active() if a is not self and a.kind != "standing"]  # those never finish
            if others or self.status == "needs you" or self._helping():
                alone_since = None
            else:
                alone_since = alone_since if alone_since is not None else waited
                if waited - alone_since >= ALONE_GRACE_S:
                    break
        if self.cancelled.is_set():  # stopped isn't finished
            return None
        if self.kind != "reviewer" and self.unreviewed_changes() and not self._nudged:
            self._nudged = True
            return ("(You've been waiting with nothing new coming. You wrote files or ran commands that haven't "
                    "been reviewed: call request_review now, then finish once the Reviewer approves.)")
        self.result = self.result or last_reply or "(finished without a report)"
        if self.kind != "reviewer" and self.unreviewed_changes():
            self.result += "\n\nNote: this agent wrote files or ran commands that were never reviewed."
        return None

    def run(self, resumed: bool = False) -> None:
        if self.kind == "standing":
            _run_standing(self, resumed)
            return
        import brain
        from tools import background

        team = ", ".join(f"{a.name} ({a.role})" for a in active() if a is not self) or "none yet"
        prompt = PROMPT.format(name=self.name, role=self.role, team=team)
        allowed = REVIEWER_TOOLS if self.kind == "reviewer" else background.ALLOWED
        if self.parent:  # never more than its specialist may use
            allowed = set(standing.definition(self.parent).get("tools", [])) & background.ALLOWED
        if self.kind == "coder" and self.workspace:
            prompt += CODER_GUIDE.format(**self.workspace)
            allowed = {"web_search"}  # everything else it does goes through its workspace's tools
        self.session = brain.ClaudeCodeSession(prompt, allowed, self.cancelled, self.log, self._tools(),
                                               label=self.name)
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
        self.session.dispose()
        if self.workspace:
            note = coding.remove_workspace(self.workspace)
            if note and self.result:
                self.result += f"\n\n{note}"
        if self.parent:  # a helper reports to the specialist that started it, not to the user
            done = self.result or "(it stopped without a result)"
            if self.status not in FINISHED:
                self.set_status("done" if self.result else "stopped", _short(done, 120))
            events.emit("result", agent=self.id, text=done)
            deliver(self.id, self.parent, f"Your helper {self.name} is done ({self.status}). Its result:\n{done}")
        elif self.status == "failed":
            _report(self)
        elif self.result:
            logged = messages.add(self.name, self.id, "Jarvis", "jarvis", self.result, kind="result")
            self.set_status("done", _short(self.result, 120))
            events.emit("result", agent=self.id, text=self.result, msg=logged["id"])
            _report(self)
        elif self.status not in FINISHED:
            self.set_status("stopped")


def _standing_turn(agent: "Agent", message: str) -> str:
    """One job for a standing agent, in a fresh Claude Code process that's closed afterwards (so an idle standing
    agent uses nothing). Its recent turns are replayed, and its notebook is part of its instructions."""
    import brain
    from tools import background

    spec = standing.definition(agent.name)
    team = ", ".join(f"{a.name} ({a.role})" for a in active() if a is not agent) or "none"
    prompt = STANDING_PROMPT.format(name=agent.name, role=agent.role, task=spec.get("instructions", agent.task),
                                    notebook=standing.notebook_for_prompt(agent.name),
                                    team=team, today=time.strftime("%A, %B %d, %Y"))
    allowed = set(spec.get("tools", [])) & background.ALLOWED  # never more than task agents get
    agent.history.append({"role": "user", "content": message})
    del agent.history[:-STANDING_HISTORY]
    agent.set_status("working", _short(message, 120))
    session = agent.session = brain.ClaudeCodeSession(prompt, allowed, agent.cancelled, agent.log, agent._tools(),
                                                     label=agent.name, model=spec.get("model"))
    scheduled = message.startswith("(Scheduled job")
    try:
        reply = session.ask(agent.history[-SCHEDULED_REPLAY:] if scheduled else agent.history,
                            lambda q: approvals.ask(q, agent.name, agent.id))
    finally:
        session.dispose()
        agent.session = None
    agent.history.append({"role": "assistant", "content": reply})
    standing.job_done(agent)  # e.g. the emails it was shown now count as handled
    return reply


def _run_standing(agent: "Agent", resumed: bool) -> None:
    import brain

    message = RESUMED if resumed and agent.status == "working" and agent.history else None
    while not agent.cancelled.is_set():
        if message is None:
            if agent.status != "waiting":
                agent.set_status("waiting", "ready for its next job")
            while message is None and not agent.cancelled.is_set():
                message = agent._next(timeout=5)
                if message == STOP_NOTE:
                    message = None
            if message is None or agent.cancelled.is_set():
                break
        try:
            reply = _standing_turn(agent, message)
        except brain.TaskInterrupted:
            break
        except Exception as e:  # one failed job doesn't end a standing agent
            if _shutting_down.is_set():
                return
            reply = f"(That job failed: {e})"
            agent.history.append({"role": "assistant", "content": reply})
        asked, message = (message or "").startswith("Message from the user"), None
        if reply.strip().startswith(QUIET) and asked:
            reply = "Nothing new to report right now."  # the user asked: they get an answer, even an empty one
        if reply.strip() and not reply.strip().startswith(QUIET):
            logged = messages.add(agent.name, agent.id, "Jarvis", "jarvis", reply, kind="update")
            events.emit("message", sender=agent.id, to="jarvis", text=reply.strip()[:3500], kind="update",
                        msg=logged["id"])  # line breaks kept: this is the agent's answer, read in its topic
        save()
    if not _shutting_down.is_set():
        agent.set_status("stopped")



def sync_standing() -> str:
    """Start the standing agents defined in standing_agents.json that aren't running, and keep the running ones'
    roles up to date. Returns what changed."""
    started, stopped = [], []
    for name, spec in standing.definitions().items():
        mine = [a for a in everyone() if a.kind == "standing" and a.name == name]
        current = next((a for a in mine if a.status not in FINISHED), None) or (mine[-1] if mine else None)
        if current:
            current.role, current.task = spec.get("role", current.role), spec.get("instructions", current.task)
            if current.status in FINISHED:  # stopped earlier: back with its history and Telegram topic
                current.cancelled = threading.Event()
                with current._wake:
                    current.inbox[:] = [m for m in current.inbox if m != STOP_NOTE]
                current.set_status("waiting", "back on the team")
                threading.Thread(target=current.run, args=(True,), daemon=True, name=f"agent-{name}").start()
                started.append(name)
            continue
        result = create_agent(name, spec.get("role", ""), spec.get("instructions", ""), kind="standing")
        started.append(name if result.startswith("Created") else f"{name} ({result})")
    for agent in active():  # turned off in standing_agents.json while running
        if agent.kind == "standing" and agent.name not in standing.definitions():
            stop_agent(agent.id)
            stopped.append(agent.name)
    save()
    changes = ([f"started {', '.join(started)}"] if started else []) + ([f"turned off {', '.join(stopped)}"] if stopped else [])
    text = "; ".join(changes)
    return text[:1].upper() + text[1:] + "." if changes else "All standing agents are running."


def _step_summary(result) -> str:
    """One readable line for the dashboard: the text of a tool's result (images just counted), not its raw form."""
    if isinstance(result, list):
        texts = [b.get("text", "") for b in result if isinstance(b, dict) and b.get("type") == "text"]
        images = sum(1 for b in result if isinstance(b, dict) and b.get("type") == "image")
        result = " ".join(texts) + (f" ({images} image{'s' if images != 1 else ''})" if images else "")
    return (str(result).strip().splitlines() or [""])[0][:100]


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


_reviewer_lock = threading.Lock()


def ensure_reviewer() -> Agent | None:
    """The team's Reviewer, started if there isn't one working. None if the team is full."""
    with _reviewer_lock:  # two agents asking at once still get one Reviewer
        return _ensure_reviewer()


def _ensure_reviewer() -> Agent | None:
    reviewer = next((a for a in active() if a.kind == "reviewer"), None)
    if reviewer is None:
        taken = any(a.name.lower() == REVIEWER_NAME.lower() for a in active())  # e.g. one the user named so
        result = create_agent(f"{REVIEWER_NAME} (team)" if taken else REVIEWER_NAME, REVIEWER_ROLE, REVIEWER_TASK,
                              kind="reviewer")
        reviewer = next((a for a in active() if a.kind == "reviewer"), None)
        if reviewer is None:
            print(f"[team] no reviewer: {result}")
    return reviewer


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
    os.makedirs(messages.FOLDER, exist_ok=True)
    with _save_lock:  # snapshot and write together, so an older snapshot never overwrites a newer one
        with _lock:
            done = [a for a in _agents.values() if a.status in FINISHED and a.kind != "standing"]
            for old in done[:-KEEP_FINISHED]:  # forget long-finished task agents (standing ones keep their place)
                del _agents[old.id]
            agents = list(_agents.values())
        kept = [a.saved() for a in agents]
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
            if item.get("ending") and agent.status not in FINISHED:  # stopped or finished just before a restart
                agent.status = "done" if agent.result else "stopped"
            if agent.kind == "standing" and agent.name not in standing.definitions() and agent.status not in FINISHED:
                agent.status = "stopped"  # removed from standing_agents.json, or "enabled": false
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

def create_agent(name: str, role: str, task: str, kind: str = "agent", repo: str = "", parent: str = "",
                 toolkits: list[str] | None = None) -> str:
    """`kind`: "agent", "coder" (works on `repo`, see team/coding.py), "reviewer" (the team's Reviewer) or
    "standing" (see team/standing.py); the last two don't count towards MAX_AGENTS."""
    specialist = next((n for n in standing.definitions() if n.lower() == name.strip().lower()), None)
    if kind in KINDS and specialist:  # never a temporary copy of a standing specialist
        return (f"{specialist} is one of the user's standing specialists, not something to create: pass the "
                f"request to it with message_agent. If it's stopped, start_specialists brings it back.")
    if kind in KINDS and len([a for a in active() if a.kind in KINDS]) >= MAX_AGENTS:
        return f"There are already {MAX_AGENTS} agents working; stop one first."
    if any(a.name.lower() == name.strip().lower() for a in active()):  # exact: "Writer" isn't "Ghostwriter"
        return f"There's already an active agent called {name}; pick another name."
    agent = Agent(name.strip() or "Agent", role.strip(), task.strip(), {"kind": kind})
    if kind == "coder":
        workspace = coding.create_workspace(repo, agent.name, agent.id)
        if isinstance(workspace, str):
            return workspace
        agent.workspace = workspace
    agent.parent, agent.toolkits = parent, list(toolkits or [])
    with _lock:
        _agents[agent.id] = agent
    events.emit("agent", agent=agent.id, name=agent.name, role=agent.role, task=agent.task, kind=kind, parent=parent)
    save()
    threading.Thread(target=agent.run, daemon=True, name=f"agent-{agent.name}").start()
    where = f" on branch {agent.workspace['branch']} of {agent.workspace['repo']}" if agent.workspace else ""
    return (f"Created {agent.name} [{agent.id}] ({agent.role}){where}. It's working on it; the dashboard and its "
            "Telegram topic show its progress.")


def message_agent(agent: str, text: str, via: str = "jarvis") -> str:
    return deliver("user", agent, text, via)


def stop_agent(agent: str) -> str:
    every = agent.lower().strip() in ("all", "everyone", "everybody")  # all the task agents, not standing ones
    targets = [a for a in active() if a.kind != "standing"] if every else [find(agent)]
    targets = [a for a in targets if a is not None and a.status not in FINISHED]
    for a in targets:
        a.cancelled.set()  # its own thread reports it stopped, once it is
        a.put(STOP_NOTE)
    if not targets:
        return ("No task agents are running (standing agents are stopped one at a time, by name)." if every
                else f"No active agent like '{agent}'.")
    return f"Stopped {', '.join(a.name for a in targets)}."


def clear_finished() -> str:
    """Take finished task agents off the team (and the dashboard). Standing specialists and their history stay;
    the message log keeps everything that was said."""
    with _lock:
        gone = [a for a in _agents.values() if a.status in FINISHED and a.kind != "standing"]
        for a in gone:
            del _agents[a.id]
    if not gone:
        return "There are no finished agents to clear."
    save()
    events.emit("cleared", agents=[a.id for a in gone])
    return f"Cleared {len(gone)} finished agent{'s' if len(gone) != 1 else ''} off the team."


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
    return messages.describe(found) or ("No messages yet." if not agent else f"No messages to or from '{agent}'.")


def answer_approval(approval: str, yes: bool) -> bool:
    return approvals.decide(approval, yes)
