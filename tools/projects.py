"""Run commands from your other projects (e.g. a job-search bot) by name.

Only commands listed in projects.json can run: Jarvis picks one by name and may fill in the options that
command declares, never arbitrary command lines. See projects.example.json.
"""
import json
import os
import re
import subprocess
import threading

from tools import system

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECTS_FILE = os.path.join(ROOT, "projects.json")
MAX_OUTPUT_CHARS = 4000
SAFE_VALUE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

_last_output: dict[str, str] = {}


def _commands() -> dict[str, dict]:
    """{"job status": {...}, ...} flattened from every project in projects.json."""
    try:
        with open(PROJECTS_FILE, "r", encoding="utf-8") as f:
            projects = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    commands = {}
    for project in projects.values():
        for name, spec in project.get("commands", {}).items():
            commands[name.lower()] = {**spec, "cwd": project["folder"], "project_description": project.get("description", "")}
    return commands


def _find(name: str) -> tuple[str, dict] | None:
    commands = _commands()
    wanted = name.lower().strip()
    if wanted in commands:
        return wanted, commands[wanted]
    partial = [k for k in commands if wanted in k or k in wanted]
    return (partial[0], commands[partial[0]]) if len(partial) == 1 else None


def _argv(spec: dict, options: dict) -> list[str] | str:
    argv = list(spec["run"])
    # Scripts in the project folder get a full path: Windows may not look in the current folder.
    local = os.path.join(spec["cwd"], argv[0])
    if not os.path.isabs(argv[0]) and os.path.isfile(local):
        argv[0] = local
    allowed = spec.get("options", {})
    for key, value in (options or {}).items():
        if key not in allowed:
            return f"'{key}' isn't an option for this command. Options: {', '.join(allowed) or 'none'}."
        value = str(value)
        if not SAFE_VALUE.match(value):
            return f"'{value}' isn't a valid value for {key}."
        argv += [allowed[key], value]
    if argv and argv[0].lower().endswith((".cmd", ".bat")):
        argv = ["cmd", "/c"] + argv  # batch files need cmd.exe
    return argv


def _clean(text: str) -> str:
    text = ANSI.sub("", text).strip()
    if len(text) > MAX_OUTPUT_CHARS:
        text = text[:MAX_OUTPUT_CHARS] + f"\n[... output truncated after {MAX_OUTPUT_CHARS} characters]"
    return text or "(no output)"


def run_project_command(command: str, options: dict | None = None) -> str:
    found = _find(command)
    if not found:
        known = ", ".join(sorted(_commands())) or "none set up (see projects.example.json)"
        return f"No project command called '{command}'. Available: {known}."
    name, spec = found
    argv = _argv(spec, options or {})
    if isinstance(argv, str):
        return argv
    cwd = spec["cwd"]
    mode = spec.get("mode", "wait")

    if mode == "launch":  # long-running apps (a local web UI, ...): start and leave running
        subprocess.Popen(argv, cwd=cwd, creationflags=subprocess.CREATE_NEW_CONSOLE)
        if spec.get("open_url"):
            threading.Timer(4, lambda: system.open_url(spec["open_url"])).start()
        return f"Started {name}." + (f" Opening {spec['open_url']} shortly." if spec.get("open_url") else "")

    def run() -> str:
        try:
            proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=spec.get("timeout", 120), creationflags=subprocess.CREATE_NO_WINDOW)
            output = _clean(proc.stdout + ("\n" + proc.stderr if proc.stderr.strip() else ""))
            if proc.returncode != 0:
                output = f"(exited with code {proc.returncode})\n{output}"
        except subprocess.TimeoutExpired:
            output = f"(stopped after {spec.get('timeout', 120)} seconds)"
        _last_output[name] = output
        return output

    if mode == "background":  # slow jobs: report back when done
        def run_and_announce():
            output = run()
            last = [line for line in output.splitlines() if line.strip()][-1:] or [""]
            system.announce(f"Sir, {name} has finished. {last[0][:150]}")

        threading.Thread(target=run_and_announce, daemon=True).start()
        return f"Started {name} in the background; I'll announce when it finishes. Say nothing more about it now."
    return run()


def get_project_output(command: str) -> str:
    found = _find(command)
    if not found or found[0] not in _last_output:
        return f"No saved output for '{command}' yet."
    return _last_output[found[0]]


def confirmation(name: str, inputs: dict) -> str | None:
    if name != "run_project_command":
        return None
    found = _find(inputs.get("command", ""))
    if found and found[1].get("confirm"):
        return f"Run {found[0]}? {found[1]['confirm']}"
    return None


def _describe() -> str:
    commands = _commands()
    if not commands:
        return "No project commands are set up yet."
    lines = []
    for name, spec in sorted(commands.items()):
        opts = f" Options: {', '.join(spec.get('options', {}))}." if spec.get("options") else ""
        lines.append(f"'{name}': {spec.get('description', '')}{opts}")
    return "Available commands: " + " ".join(lines)


TOOLS = [
    {
        "name": "run_project_command",
        "description": "Run one of the user's own projects' commands by name (configured in projects.json). "
                       + _describe(),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Command name from the list"},
                "options": {"type": "object", "description": "Only that command's listed options, e.g. {\"limit\": 5}"}
            },
            "required": ["command"]
        }
    },
    {
        "name": "get_project_output",
        "description": "Full output from the last run of a project command (e.g. after a background one finished).",
        "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}
    },
]

TOOL_MAP = {"run_project_command": run_project_command, "get_project_output": get_project_output}
