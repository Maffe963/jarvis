"""Share the agent team's memory between your PC and your Mac through OneDrive (needed for Mac mode, see mac.py).

    PC:   .venv\\Scripts\\python.exe sync_setup.py      (turn Jarvis off first)
    Mac:  python3 sync_setup.py
    Either, with a folder of your choice:  sync_setup.py "path/to/OneDrive/Jarvis"

memory/team (the agents, their notebooks, the job tracker, trips, holdings, every message) moves into
OneDrive/Jarvis/team and memory/team becomes a link to it, so both computers read and write the same files. Run it on
the PC first: its memory is moved into OneDrive. On the Mac it then links to what the PC put there (anything the Mac
had of its own is kept aside in memory/team-before-sync). Things that belong to one computer (the running service,
coders' working copies) stay in memory/local and are never shared.
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from team import client, messages  # noqa: E402

TEAM = messages.FOLDER
MACHINE_ONLY = ("service.json", "service.lock", "service.log", "service.log.old")  # from before memory/local


def onedrive() -> str | None:
    if os.name == "nt":
        for var in ("OneDriveConsumer", "OneDrive", "OneDriveCommercial"):
            if os.environ.get(var) and os.path.isdir(os.environ[var]):
                return os.environ[var]
        return None
    found = sorted(glob.glob(os.path.expanduser("~/Library/CloudStorage/OneDrive*")))
    personal = [f for f in found if f.endswith("OneDrive-Personal")]
    return (personal or found or [None])[0]


def is_link(path: str) -> bool:
    return os.path.islink(path) or (hasattr(os.path, "isjunction") and os.path.isjunction(path))


def link(target: str) -> None:
    if os.name == "nt":  # a junction: works without admin rights or Developer Mode
        subprocess.run(["cmd", "/c", "mklink", "/J", TEAM, target], check=True, capture_output=True)
    else:
        os.symlink(target, TEAM)


def move_worktrees() -> None:
    """Coders' working copies are big and belong to this computer: keep them out of OneDrive."""
    old = os.path.join(TEAM, "worktrees")
    if not os.path.isdir(old) or not os.listdir(old):
        return
    new = os.path.join(messages.LOCAL, "worktrees")
    os.makedirs(messages.LOCAL, exist_ok=True)
    for name in os.listdir(old):
        shutil.move(os.path.join(old, name), os.path.join(new, name))
        subprocess.run(["git", "worktree", "repair"], cwd=os.path.join(new, name), capture_output=True)
    os.rmdir(old)
    state = os.path.join(TEAM, "agents.json")
    if os.path.exists(state):  # the coders' saved workspaces point at the new place
        with open(state, "r", encoding="utf-8") as f:
            text = f.read()
        text = text.replace(json.dumps(old)[1:-1], json.dumps(new)[1:-1])  # paths as they're written in JSON
        with open(state, "w", encoding="utf-8") as f:
            f.write(text)


def main() -> None:
    if is_link(TEAM):
        print(f"Already shared: memory/team points to {os.path.realpath(TEAM)}")
        return
    base = sys.argv[1] if len(sys.argv) > 1 else None
    if base is None:
        drive = onedrive()
        if drive is None:
            print("Couldn't find your OneDrive folder. Install OneDrive and sign in, or give the folder:\n"
                  '  sync_setup.py "path/to/OneDrive/Jarvis"')
            sys.exit(1)
        base = os.path.join(drive, "Jarvis")
    target = os.path.join(base, "team")

    running = client.ping()
    if running:
        print("Stopping the agent team for a moment (its agents carry on afterwards)...")
        if not client._stop(running):
            print("The team service didn't stop. Turn Jarvis off and try again.")
            sys.exit(1)

    if os.path.isdir(TEAM):
        move_worktrees()
        for name in MACHINE_ONLY:
            path = os.path.join(TEAM, name)
            if os.path.exists(path):
                os.makedirs(messages.LOCAL, exist_ok=True)
                shutil.move(path, os.path.join(messages.LOCAL, name if name != "service.json" else "service.old.json"))
    has_local = os.path.isdir(TEAM) and bool(os.listdir(TEAM))
    has_shared = os.path.isdir(target) and bool(os.listdir(target))

    try:
        if has_local and not has_shared:  # the first computer: its memory moves into OneDrive
            os.makedirs(base, exist_ok=True)
            shutil.move(TEAM, target)
            print(f"Moved the team's memory into {target}")
        elif has_local:  # the second computer: use what's shared, keep this one's aside
            aside = os.path.join(os.path.dirname(TEAM), f"team-before-sync-{time.strftime('%Y%m%d-%H%M%S')}")
            os.rename(TEAM, aside)
            print(f"Using the shared memory in {target}. This computer's own copy is kept in {aside}")
        else:
            os.makedirs(target, exist_ok=True)
            if os.path.isdir(TEAM):
                os.rmdir(TEAM)
        link(target)
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"Something went wrong: {e}\nNothing was deleted; check memory/ and {base}.")
        sys.exit(1)
    print("Done: memory/team is now shared through OneDrive.")
    if os.name != "nt":
        print("In Finder, right-click the OneDrive 'Jarvis' folder and choose 'Always Keep on This Device'.\n"
              "Next: python3 mac.py install")
    else:
        print("Start Jarvis again. In File Explorer, right-click OneDrive > Jarvis > 'Always keep on this device'.")


if __name__ == "__main__":
    main()
