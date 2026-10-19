"""Long-term memory: things you ask Jarvis to remember ("my parking spot is B12"), kept across restarts and
given to Claude with every conversation."""
import json
import os
import threading
from datetime import date

FACTS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory", "facts.json")
MAX_FACTS = 400  # a few KB of text; the oldest go first past this
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
    return f"\n\nWhat you know about the user (from earlier conversations; use it when relevant):\n{lines}"


def remember(fact: str, replaces: str = "") -> str:
    """Save a fact. `replaces`: words from an older fact this one updates (e.g. a new address), which is dropped."""
    fact = fact.strip()
    if not fact:
        return "Nothing to remember."
    with _lock:
        facts = _load()
        old = replaces.lower().strip()
        if old:
            facts = [f for f in facts if old not in f["fact"].lower()]
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
        "description": "Save something about the user to remember long-term, kept across restarts and used in every "
                       "conversation (voice, phone, dashboard) and by their agents. Use it when they ask, and also on "
                       "your own whenever they mention something useful later: people in their life (names, "
                       "relationships, birthdays), preferences, routines, goals and plans, school and work, places, "
                       "accounts and cards they use (never passwords, codes or full card/ID numbers), deadlines. Not "
                       "for one-off chatter. Write a short standalone sentence, e.g. 'The user's sister is Linh; her "
                       "birthday is March 3'. When it updates something already noted, pass `replaces`.",
        "input_schema": {"type": "object", "properties": {
            "fact": {"type": "string"},
            "replaces": {"type": "string", "description": "Words from the older fact this replaces (e.g. 'parking spot')"}}, "required": ["fact"]}
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
