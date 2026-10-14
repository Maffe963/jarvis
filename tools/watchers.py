"""'Tell me when it's done' alerts: a download finishing, an app closing, a file appearing, CPU staying high or
calming down. Jarvis says it aloud and sends it to your phone (if phone control is on)."""
import glob
import os
import threading
import time
import uuid

import psutil

from tools import files, system

CHECK_EVERY_S = 2
IN_PROGRESS = (".crdownload", ".part", ".partial", ".download", ".tmp", ".opdownload")
_watches: dict[str, dict] = {}


def _downloads() -> str:
    return os.path.join(os.path.expanduser("~"), "Downloads")


def _in_progress() -> list[str]:
    return [p for p in glob.glob(os.path.join(_downloads(), "*")) if p.lower().endswith(IN_PROGRESS)]


def _stable(path: str, seconds: float = 4) -> bool:
    """A file that exists and hasn't grown for a few seconds is done being written."""
    try:
        size = os.path.getsize(path)
        time.sleep(seconds)
        return os.path.getsize(path) == size
    except OSError:
        return False


def _start(kind: str, label: str, check, timeout_min: float) -> str:
    """Run `check()` every couple of seconds until it returns a message (or `timeout_min` passes)."""
    watch_id = uuid.uuid4().hex[:6]
    stop = threading.Event()
    _watches[watch_id] = {"kind": kind, "label": label, "stop": stop, "since": time.strftime("%I:%M %p").lstrip("0")}

    def run():
        deadline = time.monotonic() + timeout_min * 60
        try:
            while not stop.is_set():
                try:
                    message = check()
                except Exception as e:
                    message = f"I stopped watching {label} because something went wrong ({e})."
                if message:
                    system.announce(f"Sir, {message}")
                    return
                if time.monotonic() > deadline:
                    system.announce(f"Sir, I've stopped watching {label}: nothing happened in {timeout_min:g} minutes.")
                    return
                stop.wait(CHECK_EVERY_S)
        finally:
            _watches.pop(watch_id, None)

    threading.Thread(target=run, daemon=True, name=f"watch-{watch_id}").start()
    return watch_id


def watch_download(file_name: str = "", timeout_minutes: float = 240) -> str:
    """When a download finishes. With no name: whatever is downloading now (or the next one to start)."""
    wanted = file_name.lower().strip()
    pending = [p for p in _in_progress() if wanted in os.path.basename(p).lower()]
    seen_before = set(glob.glob(os.path.join(_downloads(), "*")))
    state = {"pending": pending}

    def check():
        now = _in_progress()
        mine = [p for p in now if wanted in os.path.basename(p).lower()]
        if not state["pending"] and mine:
            state["pending"] = mine  # a download started after we began watching
        if state["pending"] and not any(p in now for p in state["pending"]):
            finished = [p for p in glob.glob(os.path.join(_downloads(), "*"))
                        if p not in seen_before and not p.lower().endswith(IN_PROGRESS)
                        and wanted in os.path.basename(p).lower()]
            names = ", ".join(os.path.basename(p) for p in finished) or "it"
            return f"your download has finished: {names}."
        if wanted and not state["pending"]:  # a named file that downloads without a temp file
            for p in glob.glob(os.path.join(_downloads(), "*")):
                if p not in seen_before and wanted in os.path.basename(p).lower() and _stable(p):
                    return f"{os.path.basename(p)} has finished downloading."
        return None

    label = f"the download of {file_name}" if file_name else "your download"
    watch_id = _start("download", label, check, timeout_minutes)
    status = (f"Watching {len(pending)} download(s) in progress" if pending
              else "Nothing is downloading right now; I'll watch for the next download")
    return f"{status}. I'll tell you when it's done. [watch {watch_id}]"


def watch_app_closes(app: str, timeout_minutes: float = 480) -> str:
    """When an app closes or a program finishes running (e.g. an export or installer)."""
    wanted = app.lower().replace(" ", "").removesuffix(".exe")

    def running() -> list[psutil.Process]:
        found = []
        for p in psutil.process_iter(["name"]):
            name = (p.info["name"] or "").lower().replace(" ", "").removesuffix(".exe")
            if name and (name == wanted or name.startswith(wanted)):
                found.append(p)
        return found

    procs = running()
    windows = [w for w in system._app_windows() if wanted in w["title"].lower().replace(" ", "")]
    if not procs and windows:  # matched by window title instead (e.g. "Premiere" -> Adobe Premiere Pro.exe)
        procs = [psutil.Process(w["pid"]) for w in windows]
    if not procs:
        return f"Nothing called '{app}' is running right now."
    label = app

    def check():
        return None if any(p.is_running() and p.status() != psutil.STATUS_ZOMBIE for p in procs) \
            else f"{label} has closed."

    watch_id = _start("app", label, check, timeout_minutes)
    return f"Watching {label}; I'll tell you when it closes. [watch {watch_id}]"


def watch_file(path: str, timeout_minutes: float = 240) -> str:
    """When a file (or any file matching a pattern like *.mp4) appears and is fully written."""
    real = files.resolve(path)
    existing = set(glob.glob(real))

    def check():
        for p in glob.glob(real):
            if p not in existing and _stable(p):
                return f"{os.path.basename(p)} is ready in {os.path.dirname(p)}."
        return None

    watch_id = _start("file", os.path.basename(real), check, timeout_minutes)
    return f"Watching for {real}. [watch {watch_id}]"


def watch_cpu(above: float | None = None, below: float | None = None, minutes: float = 5,
              timeout_minutes: float = 480) -> str:
    """When CPU usage stays above (busy) or below (finished working) a level for some minutes."""
    if above is None and below is None:
        return "Give a level: above (e.g. 90) or below (e.g. 20)."
    state = {"since": None}

    def check():
        usage = psutil.cpu_percent(interval=1)
        hit = (above is not None and usage >= above) or (below is not None and usage <= below)
        if not hit:
            state["since"] = None
            return None
        state["since"] = state["since"] or time.monotonic()
        if time.monotonic() - state["since"] >= minutes * 60:
            return (f"the CPU has been above {above:g}% for {minutes:g} minutes (now {usage:.0f}%)." if above is not None
                    else f"the CPU has been below {below:g}% for {minutes:g} minutes, so the work looks finished.")
        return None

    label = f"CPU {'above ' + format(above, 'g') if above is not None else 'below ' + format(below, 'g')}%"
    watch_id = _start("cpu", label, check, timeout_minutes)
    return f"Watching the CPU ({label} for {minutes:g} min). [watch {watch_id}]"


def list_watches() -> str:
    if not _watches:
        return "I'm not watching anything."
    return "\n".join(f"[{i}] {w['label']} (since {w['since']})" for i, w in _watches.items())


def cancel_watch(which: str = "") -> str:
    wanted = which.lower().strip()
    targets = [i for i, w in _watches.items() if not wanted or wanted in ("all", "everything") or
               wanted == i or wanted in w["label"].lower() or wanted in w["kind"]]
    for i in targets:
        _watches.pop(i)["stop"].set()
    return f"Stopped watching {len(targets)} thing(s)." if targets else f"I'm not watching anything like '{which}'."


TIMEOUT = {"type": "number", "description": "Give up after this many minutes (default several hours)"}
TOOLS = [
    {
        "name": "watch_download",
        "description": "Tell the user when a download (in Downloads) finishes. Without a name: what's downloading "
                       "now, or the next download to start.",
        "input_schema": {"type": "object", "properties": {"file_name": {"type": "string"}, "timeout_minutes": TIMEOUT},
                         "required": []}
    },
    {
        "name": "watch_app_closes",
        "description": "Tell the user when an app closes or a program finishes running (installer, export, game).",
        "input_schema": {"type": "object", "properties": {"app": {"type": "string"}, "timeout_minutes": TIMEOUT},
                         "required": ["app"]}
    },
    {
        "name": "watch_file",
        "description": "Tell the user when a file appears and is fully written (path, or a pattern like "
                       "~/Videos/*.mp4), e.g. a render or export output.",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "timeout_minutes": TIMEOUT},
                         "required": ["path"]}
    },
    {
        "name": "watch_cpu",
        "description": "Tell the user when CPU usage stays above a level (something is hogging the PC) or below a "
                       "level (a long job has finished) for some minutes.",
        "input_schema": {"type": "object", "properties": {
            "above": {"type": "number"}, "below": {"type": "number"},
            "minutes": {"type": "number", "description": "How long it must stay there (default 5)"},
            "timeout_minutes": TIMEOUT}, "required": []}
    },
    {
        "name": "list_watches",
        "description": "What Jarvis is currently watching for.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "cancel_watch",
        "description": "Stop watching something (by [watch id], words from it, or 'all').",
        "input_schema": {"type": "object", "properties": {"which": {"type": "string"}}, "required": []}
    },
]

TOOL_MAP = {"watch_download": watch_download, "watch_app_closes": watch_app_closes, "watch_file": watch_file,
            "watch_cpu": watch_cpu, "list_watches": list_watches, "cancel_watch": cancel_watch}
