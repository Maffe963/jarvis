"""Long-term memory: things you ask Jarvis to remember ("my parking spot is B12"), kept across restarts and
given to Claude with every conversation."""
import json
import os
import threading
from datetime import date

FACTS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory", "facts.json")
MAX_FACTS = 200
_lock = threading.Lock()


def _load() -> list[dict]:
    try:
        with open(FACTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save(facts: list[dict]) -> None:
    with open(FACTS_FILE, "w", encoding="utf-8") as f:
        json.dump(facts[-MAX_FACTS:], f, indent=2)


def prompt_section() -> str:
    """What to add to Claude's instructions so it knows the remembered facts."""
    facts = _load()
    if not facts:
        return ""
    lines = "\n".join(f"- {f['fact']} (noted {f['added']})" for f in facts)
    return f"\n\nThings the user asked you to remember (use them when relevant):\n{lines}"


def remember(fact: str) -> str:
    fact = fact.strip()
    if not fact:
        return "Nothing to remember."
    with _lock:
        facts = _load()
        if any(f["fact"].lower() == fact.lower() for f in facts):
            return "I already have that noted."
        facts.append({"fact": fact, "added": date.today().isoformat()})
        _save(facts)
    return f"Noted: {fact}"


def forget(about: str) -> str:
    wanted = about.lower().strip()
    with _lock:
        facts = _load()
        keep = [f for f in facts if wanted not in f["fact"].lower()]
        removed = [f["fact"] for f in facts if f not in keep]
        if not removed:
            return f"I don't have anything noted about '{about}'."
        _save(keep)
    return "Forgot: " + "; ".join(removed)


def list_facts() -> str:
    facts = _load()
    return "\n".join(f"- {f['fact']}" for f in facts) if facts else "Nothing remembered yet."


TOOLS = [
    {
        "name": "remember",
        "description": "Save a fact or preference long-term when the user asks you to remember something (or "
                       "clearly states a lasting preference). Write it as a short standalone sentence, e.g. "
                       "'The user's parking spot is B12'. It's kept across restarts.",
        "input_schema": {"type": "object", "properties": {"fact": {"type": "string"}}, "required": ["fact"]}
    },
    {
        "name": "forget",
        "description": "Remove remembered facts that mention the given words.",
        "input_schema": {"type": "object", "properties": {"about": {"type": "string"}}, "required": ["about"]}
    },
    {
        "name": "list_facts",
        "description": "Everything the user has asked you to remember.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
]

TOOL_MAP = {"remember": remember, "forget": forget, "list_facts": list_facts}
