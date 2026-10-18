"""Every tool Jarvis can use, shared by both backends (direct API and Claude Code subscription)."""
import threading

from tools import (background, browser, desktop, facts, finder, gcal, lists, messaging, monitors, phone, projects, reminders,
                   spotify, ui, updater, voice_settings, watchers, weather)
from tools.files import (grant_folder_access, list_files, list_folder_access, read_file,
                         revoke_folder_access, write_file)
from tools.search import web_search
from tools.system import SYSTEM_TOOL_MAP, SYSTEM_TOOLS, needs_confirmation
from team import client as team_client

TOOLS = [
    {
        "name": "web_search",
        "description": "Search the web for current information on any topic.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "read_file",
        "description": "Read the contents of a file from the filesystem.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The file path to read"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "write_file",
        "description": "Write content to a file on the filesystem.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The file path to write"},
                "content": {"type": "string", "description": "The content to write"}
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "list_files",
        "description": "List files in a directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "directory": {"type": "string", "description": "Directory path to list (defaults to current directory)"}
            },
            "required": []
        }
    },
    {
        "name": "grant_folder_access",
        "description": "Let Jarvis read and change files in a folder (or a whole drive like 'E:\\') from now on "
                       "without asking each time. Only when the user explicitly says to; they confirm it.",
        "input_schema": {"type": "object", "properties": {"folder": {"type": "string"}}, "required": ["folder"]}
    },
    {
        "name": "revoke_folder_access",
        "description": "Go back to asking before using files in a folder the user granted earlier.",
        "input_schema": {"type": "object", "properties": {"folder": {"type": "string"}}, "required": ["folder"]}
    },
    {
        "name": "list_folder_access",
        "description": "Which folders Jarvis can use without asking.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "set_reminder",
        "description": "Remind the user at a specific clock time (Jarvis says it aloud then). Use for "
                       "'remind me at 3pm...' or 'tomorrow morning...'. For 'in N minutes' use set_timer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "when": {"type": "string", "description": "Local date and time, ISO 8601, e.g. 2026-10-11T15:00"},
                "message": {"type": "string", "description": "What to remind them about, e.g. 'call mom'"}
            },
            "required": ["when", "message"]
        }
    },
    {
        "name": "list_reminders",
        "description": "List upcoming reminders.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "cancel_reminder",
        "description": "Cancel reminders by id (from list_reminders) or by words in the message.",
        "input_schema": {
            "type": "object",
            "properties": {"id_or_text": {"type": "string"}},
            "required": ["id_or_text"]
        }
    }
] + finder.TOOLS + messaging.TOOLS + weather.TOOLS + lists.TOOLS + desktop.TOOLS + projects.TOOLS + voice_settings.TOOLS + monitors.TOOLS + ui.TOOLS + facts.TOOLS + phone.TOOLS + spotify.TOOLS + gcal.TOOLS + updater.TOOLS + watchers.TOOLS + background.TOOLS + browser.TOOLS + team_client.TOOLS + SYSTEM_TOOLS

TOOL_MAP = {
    "web_search": web_search,
    "read_file": read_file,
    "write_file": write_file,
    "list_files": list_files,
    "grant_folder_access": grant_folder_access,
    "revoke_folder_access": revoke_folder_access,
    "list_folder_access": list_folder_access,
    "set_reminder": reminders.set_reminder,
    "list_reminders": reminders.list_reminders,
    "cancel_reminder": reminders.cancel_reminder,
    **finder.TOOL_MAP,
    **weather.TOOL_MAP,
    **lists.TOOL_MAP,
    **desktop.TOOL_MAP,
    **projects.TOOL_MAP,
    **voice_settings.TOOL_MAP,
    **monitors.TOOL_MAP,
    **ui.TOOL_MAP,
    **facts.TOOL_MAP,
    **phone.TOOL_MAP,
    **spotify.TOOL_MAP,
    **gcal.TOOL_MAP,
    **updater.TOOL_MAP,
    **watchers.TOOL_MAP,
    **background.TOOL_MAP,
    **browser.TOOL_MAP,
    **team_client.TOOL_MAP,
    **messaging.TOOL_MAP,
    **SYSTEM_TOOL_MAP,
}


def _duration(seconds) -> str:
    seconds = int(seconds or 0)
    for size, unit in ((3600, "hour"), (60, "minute"), (1, "second")):
        if seconds >= size and seconds % size == 0 or size == 1:
            n = seconds // size
            return f"{n} {unit}{'' if n == 1 else 's'}"


def ack_phrase(name: str, inputs: dict) -> str:
    """What Jarvis says it's doing when a tool starts, e.g. "opening Spotify"."""
    action = inputs.get("action")
    phrases = {
        "open_app": lambda: f"opening {inputs.get('name', 'that')}",
        "close_app": lambda: f"closing {inputs.get('name', 'that')}",
        "focus_window": lambda: f"switching to {inputs.get('title', 'that window')}",
        "show_desktop": lambda: "showing the desktop",
        "open_url": lambda: "opening that now",
        "media_control": lambda: {"play_pause": "toggling playback", "next": "skipping ahead",
                                  "previous": "going back a track", "stop": "stopping playback"}.get(action, "on it"),
        "volume": lambda: {"set": f"setting the volume to {inputs.get('level')} percent",
                           "up": "turning it up", "down": "turning it down",
                           "mute": "muting", "unmute": "unmuting"}.get(action, "checking now"),
        "set_timer": lambda: f"setting a timer for {_duration(inputs.get('seconds'))}",
        "set_reminder": lambda: "setting that reminder",
        "cancel_reminder": lambda: "cancelling that reminder",
        "power": lambda: {"lock": "locking the computer", "sleep": "putting the computer to sleep",
                          "restart": "restarting the computer", "shutdown": "shutting down",
                          "sign_out": "signing you out"}.get(action, "on it"),
        "web_search": lambda: "looking that up",
        "write_file": lambda: "saving that",
        "run_powershell": lambda: "running that",
        "send_email": lambda: "sending that email",
        "send_discord": lambda: "sending that message",
        "save_contact": lambda: "saving that contact",
        "find_files": lambda: "searching now",
        "open_path": lambda: "opening that",
        "get_weather": lambda: "checking the weather",
        "add_to_list": lambda: "adding that",
        "remove_from_list": lambda: "taking that off",
        "clear_list": lambda: "deleting that list",
        "set_clipboard": lambda: "copying that",
        "look_at_screen": lambda: "taking a look",
        "run_project_command": lambda: f"running {inputs.get('command', 'that')}",
        "grant_folder_access": lambda: "granting that access",
        "revoke_folder_access": lambda: "removing that access",
        "set_voice": lambda: "changing my voice",
        "ui_action": lambda: "on it",
        "remember": lambda: "noted",
        "send_to_phone": lambda: "sending that to your phone",
        "spotify_play": lambda: f"playing {inputs.get('query') or 'your music'}",
        "spotify_control": lambda: {"pause": "pausing", "resume": "resuming", "next": "skipping",
                                    "previous": "going back"}.get(inputs.get("action"), "on it"),
        "calendar_events": lambda: "checking your calendar",
        "update_jarvis": lambda: "updating myself",
        "create_agent": lambda: f"putting {inputs.get('name', 'an agent')} on it",
        "open_dashboard": lambda: "opening the dashboard",
        "browser_open": lambda: "opening that in my browser",
        "browser_search": lambda: "searching for that",
        "start_background_task": lambda: "I'll work on that in the background",
        "watch_download": lambda: "I'll keep an eye on it",
        "watch_app_closes": lambda: "I'll keep an eye on it",
        "watch_file": lambda: "I'll keep an eye on it",
        "watch_cpu": lambda: "I'll keep an eye on it",
        "check_for_updates": lambda: "checking for updates",
        "calendar_add_event": lambda: "adding that to your calendar",
        "calendar_update_event": lambda: "updating your calendar",
        "calendar_delete_event": lambda: "removing that from your calendar",
        "forget": lambda: "forgetting that",
        "move_window_to_monitor": lambda: f"moving it to the {inputs.get('monitor', 'other')} screen",
        "quit_jarvis": lambda: "closing Jarvis completely" if inputs.get("completely") else "going on standby",
    }
    return phrases.get(name, lambda: "checking now")()


# Set when the user interrupts a task: no further tools run until the next request.
task_cancelled = threading.Event()
# What tools did during the current request, so an interrupted task can say what was already done.
activity: list[str] = []
_running = threading.Lock()  # held while a tool runs


def wait_for_running_tool(timeout_s: float = 5.0) -> None:
    """After an interruption, let the action already under way finish so `activity` is complete."""
    if _running.acquire(timeout=timeout_s):
        _running.release()


def run_tool(name: str, inputs: dict, confirm=None, acknowledge=None, cancelled: threading.Event | None = None,
             log: list[str] | None = None) -> str | list[dict]:
    """Run a tool. `confirm(question) -> bool` gates risky ones; `acknowledge(phrase)` announces it starting.
    `cancelled` / `log` default to the main conversation's; background tasks pass their own."""
    cancelled = task_cancelled if cancelled is None else cancelled
    log = activity if log is None else log
    if cancelled.is_set():
        return "The user interrupted this task. Stop now and do nothing else."
    func = TOOL_MAP.get(name)
    if not func:
        return f"Unknown tool: {name}"
    question = (finder.confirmation(name, inputs) or messaging.confirmation(name, inputs)
                or lists.confirmation(name, inputs) or projects.confirmation(name, inputs)
                or ui.confirmation(name, inputs) or phone.confirmation(name, inputs)
                or gcal.confirmation(name, inputs) or updater.confirmation(name, inputs)
                or browser.confirmation(name, inputs)
                or needs_confirmation(name, inputs))
    if question and not (confirm and confirm(question)):
        return "The user declined this action. Do not retry it."
    if acknowledge:
        acknowledge(ack_phrase(name, inputs))
    try:
        with _running:
            result = func(**inputs)
        summary = result if isinstance(result, str) else "looked at the screen"
        log.append(f"{name}: {summary.splitlines()[0][:100] if summary else 'done'}")
        return result if isinstance(result, list) else str(result)  # a list is content blocks (e.g. an image)
    except Exception as e:
        return f"Tool error: {e}"
