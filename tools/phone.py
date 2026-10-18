"""Send files and screenshots from the PC to the user's phone through the Telegram bot ("send me my resume",
"send me a screenshot of my left screen")."""
import io
import os

from tools import files

MAX_BYTES = 50 * 1024 * 1024  # Telegram's limit for bots


def _bot():
    import telegram_bot

    return telegram_bot.active


def send_to_phone(path: str, caption: str = "") -> str:
    bot = _bot()
    if bot is None or bot.chat_id is None:
        return "Phone control isn't on, so I can't send files to the phone (it needs the Telegram bot set up)."
    real = files.resolve(path)
    if not os.path.isfile(real):
        return f"No file at {real}." + (" That's a folder; pick a file in it." if os.path.isdir(real) else "")
    size = os.path.getsize(real)
    if size > MAX_BYTES:
        return f"{os.path.basename(real)} is {size // 2**20} MB; Telegram only lets bots send files up to 50 MB."
    bot.send_file(real, caption)
    return f"Sent {os.path.basename(real)} ({max(1, size // 1024)} KB) to your phone."


ALL_SCREENS = ("", "all", "both", "every", "everything", "all screens", "all monitors", "all of them", "whole",
               "screen", "screens", "monitor", "monitors", "pc", "computer", "desktop")
MAX_PHOTO_SIDE = 4096  # Telegram photos: width + height must stay under 10000


def send_screenshot(monitor: str = "", caption: str = "") -> str:
    """Take a screenshot and send it straight to the phone: no file, nothing to confirm (it's the user's own phone)."""
    bot = _bot()
    if bot is None or bot.chat_id is None:
        return "Phone control isn't on, so I can't send screenshots to the phone (it needs the Telegram bot set up)."
    from PIL import ImageGrab

    from tools import monitors

    wanted = monitor.lower().replace(" screen", "").replace(" monitor", "").strip()
    if wanted in ALL_SCREENS or len(monitors.monitors()) == 1:
        image, what = ImageGrab.grab(all_screens=True), "screens" if len(monitors.monitors()) > 1 else "screen"
    else:
        target = monitors.find_monitor(monitor)
        if not target:
            return f"Unknown monitor '{monitor}'. Monitors:\n{monitors.list_monitors()}"
        image = ImageGrab.grab(bbox=tuple(target["rect"]), all_screens=True)
        what = f"{monitor.strip()} screen"
    image = image.convert("RGB")
    image.thumbnail((MAX_PHOTO_SIDE, MAX_PHOTO_SIDE))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    try:
        bot.api.send_photo(bot.chat_id, buffer.getvalue(), caption or f"Your {what} right now.")
    except Exception as e:
        return f"Couldn't send the screenshot: {e}"
    return f"Sent a screenshot of your {what} to your phone."


def confirmation(name: str, inputs: dict) -> str | None:
    if name != "send_to_phone":
        return None
    real = files.resolve(inputs.get("path", ""))
    if files.is_sensitive(real):
        return f"Send {os.path.basename(real)} to your phone? It may contain passwords or keys."
    if not files.is_allowed(real):
        return f"Send {real} to your phone? It's outside your usual folders."
    return None


TOOLS = [{
    "name": "send_screenshot",
    "description": "Take a screenshot and send it to the user's phone via Telegram, in one step: use this whenever "
                   "they want a screenshot or picture of their screen sent to them. Don't ask which screen and "
                   "don't save a file first: if they didn't name a monitor, leave it empty and all screens are sent.",
    "input_schema": {
        "type": "object",
        "properties": {"monitor": {"type": "string", "description": "e.g. 'left', 'right', 'main'; empty = all"},
                       "caption": {"type": "string"}},
        "required": []
    }
}, {
    "name": "send_to_phone",
    "description": "Send a file from the PC to the user's phone via Telegram (e.g. 'send me my resume'). Use "
                   "find_files first if you don't know the exact path. Files the user sends from the phone are "
                   "saved in Downloads\\From phone.",
    "input_schema": {
        "type": "object",
        "properties": {"path": {"type": "string"}, "caption": {"type": "string"}},
        "required": ["path"]
    }
}]

TOOL_MAP = {"send_to_phone": send_to_phone, "send_screenshot": send_screenshot}
