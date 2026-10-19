"""The team's message log: every message between agents, Jarvis and the user, kept in memory/team/messages.jsonl.

It outlives Jarvis and the team service, so Jarvis can read everything the agents said, including while it was off.
A "seen" marker remembers how far Jarvis has caught up, so it can say what it missed when it comes back.
"""
import json
import os
import threading
import time

FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory", "team")
# What belongs to this computer only (the running service's address, its lock and log, coders' working copies).
# memory/team itself can be shared between your PC and Mac through OneDrive (see sync_setup.py); this never is.
LOCAL = os.path.join(os.path.dirname(FOLDER), "local")
LOG_FILE = os.path.join(FOLDER, "messages.jsonl")
SEEN_FILE = os.path.join(FOLDER, "seen.json")
KEEP = 5000  # older messages are dropped when the service starts

_lock = threading.Lock()
_next_id: int | None = None


def _read() -> list[dict]:
    found = []
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    found.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # e.g. half a line from a crash mid-write
    except FileNotFoundError:
        pass
    return found


def _end_last_line() -> None:
    """If a crash left half a line at the end, finish it, so the next message starts on a line of its own."""
    try:
        with open(LOG_FILE, "rb+") as f:
            f.seek(0, os.SEEK_END)
            if f.tell():
                f.seek(-1, os.SEEK_END)
                if f.read(1) != b"\n":
                    f.write(b"\n")
    except FileNotFoundError:
        pass


def trim() -> None:
    """Keep the log to the latest KEEP messages."""
    with _lock:
        everything = _read()
        if len(everything) > KEEP:
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.writelines(json.dumps(m) + "\n" for m in everything[-KEEP:])


def add(sender: str, sender_id: str, to: str, to_id: str, text: str, kind: str = "message", via: str = "") -> dict:
    """Log a message. `kind`: message, question (to the user), update (an agent's reply after a step) or result."""
    global _next_id
    os.makedirs(FOLDER, exist_ok=True)
    with _lock:
        if _next_id is None:
            existing = _read()
            _next_id = existing[-1]["id"] + 1 if existing else 1
            _end_last_line()
        message = {"id": _next_id, "time": time.time(), "from": sender, "from_id": sender_id, "to": to,
                   "to_id": to_id, "text": text, "kind": kind, "via": via}
        _next_id += 1
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(message) + "\n")
    return message


def recent(agent: str = "", limit: int = 30, include_updates: bool = True) -> list[dict]:
    """The latest messages, optionally only those to or from one agent (by name or id)."""
    wanted = agent.lower().strip()
    found = [m for m in _read() if (include_updates or m["kind"] != "update") and (
        not wanted or wanted in (m["from"].lower(), m["to"].lower(), m["from_id"], m["to_id"]))]
    return found[-limit:]


def for_jarvis(after: int) -> list[dict]:
    """Messages to Jarvis or the user (not step-by-step updates) after message `after`."""
    return [m for m in _read() if m["id"] > after and m["to_id"] in ("jarvis", "user") and m["kind"] != "update"]


def seen() -> int:
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return int(json.load(f).get("seen", 0))
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return 0


def mark_seen(message_id: int) -> None:
    with _lock:
        if message_id <= seen():
            return
        os.makedirs(FOLDER, exist_ok=True)
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump({"seen": message_id}, f)


def describe(found: list[dict], width: int = 300) -> str:
    lines = []
    for m in found:
        text = " ".join(m["text"].split())
        text = text if len(text) <= width else text[: width - 1] + "…"
        when = time.strftime("%b %d %I:%M %p", time.localtime(m["time"]))
        via = f" (via {m['via']})" if m["via"] else ""
        label = {"update": f"{m['from']} (update)", "result": f"{m['from']} (final result)",
                 "question": f"{m['from']} → user (question)"}.get(m["kind"], f"{m['from']}{via} → {m['to']}")
        lines.append(f"[{when}] {label}: {text}")
    return "\n".join(lines)
