"""Know the user's monitors and put windows on the one they ask for ("open Spotify on my left screen")."""
import ctypes
import re
import time

import win32api
import win32con
import win32gui

# Real pixel coordinates even when Windows display scaling isn't 100%.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except (AttributeError, OSError):
    pass

# No "one": "the left one" means the left monitor, not monitor 1.
ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "two": 2, "third": 3, "3rd": 3, "three": 3,
            "fourth": 4, "4th": 4, "four": 4}


def monitors() -> list[dict]:
    """Monitors ordered left to right, each with its work area (screen minus taskbar)."""
    found = []
    for handle, _, _ in win32api.EnumDisplayMonitors():
        info = win32api.GetMonitorInfo(handle)
        left, top, right, bottom = info["Monitor"]
        found.append({
            "device": info["Device"].replace("\\\\.\\", ""),
            "primary": bool(info["Flags"] & win32con.MONITORINFOF_PRIMARY),
            "rect": info["Monitor"], "work": info["Work"],
            "width": right - left, "height": bottom - top,
        })
    found.sort(key=lambda m: (m["rect"][0], m["rect"][1]))
    for number, m in enumerate(found, 1):
        m["number"] = number
    return found


def describe(m: dict, count: int) -> str:
    names = [f"monitor {m['number']}"]
    if m["primary"]:
        names.append("main")
    if count > 1 and m["number"] == 1:
        names.append("left")
    if count > 1 and m["number"] == count:
        names.append("right")
    if count == 3 and m["number"] == 2:
        names.append("middle")
    shape = "vertical" if m["height"] > m["width"] else "wide" if m["width"] / m["height"] > 2 else ""
    return f"{' / '.join(names)}: {m['width']}x{m['height']}{' ' + shape if shape else ''}"


def find_monitor(description: str) -> dict | None:
    screens = monitors()
    text = description.lower()
    if not screens:
        return None
    number = re.search(r"\b(\d)\b", text)
    for word, n in ORDINALS.items():
        if re.search(rf"\b{word}\b", text):
            number = n
            break
    else:
        number = int(number.group(1)) if number else None
    if number is not None:
        return next((m for m in screens if m["number"] == number), None)
    if re.search(r"\b(main|primary)\b", text):
        return next(m for m in screens if m["primary"])
    if "left" in text:
        return screens[0]
    if "right" in text:
        return screens[-1]
    if re.search(r"\b(middle|center|centre)\b", text):
        return screens[len(screens) // 2]
    if re.search(r"\b(vertical|portrait|tall)\b", text):
        return next((m for m in screens if m["height"] > m["width"]), None)
    if re.search(r"\b(ultrawide|wide)\b", text):
        return max(screens, key=lambda m: m["width"] / m["height"])
    if re.search(r"\b(other|second(ary)?)\b", text) and len(screens) == 2:
        return next(m for m in screens if not m["primary"])
    if "top" in text:
        return min(screens, key=lambda m: m["rect"][1])
    if "bottom" in text:
        return max(screens, key=lambda m: m["rect"][3])
    return None


def place_window(hwnd: int, monitor: dict, maximize: bool = True) -> None:
    """Move a window onto a monitor's work area, maximized or filling it."""
    left, top, right, bottom = monitor["work"]
    # A maximized window has to be restored before it can move to another screen.
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    win32gui.SetWindowPos(hwnd, None, left, top, right - left, bottom - top,
                          win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)
    if maximize:
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)


def top_level_windows() -> dict[int, str]:
    windows: dict[int, str] = {}

    def collect(hwnd, _):
        title = win32gui.GetWindowText(hwnd)
        if win32gui.IsWindowVisible(hwnd) and title and title not in ("Program Manager", "Settings"):
            windows[hwnd] = title

    win32gui.EnumWindows(collect, None)
    return windows


def wait_for_new_window(before: set[int], timeout_s: float = 10.0) -> int | None:
    """The window an app or link just opened: a brand-new window, or else the one that came to the front."""
    deadline = time.monotonic() + timeout_s
    first_foreground = win32gui.GetForegroundWindow()
    while time.monotonic() < deadline:
        new = [h for h in top_level_windows() if h not in before]
        if new:
            time.sleep(0.4)  # let it finish its own initial positioning first
            return new[0]
        foreground = win32gui.GetForegroundWindow()
        if foreground != first_foreground and foreground in top_level_windows():
            return foreground
        time.sleep(0.25)
    foreground = win32gui.GetForegroundWindow()
    return foreground if foreground in top_level_windows() else None


# ---------- tools ----------

def list_monitors() -> str:
    screens = monitors()
    return "\n".join(describe(m, len(screens)) for m in screens)


def move_window_to_monitor(monitor: str, window: str = "", maximize: bool = True) -> str:
    target = find_monitor(monitor)
    if not target:
        return f"Couldn't tell which screen '{monitor}' is. Monitors:\n{list_monitors()}"
    if window.strip() and window.strip().lower() not in ("this", "current", "active", "that", "it"):
        from tools import system

        # By program first ("Spotify" is Spotify.exe, whose title is the song playing), then by title.
        matches = system._matching_windows(window)
        if not matches:
            open_now = sorted({system._app_label(w) for w in system._app_windows()})
            return (f"Couldn't find an open window for '{window}'. Open apps: {', '.join(open_now) or 'none'}. Try "
                    "again with one of those names; don't use PowerShell or keyboard shortcuts to move windows.")
        hwnd, title = matches[0]["hwnd"], matches[0]["title"]  # the one used most recently
    else:
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
    place_window(hwnd, target, maximize)
    return f"Moved '{title}' to {describe(target, len(monitors())).split(':')[0]}."


TOOLS = [
    {
        "name": "list_monitors",
        "description": "List the user's monitors, numbered left to right, with names like main/left/right/middle.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "move_window_to_monitor",
        "description": "Move an open window to another monitor (maximized by default). `window` is the app's name "
                       "(e.g. 'Spotify', 'VS Code') or part of its title; omit it for the window in front. Always use "
                       "this to move windows (never PowerShell or keyboard shortcuts): call it once per window. To "
                       "open something on a monitor, use open_app/open_url's `monitor` option instead.",
        "input_schema": {
            "type": "object",
            "properties": {
                "monitor": {"type": "string", "description": "e.g. 'left', 'right', 'main', '2', 'the vertical one'"},
                "window": {"type": "string"},
                "maximize": {"type": "boolean"}
            },
            "required": ["monitor"]
        }
    },
]

TOOL_MAP = {"list_monitors": list_monitors, "move_window_to_monitor": move_window_to_monitor}
