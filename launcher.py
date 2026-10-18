"""Turn Jarvis on from your phone: a tiny Telegram listener for while Jarvis is off.

    .venv/Scripts/pythonw.exe launcher.py

It never touches the microphone, speech or Claude; it only checks Telegram for your messages, and only
while Jarvis is off (when Jarvis is on, Jarvis's own bot answers instead). Send /on to start Jarvis, or
/on phone to start it for phone control only, with the mic off.
"""
import ctypes
import json
import os
import subprocess
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
PYTHONW = os.path.join(ROOT, ".venv", "Scripts", "pythonw.exe")
JARVIS_MUTEX = "Local\\JarvisVoiceAssistant"   # held by a running Jarvis (see tray.py)
LAUNCHER_MUTEX = "Local\\JarvisLauncher"
POLL_S = 10  # short polls, so Jarvis's own bot can take over quickly once it starts
HELP = ("Jarvis is off right now.\n\n/on - turn Jarvis on (voice and phone)\n"
        "/on phone - phone control only, microphone stays off\n/restart - same as /on while Jarvis is off")


def _env() -> dict:
    from dotenv import dotenv_values

    return dotenv_values(os.path.join(ROOT, ".env"))


def jarvis_running() -> bool:
    handle = ctypes.windll.kernel32.OpenMutexW(0x00100000, False, JARVIS_MUTEX)  # SYNCHRONIZE
    if handle:
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    return False


class Telegram:
    def __init__(self, token: str) -> None:
        self.base = f"https://api.telegram.org/bot{token}"

    def call(self, method: str, **params):
        req = urllib.request.Request(f"{self.base}/{method}", data=json.dumps(params).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=params.get("timeout", 0) + 20) as resp:
            return json.loads(resp.read())["result"]


def start_jarvis(phone_only: bool) -> None:
    args = [PYTHONW, "main.py", "--tray"] + (["--remote"] if phone_only else [])
    env = dict(os.environ, JARVIS_STARTED_FROM_PHONE="1")
    subprocess.Popen(args, cwd=ROOT, env=env, creationflags=subprocess.DETACHED_PROCESS)


def main() -> None:
    _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, LAUNCHER_MUTEX)  # held while this runs
    if ctypes.windll.kernel32.GetLastError() == 183:
        return  # already running
    settings = _env()
    token, allowed = settings.get("TELEGRAM_BOT_TOKEN") or "", settings.get("TELEGRAM_ALLOWED_USER_ID") or ""
    if not token or not allowed.isdigit():
        print("Set TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_USER_ID in .env first.")
        return
    me, api, offset, backoff = int(allowed), Telegram(token), None, 1
    try:
        from telegram_bot import register_commands

        register_commands(api.call)  # the list Telegram shows when you type "/"
    except Exception:
        pass
    while True:
        if jarvis_running():
            offset = None  # Jarvis's bot is reading messages now; pick up fresh when it turns off
            time.sleep(3)
            continue
        try:
            updates = api.call("getUpdates", timeout=POLL_S, offset=offset, allowed_updates=["message"])
            backoff = 1
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
            continue
        for update in updates:
            offset = update["update_id"] + 1
            message = update.get("message") or {}
            if (message.get("from") or {}).get("id") != me:
                continue  # only you
            text = (message.get("text") or "").strip().lower()
            if text.startswith(("/on", "/restart")):
                phone_only = "phone" in text
                api.call("getUpdates", offset=offset, timeout=0)  # mark it handled before Jarvis's bot starts
                api.call("sendMessage", chat_id=me, text="Starting Jarvis" + (" (phone only, mic off)" if phone_only else "")
                         + "... I'll message you when it's ready.")
                start_jarvis(phone_only)
                for _ in range(60):  # wait for it to come up, then step aside
                    if jarvis_running():
                        break
                    time.sleep(1)
                else:
                    api.call("sendMessage", chat_id=me, text="Jarvis didn't start. Check memory/jarvis.log on the PC.")
                offset = None
                break
            api.call("sendMessage", chat_id=me, text=HELP)


if __name__ == "__main__":
    main()
