import json
import os
from datetime import datetime

HISTORY_FILE = os.path.join(os.path.dirname(__file__), "history.json")
MAX_HISTORY = 20


def load_history() -> list[dict]:
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            history = json.load(f)
        if isinstance(history, list):
            return history
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    # Unreadable: keep it aside for inspection and start fresh rather than refusing to boot.
    backup = HISTORY_FILE + f".corrupt-{datetime.now():%Y%m%d-%H%M%S}"
    os.replace(HISTORY_FILE, backup)
    print(f"[memory] history was unreadable; moved it to {backup}")
    return []


def save_history(history: list[dict]) -> None:
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)


def add_turn(history: list[dict], role: str, content: str) -> list[dict]:
    history.append({"role": role, "content": content})
    if len(history) > MAX_HISTORY * 2:
        history = history[-(MAX_HISTORY * 2):]
    return history


def clear_history() -> None:
    if os.path.exists(HISTORY_FILE):
        os.remove(HISTORY_FILE)
