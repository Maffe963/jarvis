"""Background tasks: bigger jobs Jarvis works on in a separate Claude conversation while you keep talking.

"Research the best budget mechanical keyboards and send me a summary" runs on its own; when it's done Jarvis
says so and sends the full report to your phone. Background tasks can't touch the screen, mouse, keyboard or
apps (so they never fight you for the PC), can't send email or messages, and anything that needs approval goes
to your phone (or is declined if phone control isn't on).
"""
import json
import os
import threading
import time
import uuid

from tools import system

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_FILE = os.path.join(ROOT, "memory", "background_tasks.json")
MAX_RUNNING = 2
KEEP_RESULTS = 30

# Read-and-research tools only: nothing that drives the screen, sends messages, or changes system state.
ALLOWED = {
    "web_search", "read_file", "list_files", "find_files", "write_file", "get_weather", "calendar_events",
    "check_email", "read_discord", "list_reminders", "set_reminder", "get_list", "add_to_list", "list_contacts",
    "list_facts", "remember", "run_project_command", "get_project_output", "send_to_phone", "system_info",
    "spotify_playlists", "list_folder_access",
}

PROMPT = """You are Jarvis, working on a background task the user handed off to you. They are NOT waiting for
you and won't hear you: work through it on your own with the tools, then finish with a complete, well-organised
written report in plain text (short headings and bullet points are fine; no markdown symbols like ** or #).
You can't use the screen, mouse, keyboard or apps, and you can't send email or messages. If something needs the
user's approval, the request goes to their phone and may be declined; carry on without it if so. If they asked
for a file (notes, a draft, a list), save it with write_file in their Documents folder under "Jarvis" and say
where. Be thorough but finish in a reasonable number of steps. Start your report with a one-sentence summary."""

_tasks: dict[str, dict] = {}
_lock = threading.Lock()


def _load() -> list[dict]:
    try:
        with open(RESULTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_result(task: dict) -> None:
    with _lock:
        results = [r for r in _load() if r["id"] != task["id"]]
        results.append({k: task[k] for k in ("id", "title", "task", "status", "started", "finished", "result")})
        with open(RESULTS_FILE, "w", encoding="utf-8") as f:
            json.dump(results[-KEEP_RESULTS:], f, indent=2)


def _phone():
    import telegram_bot

    bot = telegram_bot.active
    return bot if bot is not None and bot.chat_id is not None else None


def _run(task: dict) -> None:
    import brain

    phone = _phone()
    confirm = (lambda q: phone._ask(f"(Background task \"{task['title']}\") {q}")) if phone else (lambda q: False)
    try:
        report = brain.run_background(task["task"], PROMPT, ALLOWED, task["cancelled"], confirm, task["log"],
                                      label=f"Background: {task['title']}")
        task["status"], task["result"] = "done", report
    except brain.TaskInterrupted:
        task["status"], task["result"] = "cancelled", "Cancelled before it finished."
    except Exception as e:
        task["status"], task["result"] = "failed", f"It failed: {e}"
    task["finished"] = time.strftime("%Y-%m-%d %I:%M %p")
    _save_result(task)
    _tasks.pop(task["id"], None)
    if task["status"] == "cancelled":
        return
    summary = task["result"].strip().split("\n")[0][:200]
    if phone:
        phone.send(f"✅ Background task done: {task['title']}\n\n{task['result']}" if task["status"] == "done"
                   else f"⚠️ Background task \"{task['title']}\" {task['result']}")
    where = " The full report is on your phone." if phone else " Ask me for the result when you're ready."
    system.announce(f"Sir, your background task \"{task['title']}\" is {'done' if task['status'] == 'done' else 'over: ' + summary}."
                    + (f" {summary}" if task["status"] == "done" else "") + where)


# ---------- tools ----------

def start_background_task(task: str, title: str) -> str:
    running = [t for t in _tasks.values()]
    if len(running) >= MAX_RUNNING:
        return (f"Two background tasks are already running ({', '.join(t['title'] for t in running)}). "
                "Wait for one to finish or cancel one.")
    job = {"id": uuid.uuid4().hex[:6], "title": title.strip() or task[:40], "task": task, "status": "running",
           "started": time.strftime("%Y-%m-%d %I:%M %p"), "finished": "", "result": "",
           "cancelled": threading.Event(), "log": []}
    _tasks[job["id"]] = job
    threading.Thread(target=_run, args=(job,), daemon=True, name=f"background-{job['id']}").start()
    phone = " and send the full report to your phone" if _phone() else ""
    return (f"Started background task [{job['id']}] \"{job['title']}\". I'll tell you when it's done{phone}; "
            "you can keep using me meanwhile.")


def background_tasks() -> str:
    lines = []
    for t in _tasks.values():
        steps = len(t["log"])
        lines.append(f"[{t['id']}] running: {t['title']} (started {t['started']}, {steps} step{'s' if steps != 1 else ''} so far)")
    for r in reversed(_load()[-5:]):
        lines.append(f"[{r['id']}] {r['status']}: {r['title']} (finished {r['finished']})")
    return "\n".join(lines) or "No background tasks yet."


def background_task_result(which: str = "") -> str:
    wanted = which.lower().strip()
    results = list(reversed(_load()))
    match = next((r for r in results if not wanted or r["id"] == wanted or wanted in r["title"].lower()), None)
    if not match:
        running = next((t for t in _tasks.values() if wanted in (t["id"], t["title"].lower())), None)
        return f"\"{running['title']}\" is still running." if running else f"No finished background task like '{which}'."
    return f"{match['title']} ({match['status']}, finished {match['finished']}):\n{match['result']}"


def cancel_background_task(which: str) -> str:
    wanted = which.lower().strip()
    targets = [t for t in _tasks.values() if wanted in ("all", "everything") or t["id"] == wanted
               or wanted in t["title"].lower()]
    for t in targets:
        t["cancelled"].set()
    return f"Cancelling {', '.join(t['title'] for t in targets)}." if targets else f"No running background task like '{which}'."


TOOLS = [
    {
        "name": "start_background_task",
        "description": "Hand off a bigger job to work on in the background while the user carries on (research, "
                       "comparing options, summarising files or email, drafting something, checking several "
                       "sources). Use when the user says 'in the background', 'while I...', 'let me know when', or "
                       "the job would take more than a minute. The background worker can search the web, read and "
                       "write files, check calendar/email/jobs and send results to the phone, but cannot use the "
                       "screen or apps or send messages. Write `task` as complete, standalone instructions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Full instructions, including what the result should look like"},
                "title": {"type": "string", "description": "A short name, e.g. 'Keyboard research'"}
            },
            "required": ["task", "title"]
        }
    },
    {
        "name": "background_tasks",
        "description": "Background tasks running now and recently finished.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "background_task_result",
        "description": "The report from a finished background task (by [id] or words from its title; default the latest).",
        "input_schema": {"type": "object", "properties": {"which": {"type": "string"}}, "required": []}
    },
    {
        "name": "cancel_background_task",
        "description": "Stop a running background task (by [id], words from its title, or 'all').",
        "input_schema": {"type": "object", "properties": {"which": {"type": "string"}}, "required": ["which"]}
    },
]

TOOL_MAP = {"start_background_task": start_background_task, "background_tasks": background_tasks,
            "background_task_result": background_task_result, "cancel_background_task": cancel_background_task}
