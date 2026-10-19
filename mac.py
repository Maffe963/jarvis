"""Mac mode: your Mac stands in for Jarvis while the PC is off.

    python3 mac.py              # run it (Ctrl+C to stop)
    python3 mac.py install      # start it whenever you log in to the Mac
    python3 mac.py uninstall

While the PC is on, this just waits. When the PC has been off for a few minutes (no heartbeat from it in the shared
OneDrive folder, see team/handoff.py and sync_setup.py), it starts Jarvis here in phone-only mode: your Jarvis bot
on Telegram answers, the agent team and its Telegram topics carry on, and the dashboard works, but nothing that
needs the PC's screen, apps or sound. As soon as the PC is back, it stops and the PC takes over again. It messages
your phone each time it takes over or hands back.
"""
import os
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

from team import client, handoff, messages  # noqa: E402

CHECK_S = 15
LOCAL = messages.LOCAL
LOG_FILE = os.path.join(LOCAL, "mac.log")
OFF_FLAG = os.path.join(LOCAL, "mac_off")              # /off on the phone: stop Mac mode
RESTART_NOTE = os.path.join(LOCAL, "restart_note.txt")  # "update yourself": what to tell the phone once back
PLIST = os.path.expanduser("~/Library/LaunchAgents/com.jarvis.macmode.plist")


def log(text: str) -> None:
    print(f"[mac] {time.strftime('%Y-%m-%d %H:%M:%S')} {text}", flush=True)


def tell_phone(text: str) -> None:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(), os.environ.get("TELEGRAM_ALLOWED_USER_ID", "").strip()
    if not (token and chat.isdigit()):
        return
    from telegram_bot import TelegramAPI

    try:
        TelegramAPI(token).call("sendMessage", chat_id=int(chat), text=text)
    except Exception as e:
        log(f"couldn't message the phone: {e}")


def shared() -> bool:
    """Whether memory/team is the folder shared with the PC (a link into OneDrive), so the PC's heartbeat shows up."""
    return os.path.islink(messages.FOLDER) or bool(os.environ.get("JARVIS_SHARED_MEMORY"))


def start_jarvis() -> subprocess.Popen:
    env = dict(os.environ)
    try:
        with open(RESTART_NOTE, "r", encoding="utf-8") as f:
            env["JARVIS_RESTART_NOTE"] = f.read()  # back from "update yourself": the new Jarvis says so
        os.remove(RESTART_NOTE)
    except FileNotFoundError:
        pass
    out = open(os.path.join(LOCAL, "jarvis.log"), "a", encoding="utf-8")
    return subprocess.Popen([sys.executable, os.path.join(ROOT, "main.py"), "--remote"], cwd=ROOT, env=env,
                            stdout=out, stderr=subprocess.STDOUT, start_new_session=True)


def stop_jarvis(proc: subprocess.Popen | None) -> None:
    """Stop Jarvis and the team service here (the team saves its agents, so they carry on on the PC)."""
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(20)
        except subprocess.TimeoutExpired:
            proc.kill()
    running = client.ping()
    if running and not client._stop(running):
        log("the team service didn't stop by itself; ending it")
        try:
            os.kill(int(running.get("pid", 0)), signal.SIGKILL)
        except (OSError, ValueError):
            pass


def run() -> None:
    os.makedirs(LOCAL, exist_ok=True)
    if not shared():
        print("Mac mode needs the team's memory shared with your PC through OneDrive, so it knows when the PC is on.\n"
              "Run this first:  python3 sync_setup.py\n(See \"Using Jarvis from your Mac\" in README.md.)")
        sys.exit(1)
    if os.path.exists(OFF_FLAG):
        os.remove(OFF_FLAG)
    proc, pc_was_on, last_start = None, None, 0.0
    log("Mac mode on: waiting for the PC to be off")

    def quit_(*_):
        log("stopping")
        stop_jarvis(proc)
        sys.exit(0)

    signal.signal(signal.SIGTERM, quit_)
    try:
        while True:
            if os.path.exists(OFF_FLAG):  # /off from the phone
                os.remove(OFF_FLAG)
                log("turned off from the phone")
                stop_jarvis(proc)
                tell_phone("💤 Mac mode is off. Start it again on the Mac with: python3 mac.py")
                return
            pc = handoff.pc_on()
            if pc:
                if proc is not None or pc_was_on is False:
                    log(f"the PC ({pc.get('host')}) is on: handing back")
                    stop_jarvis(proc)
                    proc = None
                    tell_phone("🖥 Your PC is back on, so Jarvis is running there again.")
                pc_was_on = True
            else:
                if proc is None or proc.poll() is not None:
                    if proc is not None:
                        log(f"Jarvis stopped (exit code {proc.returncode}); starting it again")
                    if time.monotonic() - last_start < 30:
                        time.sleep(30)  # it keeps crashing: don't spin (see memory/local/jarvis.log)
                    first = proc is None
                    proc, last_start = start_jarvis(), time.monotonic()
                    if first:
                        log("the PC is off: Jarvis is running on this Mac")
                        tell_phone("💻 Your PC is off, so Jarvis is running on your Mac. Messages, email, the "
                                   "agents and the dashboard all work; controlling the PC waits until it's back.")
                pc_was_on = False
            time.sleep(CHECK_S)
    except KeyboardInterrupt:
        quit_()


PLIST_TEXT = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.jarvis.macmode</string>
  <key>ProgramArguments</key><array><string>{python}</string><string>{script}</string></array>
  <key>WorkingDirectory</key><string>{root}</string>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict></plist>
"""


def install() -> None:
    os.makedirs(os.path.dirname(PLIST), exist_ok=True)
    os.makedirs(LOCAL, exist_ok=True)
    with open(PLIST, "w", encoding="utf-8") as f:
        f.write(PLIST_TEXT.format(python=sys.executable, script=os.path.abspath(__file__), root=ROOT, log=LOG_FILE))
    subprocess.run(["launchctl", "unload", PLIST], capture_output=True)
    subprocess.run(["launchctl", "load", PLIST], check=False)
    print(f"Mac mode now starts when you log in (and is running now). Log: {LOG_FILE}")


def uninstall() -> None:
    subprocess.run(["launchctl", "unload", PLIST], capture_output=True)
    if os.path.exists(PLIST):
        os.remove(PLIST)
    print("Mac mode won't start at login any more.")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    {"install": install, "uninstall": uninstall}.get(command, run)()
