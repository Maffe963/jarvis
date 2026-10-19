import atexit
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime

from tools import facts, usage
from tools.registry import TOOLS, run_tool, task_cancelled

SYSTEM_PROMPT = """You are Jarvis, a highly capable AI assistant in the style of Tony Stark's Jarvis.
You are helpful, precise, and slightly formal but personable. You run on the user's Windows PC and can
control it with tools: launching and closing apps, switching windows, media and volume, opening websites,
system status, power, timers, files, web search, and PowerShell as a last resort.
When asked to do something, just do it with the tools rather than explaining how.
Risky actions are confirmed with the user automatically; if one is declined, acknowledge it and move on.
Jarvis asks those confirmations aloud and the user answers by voice; there is no on-screen prompt or Allow
button, so never tell them to click one. For files, just use the tool: Jarvis only asks when it needs to,
and the user may have granted folders or whole drives (list_folder_access shows which). Prefer the file
tools over PowerShell for looking at files, since PowerShell always needs a confirmation.
"Shut down" or "turn off" on its own is ambiguous: ask whether they mean you (Jarvis) or the computer.
To close yourself, use quit_jarvis; only use power for the computer when they clearly mean the computer.
Your replies are spoken aloud: keep them to one or two short sentences, with no markdown, lists, or URLs.
When you use a tool, Jarvis automatically says "Yes sir, <what you're doing>" aloud as it starts (for
example "Yes sir, opening Spotify."), so never repeat or re-confirm the action afterwards. If the action
simply worked and you have nothing new to add, reply with exactly: [done]
If the tool returned information the user wanted, give just that information briefly ("CPU is at twelve
percent."). If it failed or the user declined it, say so plainly. For questions and conversation that
need no tool, just answer naturally, using "sir" now and then.
Each message starts with the time and the window the user has in front of them. When they say "this", "it"
or "here" without saying what, they usually mean that window: use look_at_screen to see it if you need to.
Check your own work: after a series of on-screen actions (clicking, typing, opening or moving windows) where
the result matters, look_at_screen once to confirm it actually worked, and fix it if not. Skip this for
simple actions whose tool result already confirms success.
Learn from corrections: when the user corrects you or stops you to say what they really meant, and it's a
lasting preference rather than a one-off (e.g. "Chrome means the window on my right screen", "my resume is
the PDF, not the Word file"), save it with remember so you get it right next time.
Do quick things yourself: anything on the screen or in apps, a quick fact or single search, reminders, lists,
music. Anything bigger (research, comparing options, several look-ups, writing or summarising something longer,
planning) goes to a temporary agent with create_agent, so the user can watch it on their dashboard: give it a
short fitting name and role (e.g. "Researcher", "Writer") and a complete task, then tell the user in one sentence
it's on it; its result reaches them when it's done (if the team is full, just do it yourself). If the job belongs to one of the user's standing specialists
(jobs, travel, trading, portfolio, email replies, real estate, video), send it to that specialist with
message_agent instead. Only use start_background_task when the user asks for a background task by name."""


def screen_context() -> str:
    """'[Current time: ...] [Front window: "title" (App)]' for the start of each message."""
    parts = [f"Current time: {datetime.now():%A, %B %d, %Y, %I:%M %p}"]
    try:
        import win32gui

        from tools import system

        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
        shown = hwnd and title and title not in ("Program Manager", "Settings") and win32gui.IsWindowVisible(hwnd)
        window = system._window_info(hwnd, title) if shown else None  # just this window, not every one
        if window:
            parts.append(f'Front window: "{window["title"][:80]}" ({system._app_label(window)})')
    except Exception:
        pass
    return " ".join(f"[{p}]" for p in parts)

# "api" bills an Anthropic API key per request; "subscription" goes through Claude Code (`claude -p`)
# signed in with your Claude plan, and counts against that plan's usage limits.
BACKEND = os.environ.get("JARVIS_BACKEND", "api").strip().lower()
SUBSCRIPTION_HISTORY_TURNS = 12


class BackendError(Exception):
    """A failure with a message fit to say to the user."""


class TaskInterrupted(Exception):
    """The user stopped Jarvis part-way through a request."""


def cancel_task() -> None:
    """Stop the request in progress (safe from any thread): no more tools, and Claude stops thinking."""
    task_cancelled.set()


NEW_MESSAGE = object()  # passed to on_text when Claude starts a new message (e.g. after using a tool)


def think(conversation_history: list[dict], confirm=None, acknowledge=None, on_text=None) -> str:
    """Run one turn. `confirm(question) -> bool` is asked before risky tool calls; without it they're refused.
    `on_text(piece)` receives the reply as it's written (and NEW_MESSAGE between messages)."""
    if BACKEND == "subscription":
        return _get_session().ask(conversation_history, confirm, acknowledge, on_text)
    system = f"{SYSTEM_PROMPT}{facts.prompt_section()}\n\n{screen_context()}"
    return _think_api(conversation_history, system, confirm, acknowledge, on_text)


# ---------- API backend ----------

_client = None


def _think_api(conversation_history: list[dict], system: str, confirm, acknowledge, on_text=None,
               tools: list[dict] | None = None, cancelled: threading.Event | None = None,
               log: list[str] | None = None) -> str:
    global _client
    from anthropic import Anthropic

    if _client is None:
        _client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    messages = conversation_history.copy()
    cancelled = task_cancelled if cancelled is None else cancelled

    while True:
        if cancelled.is_set():
            raise TaskInterrupted()
        with _client.messages.stream(
            model=os.environ.get("JARVIS_MODEL", "claude-sonnet-5-5"),
            max_tokens=4096,
            system=system,
            tools=TOOLS if tools is None else tools,
            messages=messages,
        ) as stream:
            if on_text:
                on_text(NEW_MESSAGE)
                for piece in stream.text_stream:
                    on_text(piece)
            response = stream.get_final_message()
        usage.record("Jarvis (API)" if cancelled is task_cancelled else "Background (API)",
                     {response.id: response.usage.model_dump()}, response.model)

        if response.stop_reason == "end_turn":
            text_blocks = [b.text for b in response.content if b.type == "text"]
            return " ".join(text_blocks).strip()

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    if cancelled.is_set():
                        raise TaskInterrupted()
                    result = run_tool(block.name, block.input, confirm, acknowledge, cancelled, log)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})
        else:
            break

    return "I encountered an issue processing your request, sir."


# ---------- Claude Code subscription backend ----------

INTERRUPT_WAIT_S = 3  # how long Claude Code gets to stop a turn before it's ended instead
REFRESH_AFTER_TURNS = 40  # then a fresh Claude Code is started in a quiet moment (see _refresh)
REFRESH_IDLE_S = 90


class ClaudeCodeSession:
    """One long-running `claude -p` that takes a message per turn over stream-json.

    Starting Claude Code costs a few seconds, so it's started once and kept warm; follow-up replies
    then take about a second. Claude Code keeps the conversation itself. If the process dies, the
    next turn starts a fresh one and replays recent history so nothing is forgotten.
    """

    def __init__(self, system_prompt: str | None = None, allowed_tools: set[str] | None = None,
                 cancelled: threading.Event | None = None, log: list[str] | None = None,
                 extra_tools: dict | None = None, label: str = "Jarvis") -> None:
        """Defaults: the main conversation. Background tasks pass their own prompt, tools and stop switch, and a
        label saying whose session it is (for the dashboard's usage and session lists)."""
        from tools.bridge import ToolBridge

        self.system_prompt = system_prompt or SYSTEM_PROMPT
        self.label = label
        self.cancelled = task_cancelled if cancelled is None else cancelled

        claude = shutil.which("claude")
        if not claude:
            raise BackendError("I can't find Claude Code on this PC, sir. Please install it or switch to API mode.")
        self.bridge = ToolBridge(allowed_tools, self.cancelled, log, extra_tools)
        config = {"mcpServers": {"jarvis": {
            "command": sys.executable,
            "args": [os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_server.py")],
            "env": {"JARVIS_BRIDGE_URL": self.bridge.url, "JARVIS_BRIDGE_TOKEN": self.bridge.token},
        }}}
        fd, self._mcp_config_path = tempfile.mkstemp(prefix="jarvis-mcp-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(config, f)
        self._cmd = [
            claude, "-p",
            "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--include-partial-messages",       # the reply arrives word by word, so speech can start early
            "--model", os.environ.get("JARVIS_MODEL", "sonnet"),
            "--effort", os.environ.get("JARVIS_EFFORT", "low"),
            "--system-prompt", self.system_prompt,
            "--tools", "",                      # no built-in Claude Code tools (Bash, Edit, ...)
            "--strict-mcp-config", "--mcp-config", self._mcp_config_path,
            "--allowedTools", "mcp__jarvis",    # only Jarvis's own tools
            "--permission-mode", "dontAsk",
            "--no-session-persistence",
            "--disable-slash-commands",
        ]
        self._proc = None
        self._events: queue.Queue = queue.Queue()
        self._stderr: list[str] = []
        self._fresh = True  # next message is the first this process sees
        self._turns = 0     # turns this process has had (its context grows with each)
        self._in_use = threading.Lock()  # held during a turn, so a refresh never cuts one off
        atexit.register(self.close)

    def _start(self) -> None:
        env = os.environ.copy()
        # If an API key is visible, Claude Code bills it instead of using the subscription.
        for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDECODE"):
            env.pop(var, None)
        env["JARVIS_SESSION"] = self.label  # so the dashboard can tell whose Claude Code process this is
        workdir = os.path.join(tempfile.gettempdir(), "jarvis-claude-code")
        os.makedirs(workdir, exist_ok=True)
        cmd = list(self._cmd)
        cmd[cmd.index("--system-prompt") + 1] = self.system_prompt + facts.prompt_section()  # memory as of now
        self._proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", env=env, cwd=workdir, bufsize=1,
        )
        self._events = queue.Queue()
        self._stderr = []
        self._fresh = True
        self._turns = 0
        proc, events = self._proc, self._events

        def pump_stdout():
            for line in proc.stdout:
                try:
                    events.put(json.loads(line))
                except json.JSONDecodeError:
                    pass
            events.put(None)  # process ended

        def pump_stderr():
            for line in proc.stderr:
                self._stderr.append(line)

        threading.Thread(target=pump_stdout, daemon=True).start()
        threading.Thread(target=pump_stderr, daemon=True).start()

    def warm_up(self) -> None:
        if self._proc is None or self._proc.poll() is not None:
            self._start()

    def close(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.kill()
            try:
                self._proc.wait(timeout=5)  # so warm_up() sees it's gone and starts a new one
            except subprocess.TimeoutExpired:
                pass
        if self._proc:
            for pipe in (self._proc.stdin, self._proc.stdout, self._proc.stderr):
                try:
                    pipe.close()
                except (OSError, ValueError):
                    pass

    def dispose(self) -> None:
        """Done with this session for good (an agent's): stop it and release its tool bridge and config file."""
        self.close()
        atexit.unregister(self.close)
        self.bridge.stop()
        try:
            os.remove(self._mcp_config_path)
        except OSError:
            pass

    def _interrupt(self) -> bool:
        """Stop the turn in progress without ending Claude Code; True once it has stopped."""
        try:
            self._proc.stdin.write(json.dumps({"type": "control_request", "request_id": uuid.uuid4().hex,
                                               "request": {"subtype": "interrupt"}}) + "\n")
            self._proc.stdin.flush()
        except (OSError, AttributeError, ValueError):
            return False
        deadline = time.monotonic() + INTERRUPT_WAIT_S
        while time.monotonic() < deadline:
            try:
                event = self._events.get(timeout=0.2)
            except queue.Empty:
                continue
            if event is None:
                self._proc = None
                return False
            if event.get("type") == "result":
                return True
        return False

    def ask(self, history: list[dict], confirm, acknowledge=None, on_text=None) -> str:
        with self._in_use:
            try:
                return self._ask(history, confirm, acknowledge, on_text)
            finally:
                self._turns += 1
                if self._turns == REFRESH_AFTER_TURNS:
                    timer = threading.Timer(REFRESH_IDLE_S, self._refresh)
                    timer.daemon = True
                    timer.start()

    def _refresh(self) -> None:
        """After many turns, start a fresh Claude Code (it gets the recent conversation replayed) while nobody's
        waiting, so replies don't slow down as the context grows. If a request is running, try again later."""
        if not self._in_use.acquire(blocking=False):
            timer = threading.Timer(REFRESH_IDLE_S, self._refresh)
            timer.daemon = True
            timer.start()
            return
        try:
            if self._proc is not None and self._proc.poll() is None:
                self.close()
                self._start()
        finally:
            self._in_use.release()

    def _ask(self, history: list[dict], confirm, acknowledge=None, on_text=None) -> str:
        self.warm_up()
        self.bridge.confirm = confirm
        self.bridge.acknowledge = acknowledge
        text = _build_prompt(history) if self._fresh else history[-1]["content"]
        if not self._fresh and len(history) >= 2 and str(history[-2].get("content", "")).startswith("[The user interrupted"):
            text = f"{history[-2]['content']}\n\n{text}"  # what was already done before they stopped me
        message = json.dumps({"type": "user", "message": {"role": "user", "content": (
            f"{screen_context()}\n{text}")}}) + "\n"
        try:
            self._proc.stdin.write(message)
            self._proc.stdin.flush()
        except OSError:
            # The process died between turns: start over, replaying history.
            self._start()
            return self._ask(history, confirm, acknowledge, on_text)
        self._fresh = False

        deadline = time.monotonic() + 300
        used, model = {}, ""  # this turn's usage, by API message
        while True:
            if self.cancelled.is_set():
                # Ask Claude Code to stop mid-thought, keeping the process (and the conversation) for the next
                # request. If it doesn't stop quickly, end it; a fresh one starts now (replaying history) so the
                # user's correction isn't slowed down.
                if not self._interrupt():
                    self.close()
                    if self.cancelled is task_cancelled:
                        threading.Thread(target=self.warm_up, daemon=True).start()
                raise TaskInterrupted()
            try:
                event = self._events.get(timeout=0.2)
            except queue.Empty:
                if time.monotonic() > deadline:
                    self.close()
                    raise BackendError("That took too long, sir. Please try again.")
                continue
            if event is None:
                detail = "".join(self._stderr).strip() or "Claude Code exited."
                self._proc = None
                raise _error_for(detail)
            if on_text and event.get("type") == "stream_event":
                inner = event.get("event") or {}
                if inner.get("type") == "message_start":
                    on_text(NEW_MESSAGE)
                elif inner.get("type") == "content_block_delta" and inner["delta"].get("type") == "text_delta":
                    on_text(inner["delta"]["text"])
            kind = event.get("type")
            if kind == "assistant":
                message = event.get("message") or {}
                if message.get("usage"):
                    used[message.get("id") or len(used)] = message["usage"]
                    model = message.get("model") or model
            elif kind == "rate_limit_event":
                usage.record_limit(event)
            if kind == "result":
                usage.record(self.label, used, model)
                if event.get("is_error"):
                    raise _error_for(str(event.get("result") or event.get("subtype")))
                return str(event.get("result", "")).strip()


def _error_for(detail: str) -> BackendError:
    print(f"[claude code] {detail[:500]}")
    lowered = detail.lower()
    if "login" in lowered or "auth" in lowered:
        return BackendError("Claude Code isn't signed in, sir. Run claude in a terminal and use slash login.")
    if "limit" in lowered:
        return BackendError("I've hit the subscription's usage limit, sir. It should reset soon.")
    return BackendError("Claude Code returned an error, sir. The details are on screen.")


def _build_prompt(history: list[dict]) -> str:
    """A fresh Claude Code process knows nothing, so recent turns are replayed as a transcript."""
    recent = history[-SUBSCRIPTION_HISTORY_TURNS:]
    *earlier, latest = recent
    if not earlier:
        return latest["content"]
    transcript = "\n".join(
        f"{'User' if t['role'] == 'user' else 'Jarvis'}: {t['content']}" for t in earlier
    )
    return f"Conversation so far:\n{transcript}\n\nThe user now says:\n{latest['content']}"


_session = None


def _get_session() -> ClaudeCodeSession:
    global _session
    if _session is None:
        _session = ClaudeCodeSession()
    return _session


def run_background(task: str, system_prompt: str, allowed: set[str], cancelled: threading.Event,
                   confirm, log: list[str], label: str = "Background task") -> str:
    """Work on a task in a separate Claude conversation (its own Claude Code process, tools and stop switch),
    so it doesn't hold up the main one. Returns Claude's final report."""
    request = [{"role": "user", "content": task}]
    if BACKEND == "subscription":
        session = ClaudeCodeSession(system_prompt, allowed, cancelled, log, label=label)
        try:
            return session.ask(request, confirm)
        finally:
            session.close()
    system = f"{system_prompt}{facts.prompt_section()}\n\n{screen_context()}"
    return _think_api(request, system, confirm, None, tools=[t for t in TOOLS if t["name"] in allowed],
                      cancelled=cancelled, log=log)


def standby_backend() -> None:
    """Stop Claude Code to free memory; the next request starts it again and replays recent history."""
    if _session is not None:
        _session.close()
    from tools import offline

    offline.unload()


def warm_up() -> None:
    """Start the backend early so the first reply isn't slowed by start-up."""
    if BACKEND == "subscription":
        _get_session().warm_up()
