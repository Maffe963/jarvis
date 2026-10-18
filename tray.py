"""Run Jarvis in the background with an icon by the clock (this is what the Jarvis shortcut starts).

    .venv/Scripts/pythonw.exe main.py --tray

Output goes to memory/jarvis.log instead of a console window. Starting Jarvis while it's already running
turns the running one off, so the shortcut works as an on/off switch.
"""
import ctypes
import os
import sys
import threading
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(ROOT, "memory", "jarvis.log")
MAX_LOG_BYTES = 1_000_000
OFF_SWITCH = "Local\\JarvisTurnOff"

_mutex = None
_icon = None


def claim_single_instance() -> bool:
    """False if another Jarvis is already listening (two would both answer)."""
    global _mutex
    kernel32 = ctypes.windll.kernel32
    _mutex = kernel32.CreateMutexW(None, False, "Local\\JarvisVoiceAssistant")
    return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def turn_off_running_jarvis() -> None:
    """Ask the Jarvis that's already running to shut down (it says 'Jarvis offline' and exits)."""
    import win32event

    win32event.SetEvent(win32event.CreateEvent(None, False, False, OFF_SWITCH))


def listen_for_off_switch() -> None:
    """Shut down when another start of Jarvis asks this one to turn off."""
    import win32event

    event = win32event.CreateEvent(None, False, False, OFF_SWITCH)

    def wait():
        win32event.WaitForSingleObject(event, win32event.INFINITE)
        turn_off()

    threading.Thread(target=wait, daemon=True).start()


def turn_off() -> None:
    from voice import tts

    print("[turned off]")
    tts.stop()
    try:
        tts.speak("Jarvis offline.")
    except Exception:
        pass
    if _icon is not None:
        _icon.stop()
    _shutdown()


def _icon_image(muted: bool):
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((2, 2, 62, 62), fill=(110, 110, 110) if muted else (37, 120, 230))
    draw.text((32, 33), "J", fill="white", anchor="mm", font=ImageFont.load_default(size=40))
    if muted:
        draw.line((12, 12, 52, 52), fill="white", width=6)
    return img


def _redirect_output() -> None:
    if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > MAX_LOG_BYTES:
        os.replace(LOG_FILE, LOG_FILE + ".old")
    log = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = log


def _shutdown() -> None:
    import brain

    if brain._session is not None:
        brain._session.close()
    os._exit(0)  # the voice loop and its threads can't be interrupted politely


def _open_dashboard() -> None:
    import webbrowser

    from dashboard import server

    if server.url():
        webbrowser.open(server.url())


def run(run_voice) -> None:
    global _icon
    if not claim_single_instance():
        turn_off_running_jarvis()  # the shortcut is an on/off switch
        return
    _redirect_output()
    listen_for_off_switch()

    import pystray

    from voice import mic

    def toggle_mute(icon, item):
        if mic.muted.is_set():
            mic.muted.clear()
        else:
            mic.muted.set()
        icon.icon = _icon_image(mic.muted.is_set())
        icon.title = "Jarvis (muted)" if mic.muted.is_set() else "Jarvis"

    icon = pystray.Icon("jarvis", _icon_image(False), "Jarvis", pystray.Menu(
        pystray.MenuItem("Mute microphone", toggle_mute, checked=lambda item: mic.muted.is_set()),
        pystray.MenuItem("Open dashboard", lambda icon, item: _open_dashboard()),
        pystray.MenuItem("Open log", lambda icon, item: os.startfile(LOG_FILE)),
        pystray.MenuItem("Turn Jarvis off", lambda icon, item: turn_off()),
    ))
    _icon = icon
    icon.run_detached()
    try:
        run_voice()
    except Exception:
        traceback.print_exc()
    finally:
        icon.stop()
        _shutdown()
