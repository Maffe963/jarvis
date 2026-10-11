"""Named lists (shopping, to-do, packing, ...) kept in memory/lists.json."""
import json
import os
import threading

LISTS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory", "lists.json")
_lock = threading.Lock()


def _load() -> dict[str, list[str]]:
    try:
        with open(LISTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(lists: dict) -> None:
    with open(LISTS_FILE, "w", encoding="utf-8") as f:
        json.dump(lists, f, indent=2)


def _key(lists: dict, name: str) -> str:
    """Existing list name matching `name` loosely ("shopping" ~ "Shopping list"), else a new clean name."""
    wanted = name.lower().replace(" list", "").strip()
    for existing in lists:
        if existing.lower() == wanted:
            return existing
    return wanted


def add_to_list(list_name: str, items: list[str]) -> str:
    with _lock:
        lists = _load()
        key = _key(lists, list_name)
        current = lists.setdefault(key, [])
        added = [i.strip() for i in items if i.strip() and i.strip().lower() not in (c.lower() for c in current)]
        current.extend(added)
        _save(lists)
    already = len(items) - len(added)
    return (f"Added {', '.join(added)} to the {key} list." if added else "Nothing new to add.") + (
        f" ({already} already on it.)" if added and already else "")


def remove_from_list(list_name: str, items: list[str]) -> str:
    with _lock:
        lists = _load()
        key = _key(lists, list_name)
        if key not in lists:
            return f"There's no {key} list."
        wanted = [i.lower().strip() for i in items]
        removed = [c for c in lists[key] if any(w == c.lower() or w in c.lower() for w in wanted)]
        lists[key] = [c for c in lists[key] if c not in removed]
        _save(lists)
    return f"Removed {', '.join(removed)}." if removed else f"None of those were on the {key} list."


def clear_list(list_name: str) -> str:
    with _lock:
        lists = _load()
        key = _key(lists, list_name)
        if key not in lists:
            return f"There's no {key} list."
        del lists[key]
        _save(lists)
    return f"Deleted the {key} list."


def get_list(list_name: str = "") -> str:
    lists = _load()
    if not list_name.strip():
        return "Lists: " + ", ".join(f"{k} ({len(v)})" for k, v in lists.items()) if lists else "No lists yet."
    key = _key(lists, list_name)
    if not lists.get(key):
        return f"The {key} list is empty."
    return f"{key}: " + "; ".join(lists[key])


TOOLS = [
    {
        "name": "add_to_list",
        "description": "Add items to a named list (shopping, to-do, packing, ...), creating it if needed.",
        "input_schema": {
            "type": "object",
            "properties": {"list_name": {"type": "string"}, "items": {"type": "array", "items": {"type": "string"}}},
            "required": ["list_name", "items"]
        }
    },
    {
        "name": "remove_from_list",
        "description": "Remove items from a list (e.g. when bought or done).",
        "input_schema": {
            "type": "object",
            "properties": {"list_name": {"type": "string"}, "items": {"type": "array", "items": {"type": "string"}}},
            "required": ["list_name", "items"]
        }
    },
    {
        "name": "get_list",
        "description": "Read a list's items, or with no name, the names of all lists.",
        "input_schema": {"type": "object", "properties": {"list_name": {"type": "string"}}, "required": []}
    },
    {
        "name": "clear_list",
        "description": "Delete a whole list. The user will be asked to confirm.",
        "input_schema": {"type": "object", "properties": {"list_name": {"type": "string"}}, "required": ["list_name"]}
    },
]

TOOL_MAP = {"add_to_list": add_to_list, "remove_from_list": remove_from_list, "get_list": get_list,
            "clear_list": clear_list}


def confirmation(name: str, inputs: dict) -> str | None:
    if name == "clear_list":
        return f"Delete the whole {inputs.get('list_name', '')} list?"
    return None
