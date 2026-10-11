"""Run Jarvis in the background with an icon by the clock (this is what starts at Windows sign-in).

    .venv/Scripts/pythonw.exe main.py --tray

Output goes to memory/jarvis.log instead of a console window.
"""
import ctypes
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(ROOT, "memory", "jarvis.log")
MAX_LOG_BYTES = 1_000_000

_mutex = None


def claim_single_instance() -> bool:
    """False if another Jarvis is already listening (two would both answer)."""
    global _mutex
    kernel32 = ctypes.windll.kernel32
    _mutex = kernel32.CreateMutexW(None, False, "Local\\JarvisVoiceAssistant")
    return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


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


def run(run_voice) -> None:
    if not claim_single_instance():
        ctypes.windll.user32.MessageBoxW(None, "Jarvis is already running.", "Jarvis", 0x40)
        return
    _redirect_output()

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
        pystray.MenuItem("Open log", lambda icon, item: os.startfile(LOG_FILE)),
        pystray.MenuItem("Quit Jarvis", lambda icon, item: (icon.stop(), _shutdown())),
    ))
    icon.run_detached()
    try:
        run_voice()
    except Exception:
        traceback.print_exc()
    finally:
        icon.stop()
        _shutdown()
