"""Tools that let Jarvis control the Windows PC."""
import ctypes
import difflib
import json
import os
import subprocess
import threading
import time
import webbrowser

import psutil

from tools import files, monitors

user32 = ctypes.windll.user32

# Set by main.py so background tools (timers) can talk. Defaults to printing.
_announce = print


def set_announcer(fn) -> None:
    global _announce
    _announce = fn


def announce(message: str) -> None:
    _announce(message)


# ---------- apps ----------

_start_apps: list[dict] | None = None


def _get_start_apps() -> list[dict]:
    """Every app in the Start menu (desktop and Store apps), as {Name, AppID}."""
    global _start_apps
    if _start_apps is None:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-StartApps | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=30,
        ).stdout
        _start_apps = json.loads(out) if out.strip() else []
    return _start_apps


def _match_app(name: str) -> dict | None:
    apps = _get_start_apps()
    lowered = name.lower().strip()
    names = [a["Name"].lower() for a in apps]
    for i, n in enumerate(names):
        if n == lowered:
            return apps[i]
    for i, n in enumerate(names):
        if n.startswith(lowered) or lowered in n:
            return apps[i]
    close = difflib.get_close_matches(lowered, names, n=1, cutoff=0.6)
    return apps[names.index(close[0])] if close else None


def _open_on_monitor(launch, monitor: str, what: str) -> str:
    """Launch something; if a monitor was named, move the window it opens onto that monitor."""
    if not monitor.strip():
        launch()
        return f"Opened {what}."
    target = monitors.find_monitor(monitor)
    if not target:
        launch()
        return f"Opened {what}, but I'm not sure which screen '{monitor}' is. Monitors:\n{monitors.list_monitors()}"
    before = set(monitors.top_level_windows())
    launch()
    hwnd = monitors.wait_for_new_window(before)
    if not hwnd:
        return f"Opened {what}, but couldn't find its window to move."
    monitors.place_window(hwnd, target)
    return f"Opened {what} on {monitors.describe(target, len(monitors.monitors())).split(':')[0]}."


def open_app(name: str, monitor: str = "") -> str:
    global _start_apps
    app = _match_app(name)
    if not app:
        _start_apps = None  # maybe installed since the list was read; look again
        app = _match_app(name)
    if not app:
        return f"No installed app matching '{name}'."
    return _open_on_monitor(lambda: subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app['AppID']}"]),
                            monitor, app["Name"])


# What people call an app -> its program name.
APP_ALIASES = {
    "word": "winword", "microsoft word": "winword", "excel": "excel", "powerpoint": "powerpnt",
    "outlook": "outlook", "onenote": "onenote", "vs code": "code", "vscode": "code", "visual studio code": "code",
    "edge": "msedge", "microsoft edge": "msedge", "google chrome": "chrome", "file explorer": "explorer",
    "explorer": "explorer", "files": "explorer", "teams": "ms-teams", "calculator": "calculatorapp",
    "settings": "systemsettings", "task manager": "taskmgr", "terminal": "windowsterminal",
    "command prompt": "cmd", "photos": "photos", "paint": "mspaint", "snipping tool": "snippingtool",
}
# Parts of Windows itself that must never be closed.
NEVER_CLOSE = {"dwm", "csrss", "winlogon", "searchhost", "searchapp", "startmenuexperiencehost",
               "shellexperiencehost", "textinputhost", "lockapp", "systemtray", "applicationframehost"}
EVERYTHING = {"all", "everything", "all apps", "all my apps", "all windows", "everything open"}


def _jarvis_pids() -> set[int]:
    """Jarvis and the terminal it runs in: closing those would close Jarvis itself."""
    pids, proc = set(), psutil.Process()
    while proc is not None:
        pids.add(proc.pid)
        try:
            proc = proc.parent()
        except psutil.Error:
            break
    return pids


def _app_windows() -> list[dict]:
    """Open app windows with their program names (Store apps resolved to the real app, not their frame host)."""
    protected = _jarvis_pids()
    console = ctypes.windll.kernel32.GetConsoleWindow()
    found = []
    for hwnd, title in _visible_windows().items():
        window = _window_info(hwnd, title, protected, console)
        if window:
            found.append(window)
    return found


def _window_info(hwnd: int, title: str, protected: set[int] | None = None, console: int | None = None) -> dict | None:
    """{"hwnd", "title", "exe", "pid"} for an app window the user sees, or None for anything else."""
    import win32con
    import win32gui
    import win32process

    def exe_of(pid: int) -> str:
        try:
            return psutil.Process(pid).name().lower().removesuffix(".exe")
        except psutil.Error:
            return ""

    if protected is None:
        protected = _jarvis_pids()
    if console is None:
        console = ctypes.windll.kernel32.GetConsoleWindow()
    if _is_background_window(hwnd, win32con, win32gui):
        return None
    pid = win32process.GetWindowThreadProcessId(hwnd)[1]
    exe = exe_of(pid)
    if exe == "applicationframehost":  # Store apps (Calculator, Settings...) live in a child window
        children = []
        win32gui.EnumChildWindows(hwnd, lambda child, _: children.append(child), None)
        for child in children:
            child_pid = win32process.GetWindowThreadProcessId(child)[1]
            if child_pid != pid:
                pid, exe = child_pid, exe_of(child_pid)
                break
    if (not exe or exe in NEVER_CLOSE or pid in protected or hwnd == console
            or (exe == "explorer" and win32gui.GetClassName(hwnd) != "CabinetWClass")):
        return None  # explorer.exe is also the taskbar and desktop; only its folder windows are "File Explorer"
    return {"hwnd": hwnd, "title": title, "exe": exe, "pid": pid}


def _is_background_window(hwnd: int, win32con, win32gui) -> bool:
    """Tool windows, zero-size helpers and hidden ("cloaked") Store app frames aren't apps the user sees."""
    if win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE) & win32con.WS_EX_TOOLWINDOW:
        return True
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    if right - left < 50 or bottom - top < 50:
        return True
    cloaked = ctypes.c_int(0)
    ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))  # DWMWA_CLOAKED
    return bool(cloaked.value)


_labels: dict[str, str] = {}


def _app_label(window: dict) -> str:
    """The program's own name ("TradingView", "Spotify"), from its file's description."""
    exe = window["exe"]
    if exe not in _labels:
        label = ""
        try:
            import win32api

            path = psutil.Process(window["pid"]).exe()
            lang, page = win32api.GetFileVersionInfo(path, "\\VarFileInfo\\Translation")[0]
            label = win32api.GetFileVersionInfo(path, f"\\StringFileInfo\\{lang:04x}{page:04x}\\FileDescription") or ""
        except Exception:
            pass  # Store apps' files can't be read; fall back to the window title below
        if not label.strip():
            title = window["title"]
            return title if len(title) <= 30 else exe
        _labels[exe] = label.strip()
    return _labels[exe]


def _matching_windows(name: str) -> list[dict]:
    windows = _app_windows()
    wanted = name.lower().strip().removesuffix(".exe")
    if wanted in EVERYTHING:
        return windows
    compact = wanted.replace(" ", "")
    exe = APP_ALIASES.get(wanted, compact)
    by_program = [w for w in windows if w["exe"] == exe or (len(compact) >= 3 and w["exe"].startswith(compact))]
    if by_program:
        return by_program
    if len(wanted) < 3:
        return []
    return [w for w in windows if wanted in w["title"].lower()]


def close_app(name: str) -> str:
    """Politely close an app's windows, like clicking X: apps can still ask to save unsaved work."""
    import win32con
    import win32gui

    targets = _matching_windows(name)
    if not targets:
        open_now = sorted({_app_label(w) for w in _app_windows()})
        return f"Nothing open matching '{name}'. Open apps: {', '.join(open_now) or 'none'}."
    for w in targets:
        win32gui.PostMessage(w["hwnd"], win32con.WM_CLOSE, 0, 0)
    deadline = time.time() + 3
    while time.time() < deadline:
        still = [w for w in targets if win32gui.IsWindow(w["hwnd"]) and win32gui.IsWindowVisible(w["hwnd"])]
        if not still:
            break
        time.sleep(0.2)
    closed = sorted({_app_label(w) for w in targets if w not in still})
    waiting = sorted({_app_label(w) for w in still})
    parts = []
    if closed:
        parts.append(f"Closed {', '.join(closed)}.")
    if waiting:
        parts.append(f"{', '.join(waiting)} {'is' if len(waiting) == 1 else 'are'} still open, probably asking "
                     "whether to save changes.")
    return " ".join(parts)


def list_running_apps() -> str:
    titles = _visible_windows()
    return "\n".join(sorted(set(titles.values()))) or "No open windows."


# ---------- windows ----------

def _visible_windows() -> dict[int, str]:
    import win32gui

    windows: dict[int, str] = {}

    def collect(hwnd, _):
        title = win32gui.GetWindowText(hwnd)
        if win32gui.IsWindowVisible(hwnd) and title and title not in ("Program Manager", "Settings"):
            windows[hwnd] = title

    win32gui.EnumWindows(collect, None)
    return windows


def focus_window(title: str) -> str:
    import win32con
    import win32gui

    lowered = title.lower()
    for hwnd, t in _visible_windows().items():
        if lowered in t.lower():
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            # Windows blocks background processes from stealing focus; a synthetic
            # Alt press is the standard workaround.
            user32.keybd_event(0x12, 0, 0, 0)
            user32.keybd_event(0x12, 0, 2, 0)
            win32gui.SetForegroundWindow(hwnd)
            return f"Switched to '{t}'."
    return f"No open window matching '{title}'."


def show_desktop() -> str:
    _press_combo(0x5B, 0x44)  # Win + D
    return "Toggled the desktop."


# ---------- keyboard / media ----------

def _press(vk: int) -> None:
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, 2, 0)


def _press_combo(*vks: int) -> None:
    for vk in vks:
        user32.keybd_event(vk, 0, 0, 0)
    for vk in reversed(vks):
        user32.keybd_event(vk, 0, 2, 0)


MEDIA_KEYS = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2}


def media_control(action: str) -> str:
    vk = MEDIA_KEYS.get(action)
    if vk is None:
        return f"Unknown media action '{action}'. Use one of {list(MEDIA_KEYS)}."
    _press(vk)
    return f"Sent media {action}."


# ---------- volume ----------

def _endpoint():
    from pycaw.pycaw import AudioUtilities

    return AudioUtilities.GetSpeakers().EndpointVolume


def volume(action: str, level: int | None = None) -> str:
    ev = _endpoint()
    current = round(ev.GetMasterVolumeLevelScalar() * 100)
    if action == "get":
        return f"Volume is {current}%{' (muted)' if ev.GetMute() else ''}."
    if action == "mute":
        ev.SetMute(1, None)
        return "Muted."
    if action == "unmute":
        ev.SetMute(0, None)
        return "Unmuted."
    if action in ("set", "up", "down"):
        step = level if level is not None else 10
        target = {"set": step, "up": current + step, "down": current - step}[action]
        target = max(0, min(100, target))
        ev.SetMute(0, None)
        ev.SetMasterVolumeLevelScalar(target / 100, None)
        return f"Volume set to {target}%."
    return f"Unknown volume action '{action}'."


# ---------- web ----------

def open_url(url: str, monitor: str = "") -> str:
    if "://" not in url:
        url = "https://" + url
    if monitor.strip():
        # A new window, not a tab in an existing one, so only this page moves.
        return _open_on_monitor(lambda: webbrowser.open(url, new=1), monitor, url)
    webbrowser.open(url)
    return f"Opened {url}."


# ---------- system ----------

def system_info() -> str:
    parts = [
        f"CPU {psutil.cpu_percent(interval=0.5):.0f}%",
        f"RAM {psutil.virtual_memory().percent:.0f}% used",
        f"C: drive {psutil.disk_usage('C:/').percent:.0f}% full",
    ]
    battery = psutil.sensors_battery()
    if battery:
        parts.append(f"battery {battery.percent:.0f}%{' charging' if battery.power_plugged else ''}")
    return ", ".join(parts) + "."


def power(action: str) -> str:
    if action == "lock":
        user32.LockWorkStation()
        return "Locked."
    commands = {
        "sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
        "restart": ["shutdown", "/r", "/t", "5"],
        "shutdown": ["shutdown", "/s", "/t", "5"],
        "sign_out": ["shutdown", "/l"],
    }
    if action not in commands:
        return f"Unknown power action '{action}'."
    subprocess.Popen(commands[action])
    return f"Executing {action}."


def run_powershell(command: str) -> str:
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        return "Command timed out after 60 seconds."
    out = (result.stdout + result.stderr).strip() or f"(no output, exit code {result.returncode})"
    return out[:4000]


# ---------- timers ----------

quit_requested = threading.Event()     # main.py exits once the current reply is done
standby_requested = threading.Event()  # main.py goes on standby once the current reply is done


def quit_jarvis(completely: bool = False) -> str:
    if completely:
        quit_requested.set()
        return "Jarvis will close completely after this reply. The computer stays on."
    standby_requested.set()
    return "Jarvis will go on standby after this reply (only 'hey Jarvis' keeps listening). Don't say goodbye; Jarvis announces it."


def set_timer(seconds: int, label: str = "") -> str:
    def ring():
        announce(f"Sir, your {label + ' ' if label else ''}timer is done.")

    t = threading.Timer(seconds, ring)
    t.daemon = True
    t.start()
    done_at = time.strftime("%I:%M %p", time.localtime(time.time() + seconds)).lstrip("0")
    return f"Timer set for {seconds} seconds (done at {done_at})."


# ---------- registry ----------

def needs_confirmation(name: str, inputs: dict) -> str | None:
    """Return a question to ask the user before running this tool call, or None if it's safe."""
    if name == "close_app":
        # Closing one app is as safe as clicking its X (it can still ask to save); ask only when it's
        # several different apps, e.g. a vague name or "everything".
        apps = sorted({_app_label(w) for w in _matching_windows(inputs.get("name", ""))})
        return f"Close {', '.join(apps)}?" if len(apps) > 1 else None
    if name == "power" and inputs.get("action") != "lock":
        return f"{inputs.get('action', '').replace('_', ' ').capitalize()} the computer?"
    if name == "run_powershell":
        return f"Run this command: {inputs.get('command')}?"
    if name == "grant_folder_access":
        folder = files.resolve(inputs.get("folder", ""))
        whole_drive = os.path.splitdrive(folder)[1] in ("\\", "")
        return (f"Let me read and change files in {folder}{' (your whole ' + folder[:2] + ' drive)' if whole_drive else ''} "
                "from now on without asking?")
    if name in ("read_file", "write_file", "list_files"):
        path = inputs.get("directory", ".") if name == "list_files" else inputs.get("path", "")
        if not files.is_allowed(path):
            verb = {"read_file": "Read", "write_file": "Write to", "list_files": "Look in"}[name]
            why = ("It may contain passwords or keys." if files.is_sensitive(path)
                   else "It's outside your usual folders.")
            return f"{verb} {files.resolve(path)}? {why}"
        if name == "write_file" and os.path.exists(files.resolve(path)):
            return f"Overwrite {files.resolve(path)}?"
    return None


def _schema(name, description, properties=None, required=None):
    return {
        "name": name,
        "description": description,
        "input_schema": {"type": "object", "properties": properties or {}, "required": required or []},
    }


SYSTEM_TOOLS = [
    _schema("open_app", "Launch an installed application by name (e.g. 'Spotify', 'Chrome', 'Calculator'), "
            "optionally on a specific monitor.",
            {"name": {"type": "string"},
             "monitor": {"type": "string", "description": "Only if the user named a screen: 'left', 'right', "
                                                          "'main', '2', 'the vertical one', ..."}}, ["name"]),
    _schema("close_app", "Close an open app politely, like clicking its X (it can still ask to save work; never "
            "force-quits). Use the name the user said, e.g. 'Spotify', 'Word', 'VS Code', 'File Explorer', or "
            "'everything'. Closing several apps at once is confirmed with the user.",
            {"name": {"type": "string"}}, ["name"]),
    _schema("list_running_apps", "List the titles of open windows."),
    _schema("focus_window", "Bring an open window to the front by part of its title.",
            {"title": {"type": "string"}}, ["title"]),
    _schema("show_desktop", "Minimize everything / restore (Win+D)."),
    _schema("media_control", "Control whatever media is playing (Spotify, YouTube, etc.).",
            {"action": {"type": "string", "enum": list(MEDIA_KEYS)}}, ["action"]),
    _schema("volume", "Get or change the system volume.",
            {"action": {"type": "string", "enum": ["get", "set", "up", "down", "mute", "unmute"]},
             "level": {"type": "integer", "description": "For 'set': target 0-100. For 'up'/'down': step size (default 10)."}},
            ["action"]),
    _schema("open_url", "Open a URL in the default browser. Use for websites, YouTube searches "
            "(https://www.youtube.com/results?search_query=...), Google searches, etc., optionally on a specific monitor.",
            {"url": {"type": "string"},
             "monitor": {"type": "string", "description": "Only if the user named a screen"}}, ["url"]),
    _schema("system_info", "Get CPU, RAM, disk, and battery status."),
    _schema("power", "Lock, sleep, restart, shut down, or sign out of the computer. Everything except lock is confirmed with the user.",
            {"action": {"type": "string", "enum": ["lock", "sleep", "restart", "shutdown", "sign_out"]}}, ["action"]),
    _schema("run_powershell", "Run a PowerShell command and return its output. Use only when no other tool fits. "
            "The user will be asked to confirm.",
            {"command": {"type": "string"}}, ["command"]),
    _schema("quit_jarvis", "Close Jarvis itself (this assistant), leaving the computer on. Use when the user "
            "wants Jarvis to quit, close, stop running or turn itself off. By default Jarvis goes on standby "
            "and wakes again on 'hey Jarvis'. Set completely when they want it off (turn off / shut down / switch "
            "off Jarvis, quit for good): then nothing keeps listening until they start Jarvis again.",
            {"completely": {"type": "boolean"}}),
    _schema("set_timer", "Set a countdown timer; Jarvis announces when it finishes.",
            {"seconds": {"type": "integer"}, "label": {"type": "string"}}, ["seconds"]),
]

SYSTEM_TOOL_MAP = {
    "quit_jarvis": quit_jarvis,
    "open_app": open_app,
    "close_app": close_app,
    "list_running_apps": list_running_apps,
    "focus_window": focus_window,
    "show_desktop": show_desktop,
    "media_control": media_control,
    "volume": volume,
    "open_url": open_url,
    "system_info": system_info,
    "power": power,
    "run_powershell": run_powershell,
    "set_timer": set_timer,
}
