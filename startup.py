"""Start Jarvis automatically when you sign in to Windows, or stop doing so.

    .venv/Scripts/python.exe startup.py install
    .venv/Scripts/python.exe startup.py uninstall
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SHORTCUT = os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs\Startup\Jarvis.lnk")
PYTHONW = os.path.join(ROOT, ".venv", "Scripts", "pythonw.exe")


def install() -> None:
    # Values go in through environment variables so paths with spaces need no quoting.
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:J_LNK);"
        "$s.TargetPath = $env:J_TARGET; $s.Arguments = 'main.py --tray';"
        "$s.WorkingDirectory = $env:J_DIR; $s.Description = 'Jarvis voice assistant'; $s.Save()"
    )
    env = dict(os.environ, J_LNK=SHORTCUT, J_TARGET=PYTHONW, J_DIR=ROOT)
    subprocess.run(["powershell", "-NoProfile", "-Command", script], env=env, check=True)
    print(f"Jarvis will start when you sign in.\nShortcut: {SHORTCUT}")


def uninstall() -> None:
    if os.path.exists(SHORTCUT):
        os.remove(SHORTCUT)
        print("Jarvis will no longer start when you sign in.")
    else:
        print("Jarvis wasn't set to start at sign-in.")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action == "install":
        install()
    elif action == "uninstall":
        uninstall()
    else:
        print(__doc__)
