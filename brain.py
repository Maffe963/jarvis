import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime

from tools.registry import TOOLS, run_tool

SYSTEM_PROMPT = """You are Jarvis, a highly capable AI assistant in the style of Tony Stark's Jarvis.
You are helpful, precise, and slightly formal but personable. You run on the user's Windows PC and can
control it with tools: launching and closing apps, switching windows, media and volume, opening websites,
system status, power, timers, files, web search, and PowerShell as a last resort.
When asked to do something, just do it with the tools rather than explaining how.
Risky actions are confirmed with the user automatically; if one is declined, acknowledge it and move on.
Your replies are spoken aloud: keep them to one or two short sentences, with no markdown, lists, or URLs.
Address the user as "sir" occasionally for character."""

# "api" bills an Anthropic API key per request; "subscription" goes through Claude Code (`claude -p`)
# signed in with your Claude plan, and counts against that plan's usage limits.
BACKEND = os.environ.get("JARVIS_BACKEND", "api").strip().lower()
SUBSCRIPTION_HISTORY_TURNS = 12


class BackendError(Exception):
    """A failure with a message fit to say to the user."""


def think(conversation_history: list[dict], confirm=None) -> str:
    """Run one turn. `confirm(question) -> bool` is asked before risky tool calls; without it they're refused."""
    system = f"{SYSTEM_PROMPT}\n\nCurrent date and time: {datetime.now():%A, %B %d, %Y, %I:%M %p}."
    if BACKEND == "subscription":
        return _think_claude_code(conversation_history, system, confirm)
    return _think_api(conversation_history, system, confirm)


# ---------- API backend ----------

_client = None


def _think_api(conversation_history: list[dict], system: str, confirm) -> str:
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
                    result = run_tool(block.name, block.input, confirm)
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

_bridge = None
_mcp_config_path = None


def _start_bridge() -> None:
    global _bridge, _mcp_config_path
    from tools.bridge import ToolBridge

    _bridge = ToolBridge()
    config = {"mcpServers": {"jarvis": {
        "command": sys.executable,
        "args": [os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_server.py")],
        "env": {"JARVIS_BRIDGE_URL": _bridge.url, "JARVIS_BRIDGE_TOKEN": _bridge.token},
    }}}
    fd, _mcp_config_path = tempfile.mkstemp(prefix="jarvis-mcp-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(config, f)


def _build_prompt(history: list[dict]) -> str:
    """`claude -p` is stateless here (no saved sessions), so recent turns are replayed as a transcript."""
    recent = history[-SUBSCRIPTION_HISTORY_TURNS:]
    *earlier, latest = recent
    if not earlier:
        return latest["content"]
    transcript = "\n".join(
        f"{'User' if t['role'] == 'user' else 'Jarvis'}: {t['content']}" for t in earlier
    )
    return f"Conversation so far:\n{transcript}\n\nThe user now says:\n{latest['content']}"


def _think_claude_code(conversation_history: list[dict], system: str, confirm) -> str:
    claude = shutil.which("claude")
    if not claude:
        raise BackendError("I can't find Claude Code on this PC, sir. Please install it or switch to API mode.")
    if _bridge is None:
        _start_bridge()
    _bridge.confirm = confirm

    env = os.environ.copy()
    # If an API key is visible, Claude Code bills it instead of using the subscription.
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDECODE"):
        env.pop(var, None)

    cmd = [
        claude, "-p",
        "--output-format", "json",
        "--model", os.environ.get("JARVIS_MODEL", "sonnet"),
        "--effort", os.environ.get("JARVIS_EFFORT", "low"),
        "--system-prompt", system,
        "--tools", "",                      # no built-in Claude Code tools (Bash, Edit, ...)
        "--strict-mcp-config", "--mcp-config", _mcp_config_path,
        "--allowedTools", "mcp__jarvis",    # only Jarvis's own tools
        "--permission-mode", "dontAsk",
        "--no-session-persistence",
        "--disable-slash-commands",
    ]
    workdir = os.path.join(tempfile.gettempdir(), "jarvis-claude-code")
    os.makedirs(workdir, exist_ok=True)
    try:
        proc = subprocess.run(
            cmd, input=_build_prompt(conversation_history), capture_output=True,
            text=True, encoding="utf-8", errors="replace", env=env, cwd=workdir, timeout=300,
        )
    except subprocess.TimeoutExpired:
        raise BackendError("That took too long, sir. Please try again.")

    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        result = {"is_error": True, "result": (proc.stdout + proc.stderr).strip()}

    if proc.returncode != 0 or result.get("is_error"):
        detail = str(result.get("result") or proc.stderr).strip()
        print(f"[claude code] {detail[:500]}")
        lowered = detail.lower()
        if "login" in lowered or "auth" in lowered:
            raise BackendError("Claude Code isn't signed in, sir. Run claude in a terminal and use slash login.")
        if "limit" in lowered:
            raise BackendError("I've hit the subscription's usage limit, sir. It should reset soon.")
        raise BackendError("Claude Code returned an error, sir. The details are on screen.")
    return str(result.get("result", "")).strip()
