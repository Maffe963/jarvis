"""Reminders at a clock time. Saved to memory/reminders.json so they survive restarts."""
import json
import os
import threading
import time
import uuid
from datetime import datetime

from tools import system

REMINDERS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory", "reminders.json")
CHECK_EVERY_S = 10

_lock = threading.Lock()
_started = False


def _load() -> list[dict]:
    try:
        with open(REMINDERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save(reminders: list[dict]) -> None:
    with open(REMINDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(reminders, f, indent=2)


def _spoken(dt: datetime) -> str:
    today = datetime.now().date()
    if dt.date() == today:
        day = "today"
    elif (dt.date() - today).days == 1:
        day = "tomorrow"
    else:
        day = dt.strftime("%A, %B %d").replace(" 0", " ")
    return f"{dt.strftime('%I:%M %p').lstrip('0')} {day}"


def set_reminder(when: str, message: str) -> str:
    try:
        due = datetime.fromisoformat(when)
    except ValueError:
        return f"Couldn't read the time '{when}'. Use local ISO format, e.g. 2026-10-11T15:00."
    if due.tzinfo is not None:
        due = due.astimezone().replace(tzinfo=None)
    if due <= datetime.now():
        return f"{_spoken(due)} has already passed."
    with _lock:
        reminders = _load()
        reminders.append({"id": uuid.uuid4().hex[:6], "due": due.isoformat(timespec="minutes"), "message": message})
        _save(reminders)
    return f"Reminder set for {_spoken(due)}: {message}"


def list_reminders() -> str:
    with _lock:
        reminders = sorted(_load(), key=lambda r: r["due"])
    if not reminders:
        return "No reminders set."
    return "\n".join(f"[{r['id']}] {_spoken(datetime.fromisoformat(r['due']))}: {r['message']}" for r in reminders)


def cancel_reminder(id_or_text: str) -> str:
    wanted = id_or_text.lower().strip()
    with _lock:
        reminders = _load()
        keep = [r for r in reminders if r["id"] != wanted and wanted not in r["message"].lower()]
        removed = [r for r in reminders if r not in keep]
        if not removed:
            return f"No reminder matching '{id_or_text}'."
        _save(keep)
    return "Cancelled: " + "; ".join(r["message"] for r in removed)


def _check() -> None:
    now = datetime.now()
    with _lock:
        reminders = _load()
        due = [r for r in reminders if datetime.fromisoformat(r["due"]) <= now]
        if due:
            _save([r for r in reminders if r not in due])
    for r in due:
        late_by = (now - datetime.fromisoformat(r["due"])).total_seconds()
        when = f" (this was due at {_spoken(datetime.fromisoformat(r['due']))})" if late_by > 120 else ""
        system.announce(f"Sir, a reminder: {r['message']}{when}")


def start() -> None:
    """Begin announcing reminders when they come due, including any missed while Jarvis was off."""
    global _started
    if _started:
        return
    _started = True

    def loop():
        while True:
            try:
                _check()
            except Exception as e:
                print(f"[reminders] {e}")
            time.sleep(CHECK_EVERY_S)

    threading.Thread(target=loop, daemon=True).start()
