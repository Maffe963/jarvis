"""Jarvis updates itself from GitHub ("update yourself"), then restarts.

Only fast-forward updates are applied, and never over unsaved local code changes.
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
PYTHONW = os.path.join(ROOT, ".venv", "Scripts", "pythonw.exe")


# Run in a separate process: waits for Jarvis (argv[1] = its PID) to exit, then starts argv[2:].
RESTART_SCRIPT = (
    "import ctypes, subprocess, sys, time\n"
    "pid = int(sys.argv[1])\n"
    "handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)\n"
    "if handle:\n"
    "    ctypes.windll.kernel32.WaitForSingleObject(handle, 60000)\n"
    "time.sleep(1)\n"
    "subprocess.Popen(sys.argv[2:])\n"
)


def _git(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    git = shutil.which("git")
    if not git:
        raise RuntimeError("git isn't installed or isn't on PATH.")
    return subprocess.run([git, *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout,
                          creationflags=subprocess.CREATE_NO_WINDOW)


def _pending() -> tuple[list[str], str | None]:
    """(new commits on GitHub, a reason updating isn't possible)."""
    fetch = _git("fetch", "--quiet", "origin")
    if fetch.returncode != 0:
        return [], f"Couldn't reach GitHub: {fetch.stderr.strip()[:200]}"
    branch = _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    new = _git("log", "--format=%s", f"HEAD..origin/{branch}").stdout.splitlines()
    if _git("status", "--porcelain", "--untracked-files=no").stdout.strip():
        return new, "There are code changes on this PC that aren't committed, so I won't overwrite them."
    if _git("rev-list", "--count", f"origin/{branch}..HEAD").stdout.strip() not in ("", "0"):
        return new, "This PC has commits that aren't on GitHub yet; push them first, then update."
    return new, None


def check_for_updates() -> str:
    try:
        new, problem = _pending()
    except Exception as e:
        return f"Couldn't check for updates: {e}"
    if not new:
        return "Jarvis is up to date." + (f" (Note: {problem})" if problem and "reach" not in problem else "")
    listing = "\n".join(f"- {line}" for line in new[:15]) + (f"\n...and {len(new) - 15} more" if len(new) > 15 else "")
    return (f"{len(new)} update(s) available:\n{listing}"
            + (f"\nBut: {problem}" if problem else "\nSay 'update yourself' to install them."))


def update_jarvis() -> str:
    try:
        new, problem = _pending()
    except Exception as e:
        return f"Couldn't update: {e}"
    if problem:
        return problem
    if not new:
        return "Already up to date; nothing to install."
    before = _git("rev-parse", "HEAD").stdout.strip()
    pull = _git("pull", "--ff-only", "--quiet", timeout=120)
    if pull.returncode != 0:
        return f"The update failed, nothing changed: {pull.stderr.strip()[:300]}"
    changed = _git("diff", "--name-only", before, "HEAD").stdout.split()
    note = ""
    if "requirements.txt" in changed:
        pip = subprocess.run([PYTHON, "-m", "pip", "install", "-q", "-r", "requirements.txt"], cwd=ROOT,
                             capture_output=True, text=True, timeout=900, creationflags=subprocess.CREATE_NO_WINDOW)
        note = " New packages installed." if pip.returncode == 0 else f" (Installing new packages failed: {pip.stderr[-200:]})"
    _restart_after_reply()
    return f"Installed {len(new)} update(s).{note} Restarting now; I'll be back in a few seconds."


def _restart_after_reply() -> None:
    """Start a fresh Jarvis once this one has exited (a new copy can't start while this one runs)."""
    args = [PYTHONW if "--tray" in sys.argv else PYTHON, os.path.join(ROOT, "main.py"), *sys.argv[1:]]
    subprocess.Popen([PYTHONW, "-c", RESTART_SCRIPT, str(os.getpid()), *args], cwd=ROOT,
                     creationflags=subprocess.DETACHED_PROCESS)
    from tools import system

    system.quit_requested.set()  # main.py exits after this reply is spoken / sent


def confirmation(name: str, inputs: dict) -> str | None:
    if name == "update_jarvis":
        return "Update Jarvis from GitHub and restart?"
    return None


TOOLS = [
    {
        "name": "check_for_updates",
        "description": "See whether there's a newer version of Jarvis's own code on GitHub, and what changed.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "update_jarvis",
        "description": "Install the newest version of Jarvis from GitHub and restart (the user confirms first). "
                       "Use when they say 'update yourself'. Check for updates first if unsure.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
]

TOOL_MAP = {"check_for_updates": check_for_updates, "update_jarvis": update_jarvis}
