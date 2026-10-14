"""Send files from the PC to the user's phone through the Telegram bot ("send me my resume")."""
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

TOOL_MAP = {"send_to_phone": send_to_phone}
