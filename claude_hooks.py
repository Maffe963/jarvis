"""Hooks for the user's own Claude Code sessions (installed by setup_addons.py into ~/.claude/settings.json).

  python claude_hooks.py notify   Claude Code is waiting on the user (a permission prompt, or idle for a minute):
                                  Jarvis says so aloud and shows it on the dashboard.
  python claude_hooks.py guard    Before a shell command runs: block recursive deletes of a drive, the home folder
                                  or Windows itself. Everything else is left to Claude Code's own permission prompts.

Jarvis's own Claude Code sessions run with hooks off (brain.py), so these only ever see the user's sessions.
"""
import json
import os
import posixpath
import re
import shlex
import sys
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(ROOT, "memory", "hook_token")  # written by the dashboard each time Jarvis starts
PORT = int(os.environ.get("JARVIS_DASHBOARD_PORT", "8765"))

HOME = os.path.expanduser("~").replace("\\", "/").rstrip("/").lower()
SEGMENTS = re.compile(r"&&|\|\||[;|\n&]")  # a shell line's separate commands
HOME_VARS = re.compile(r"^(~|\$home|\$\{home\}|\$userprofile|\$\{userprofile\}|\$env:userprofile|%userprofile%)(?=/|$)")
DELETE_COMMANDS = {"rm", "remove-item", "ri", "rmdir", "rd", "del", "erase"}
SYSTEM_FOLDERS = ("windows", "program files", "program files (x86)", "programdata")
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


def _path(word: str) -> str:
    """An argument as a comparable path: lower case, forward slashes, home variables expanded, Git Bash's /c/ and
    WSL's /mnt/c/ as c:/, no trailing slash or wildcard."""
    path = word.strip("'\"").replace("\\", "/").lower()
    path = HOME_VARS.sub(HOME, path)
    drive = re.match(r"^(?:/mnt)?/([a-z])(?=/|$)", path)
    if drive:
        path = f"{drive.group(1)}:" + path[drive.end():]
    path = re.sub(r"/+", "/", path)
    if ".." in path.split("/"):
        path = posixpath.normpath(path)
    return path.rstrip("*").rstrip("/")


def _protected(path: str) -> bool:
    """A drive or the whole filesystem, Windows or Program Files (or anything in them), the home folder, or a folder
    that contains it."""
    if path in ("", "/") or re.fullmatch(r"[a-z]:", path):
        return True
    if re.match(r"^[a-z]:/(" + "|".join(re.escape(f) for f in SYSTEM_FOLDERS) + r")(/|$)", path):
        return True
    return path == HOME or HOME.startswith(path + "/")


def _recursive_delete(words: list[str]) -> list[str] | None:
    """The paths a recursive delete would remove, or None if this command isn't one."""
    while words and words[0].lower() in ("sudo", "command", "&"):
        words = words[1:]
    if not words or words[0].lower() not in DELETE_COMMANDS:
        return None
    flags = [w.lower() for w in words[1:] if w.startswith("-") or (w.startswith("/") and len(w) == 2)]
    if words[0].lower() == "rm":
        recursive = any(f == "--recursive" or (not f.startswith("--") and "r" in f.lower()) for f in flags)
    else:
        recursive = any("-recurse".startswith(f) and len(f) >= 2 for f in flags) or "/s" in flags
    if not recursive:
        return None
    return [w for w in words[1:] if not w.startswith("-") and not (w.startswith("/") and len(w) == 2 and
                                                                     w[1].isalpha() and words[0].lower() != "rm")
            or w in ("/",)]


def blocked(command: str) -> bool:
    for segment in SEGMENTS.split(command):
        try:
            words = shlex.split(segment, posix=False)
        except ValueError:
            words = segment.split()
        paths = _recursive_delete(words)
        if paths and any(_protected(_path(p)) for p in paths):
            return True
    return False


def guard(event: dict) -> int:
    command = str((event.get("tool_input") or {}).get("command") or "")
    if blocked(command):
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
