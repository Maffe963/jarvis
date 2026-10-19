"""Hooks for the user's own Claude Code sessions (installed by setup_addons.py into ~/.claude/settings.json).

  python claude_hooks.py notify   Claude Code is waiting on the user (a permission prompt, or idle for a minute):
                                  Jarvis says so aloud and shows it on the dashboard.
  python claude_hooks.py guard    Before a shell command runs: block recursive deletes of a drive, the home folder
                                  or Windows itself. Everything else is left to Claude Code's own permission prompts.

Jarvis's own Claude Code sessions run with hooks off (brain.py), so these only ever see the user's sessions.
"""
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(ROOT, "memory", "hook_token")  # written by the dashboard each time Jarvis starts
PORT = int(os.environ.get("JARVIS_DASHBOARD_PORT", "8765"))

HOME = os.path.expanduser("~").replace("\\", "/").rstrip("/").lower()
RECURSIVE_DELETE = re.compile(r"\brm\s+(-\w*r\w*|--recursive)\b|\bRemove-Item\b[^|;&]*-Recurse\b|\b(rd|rmdir)\s+/s\b",
                              re.IGNORECASE)
PROTECTED = re.compile(r"""(?:^|[\s'"=])(?:/|~/?|\$HOME/?|\$env:USERPROFILE\\?|%USERPROFILE%\\?|[a-z]:[\\/]?"""
                       r"""|[a-z]:[\\/](?:windows|program files(?: \(x86\))?|users)[\\/]?)(?:\*)?(?=$|[\s'";|&])""",
                       re.IGNORECASE)


def notify(event: dict) -> None:
    if os.environ.get("JARVIS_SESSION"):  # one of Jarvis's own sessions (if hooks were ever on there)
        return
    message = str(event.get("message") or "Claude Code needs you").strip()
    folder = re.split(r"[\\/]", str(event.get("cwd") or "").rstrip("\\/"))[-1]
    text = f"Claude Code{f' in {folder}' if folder else ''}: {message}"
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            token = f.read().strip()
        request = urllib.request.Request(f"http://127.0.0.1:{PORT}/announce", json.dumps({"text": text}).encode(),
                                         {"Content-Type": "application/json", "X-Jarvis-Token": token})
        urllib.request.urlopen(request, timeout=3).close()
    except Exception:
        pass  # Jarvis isn't running: nothing to tell


def _protected(command: str) -> bool:
    if PROTECTED.search(command):
        return True
    for word in re.findall(r"""[^\s'"]+|'[^']*'|"[^"]*\"""", command):
        path = word.strip("'\"").replace("\\", "/").rstrip("/*").rstrip("/").lower()
        if path and path == HOME:
            return True
    return False


def guard(event: dict) -> int:
    command = str((event.get("tool_input") or {}).get("command") or "")
    if RECURSIVE_DELETE.search(command) and _protected(command):
        print("Blocked by Jarvis's safety hook: this deletes a whole drive, your home folder or Windows. "
              "If that's really intended, run it yourself.", file=sys.stderr)
        return 2  # Claude Code blocks the command and shows Claude this message
    return 0


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        event = {}
    what = sys.argv[1] if len(sys.argv) > 1 else ""
    if what == "notify":
        notify(event)
    elif what == "guard":
        return guard(event)
    return 0


if __name__ == "__main__":
    sys.exit(main())
