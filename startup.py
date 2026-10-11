"""Shortcuts for starting Jarvis: at Windows sign-in, and from the Desktop / Start menu.

    .venv/Scripts/python.exe startup.py install      # start when you sign in
    .venv/Scripts/python.exe startup.py uninstall    # stop doing that
    .venv/Scripts/python.exe startup.py shortcuts    # add Jarvis to the Desktop and Start menu
    .venv/Scripts/python.exe startup.py remove-shortcuts
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PYTHONW = os.path.join(ROOT, ".venv", "Scripts", "pythonw.exe")
ICON = os.path.join(ROOT, "assets", "jarvis.ico")


def _folder(name: str) -> str:
    """A Windows special folder ('Desktop', 'Programs', 'Startup'), wherever it really is (e.g. in OneDrive)."""
    return subprocess.run(["powershell", "-NoProfile", "-Command", f"[Environment]::GetFolderPath('{name}')"],
                          capture_output=True, text=True, check=True).stdout.strip()


def _make_icon() -> None:
    if os.path.exists(ICON):
        return
    sys.path.insert(0, ROOT)
    import tray

    os.makedirs(os.path.dirname(ICON), exist_ok=True)
    tray._icon_image(False).resize((256, 256)).save(ICON, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (256, 256)])


def _make_shortcut(path: str) -> None:
    _make_icon()
    # Values go in through environment variables so paths with spaces need no quoting.
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:J_LNK);"
        "$s.TargetPath = $env:J_TARGET; $s.Arguments = 'main.py --tray';"
        "$s.WorkingDirectory = $env:J_DIR; $s.IconLocation = $env:J_ICON;"
        "$s.Description = 'Jarvis: click to turn on, click again to turn off'; $s.Save()"
    )
    env = dict(os.environ, J_LNK=path, J_TARGET=PYTHONW, J_DIR=ROOT, J_ICON=ICON)
    subprocess.run(["powershell", "-NoProfile", "-Command", script], env=env, check=True)


def _remove(path: str) -> bool:
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def install() -> None:
    path = os.path.join(_folder("Startup"), "Jarvis.lnk")
    _make_shortcut(path)
    print(f"Jarvis will start when you sign in.\nShortcut: {path}")


def uninstall() -> None:
    removed = _remove(os.path.join(_folder("Startup"), "Jarvis.lnk"))
    print("Jarvis will no longer start when you sign in." if removed else "Jarvis wasn't set to start at sign-in.")


def shortcuts() -> None:
    for folder in ("Desktop", "Programs"):
        path = os.path.join(_folder(folder), "Jarvis.lnk")
        _make_shortcut(path)
        print(f"Added {path}")


def remove_shortcuts() -> None:
    for folder in ("Desktop", "Programs"):
        path = os.path.join(_folder(folder), "Jarvis.lnk")
        print(f"Removed {path}" if _remove(path) else f"No shortcut at {path}")


if __name__ == "__main__":
    actions = {"install": install, "uninstall": uninstall, "shortcuts": shortcuts, "remove-shortcuts": remove_shortcuts}
    action = actions.get(sys.argv[1] if len(sys.argv) > 1 else "")
    if action:
        action()
    else:
        print(__doc__)
