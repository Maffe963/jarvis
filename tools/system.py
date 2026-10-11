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

from tools import files

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


def open_app(name: str) -> str:
    global _start_apps
    app = _match_app(name)
    if not app:
        _start_apps = None  # maybe installed since the list was read; look again
        app = _match_app(name)
    if not app:
        return f"No installed app matching '{name}'."
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app['AppID']}"])
    return f"Opened {app['Name']}."


def _matching_processes(name: str) -> set[str]:
    """Exe names for `name`: exact matches if any, else names that start with it. Never loose substrings."""
    wanted = name.lower().replace(" ", "").removesuffix(".exe")
    if len(wanted) < 3:
        return set()
    running = {p.info["name"] for p in psutil.process_iter(["name"]) if p.info["name"]}
    stem = {exe: exe.lower().replace(" ", "").removesuffix(".exe") for exe in running}
    exact = {exe for exe, s in stem.items() if s == wanted}
    return exact or {exe for exe, s in stem.items() if s.startswith(wanted)}


def close_app(name: str) -> str:
    """Politely close an app (like clicking X), so it can still prompt to save work."""
    exe_names = _matching_processes(name)
    if not exe_names:
        return f"No running process matching '{name}'. Use the process name, e.g. 'chrome' or 'spotify'."
    for exe in exe_names:
        subprocess.run(["taskkill", "/IM", exe], capture_output=True)
    return f"Asked {', '.join(sorted(exe_names))} to close."


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

def open_url(url: str) -> str:
    if "://" not in url:
        url = "https://" + url
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

quit_requested = threading.Event()  # main.py exits once the current reply is done


def quit_jarvis() -> str:
    quit_requested.set()
    return "Jarvis will close after this reply. The computer stays on."


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
        exe_names = _matching_processes(inputs.get("name", ""))
        return f"Close {', '.join(sorted(exe_names))}?" if exe_names else None
    if name == "power" and inputs.get("action") != "lock":
        return f"{inputs.get('action', '').replace('_', ' ').capitalize()} the computer?"
    if name == "run_powershell":
        return f"Run this command: {inputs.get('command')}?"
    if name in ("read_file", "write_file", "list_files"):
        path = inputs.get("directory", ".") if name == "list_files" else inputs.get("path", "")
        if not files.is_allowed(path):
            verb = {"read_file": "Read", "write_file": "Write to", "list_files": "Look in"}[name]
            return f"{verb} {files.resolve(path)}? It's outside your usual folders."
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
    _schema("open_app", "Launch an installed application by name (e.g. 'Spotify', 'Chrome', 'Calculator').",
            {"name": {"type": "string"}}, ["name"]),
    _schema("close_app", "Close a running application by name. The user will be asked to confirm.",
            {"name": {"type": "string", "description": "App or process name, e.g. 'chrome' or 'spotify'"}}, ["name"]),
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
            "(https://www.youtube.com/results?search_query=...), Google searches, etc.",
            {"url": {"type": "string"}}, ["url"]),
    _schema("system_info", "Get CPU, RAM, disk, and battery status."),
    _schema("power", "Lock, sleep, restart, shut down, or sign out of the computer. Everything except lock is confirmed with the user.",
            {"action": {"type": "string", "enum": ["lock", "sleep", "restart", "shutdown", "sign_out"]}}, ["action"]),
    _schema("run_powershell", "Run a PowerShell command and return its output. Use only when no other tool fits. "
            "The user will be asked to confirm.",
            {"command": {"type": "string"}}, ["command"]),
    _schema("quit_jarvis", "Close Jarvis itself (this assistant), leaving the computer on. Use when the user "
            "wants Jarvis to quit, close, stop running or turn itself off."),
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
