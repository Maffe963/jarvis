import atexit
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
from datetime import datetime

from tools.registry import TOOLS, run_tool

SYSTEM_PROMPT = """You are Jarvis, a highly capable AI assistant in the style of Tony Stark's Jarvis.
You are helpful, precise, and slightly formal but personable. You run on the user's Windows PC and can
control it with tools: launching and closing apps, switching windows, media and volume, opening websites,
system status, power, timers, files, web search, and PowerShell as a last resort.
When asked to do something, just do it with the tools rather than explaining how.
Risky actions are confirmed with the user automatically; if one is declined, acknowledge it and move on.
"Shut down" or "turn off" on its own is ambiguous: ask whether they mean you (Jarvis) or the computer.
To close yourself, use quit_jarvis; only use power for the computer when they clearly mean the computer.
Your replies are spoken aloud: keep them to one or two short sentences, with no markdown, lists, or URLs.
When you use a tool, Jarvis automatically says "Yes sir, <what you're doing>" aloud as it starts (for
example "Yes sir, opening Spotify."), so never repeat or re-confirm the action afterwards. If the action
simply worked and you have nothing new to add, reply with exactly: [done]
If the tool returned information the user wanted, give just that information briefly ("CPU is at twelve
percent."). If it failed or the user declined it, say so plainly. For questions and conversation that
need no tool, just answer naturally, using "sir" now and then."""

# "api" bills an Anthropic API key per request; "subscription" goes through Claude Code (`claude -p`)
# signed in with your Claude plan, and counts against that plan's usage limits.
BACKEND = os.environ.get("JARVIS_BACKEND", "api").strip().lower()
SUBSCRIPTION_HISTORY_TURNS = 12


class BackendError(Exception):
    """A failure with a message fit to say to the user."""


def think(conversation_history: list[dict], confirm=None, acknowledge=None) -> str:
    """Run one turn. `confirm(question) -> bool` is asked before risky tool calls; without it they're refused."""
    if BACKEND == "subscription":
        return _get_session().ask(conversation_history, confirm, acknowledge)
    system = f"{SYSTEM_PROMPT}\n\nCurrent date and time: {datetime.now():%A, %B %d, %Y, %I:%M %p}."
    return _think_api(conversation_history, system, confirm, acknowledge)


# ---------- API backend ----------

_client = None


def _think_api(conversation_history: list[dict], system: str, confirm, acknowledge) -> str:
    global _client
    from anthropic import Anthropic

    if _client is None:
        _client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    messages = conversation_history.copy()

    while True:
        with _client.messages.stream(
            model=os.environ.get("JARVIS_MODEL", "claude-sonnet-5-5"),
            max_tokens=4096,
            system=system,
            tools=TOOLS,
            messages=messages,
        ) as stream:
            response = stream.get_final_message()

        if response.stop_reason == "end_turn":
            text_blocks = [b.text for b in response.content if b.type == "text"]
            return " ".join(text_blocks).strip()

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = run_tool(block.name, block.input, confirm, acknowledge)
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

class ClaudeCodeSession:
    """One long-running `claude -p` that takes a message per turn over stream-json.

    Starting Claude Code costs a few seconds, so it's started once and kept warm; follow-up replies
    then take about a second. Claude Code keeps the conversation itself. If the process dies, the
    next turn starts a fresh one and replays recent history so nothing is forgotten.
    """

    def __init__(self) -> None:
        from tools.bridge import ToolBridge

        claude = shutil.which("claude")
        if not claude:
            raise BackendError("I can't find Claude Code on this PC, sir. Please install it or switch to API mode.")
        self.bridge = ToolBridge()
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
            "--model", os.environ.get("JARVIS_MODEL", "sonnet"),
            "--effort", os.environ.get("JARVIS_EFFORT", "low"),
            "--system-prompt", SYSTEM_PROMPT,
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
        atexit.register(self.close)

    def _start(self) -> None:
        env = os.environ.copy()
        # If an API key is visible, Claude Code bills it instead of using the subscription.
        for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDECODE"):
            env.pop(var, None)
        workdir = os.path.join(tempfile.gettempdir(), "jarvis-claude-code")
        os.makedirs(workdir, exist_ok=True)
        self._proc = subprocess.Popen(
            self._cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", env=env, cwd=workdir, bufsize=1,
        )
        self._events = queue.Queue()
        self._stderr = []
        self._fresh = True
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

    def ask(self, history: list[dict], confirm, acknowledge=None) -> str:
        self.warm_up()
        self.bridge.confirm = confirm
        self.bridge.acknowledge = acknowledge
        text = _build_prompt(history) if self._fresh else history[-1]["content"]
        message = json.dumps({"type": "user", "message": {"role": "user", "content": (
            f"[Current time: {datetime.now():%A, %B %d, %Y, %I:%M %p}]\n{text}")}}) + "\n"
        try:
            self._proc.stdin.write(message)
            self._proc.stdin.flush()
        except OSError:
            # The process died between turns: start over, replaying history.
            self._start()
            return self.ask(history, confirm, acknowledge)
        self._fresh = False

        while True:
            try:
                event = self._events.get(timeout=300)
            except queue.Empty:
                self.close()
                raise BackendError("That took too long, sir. Please try again.")
            if event is None:
                detail = "".join(self._stderr).strip() or "Claude Code exited."
                self._proc = None
                raise _error_for(detail)
            if event.get("type") == "result":
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


def standby_backend() -> None:
    """Stop Claude Code to free memory; the next request starts it again and replays recent history."""
    if _session is not None:
        _session.close()


def warm_up() -> None:
    """Start the backend early so the first reply isn't slowed by start-up."""
    if BACKEND == "subscription":
        _get_session().warm_up()
