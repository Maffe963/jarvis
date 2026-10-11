"""Click, type and press keys in any app, finding buttons by their label (Windows UI Automation) or by
position in a screenshot that Claude has looked at.

Anything that looks destructive (a "Delete" or "Send" button, Alt+F4, typing into a terminal) is
confirmed with the user first.
"""
import ctypes
import re
import time
from ctypes import wintypes

import psutil
import win32api
import win32con
import win32gui
import win32process

from tools import desktop

DESTRUCTIVE = re.compile(
    r"\b(delete|remove|discard|erase|uninstall|format|reset|clear|empty|trash|bin|send|submit|post|publish|"
    r"buy|purchase|pay|checkout|order|confirm|sign out|log ?out|unsubscribe|cancel subscription|deactivate|"
    r"close account|overwrite|replace|transfer|withdraw)\b", re.IGNORECASE)
RISKY_KEYS = {"alt+f4", "ctrl+w", "ctrl+shift+w", "ctrl+q", "delete", "shift+delete", "ctrl+shift+delete",
              "ctrl+d", "ctrl+enter", "ctrl+shift+enter"}
TERMINALS = {"cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe", "wt.exe", "bash.exe", "mintty.exe",
             "conhost.exe", "openconsole.exe"}
MAX_ELEMENTS = 4000
KEY_NAMES = {
    "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "space": 0x20, "backspace": 0x08,
    "delete": 0x2E, "del": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "ctrl": 0x11, "control": 0x11, "shift": 0x10,
    "alt": 0x12, "win": 0x5B, "windows": 0x5B, "capslock": 0x14, "printscreen": 0x2C,
    **{f"f{n}": 0x6F + n for n in range(1, 13)},
}

_uia = None


def _automation():
    global _uia
    if _uia is None:
        import comtypes.client

        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as U

        _uia = (comtypes.client.CreateObject(U.CUIAutomation, interface=U.IUIAutomation), U)
    return _uia


def _window(title: str) -> int:
    if not title.strip():
        return win32gui.GetForegroundWindow()
    wanted = title.lower()
    found = []

    def collect(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and wanted in win32gui.GetWindowText(hwnd).lower():
            found.append(hwnd)

    win32gui.EnumWindows(collect, None)
    return found[0] if found else 0


def _process_name(hwnd: int) -> str:
    try:
        return psutil.Process(win32process.GetWindowThreadProcessId(hwnd)[1]).name().lower()
    except (psutil.Error, OSError):
        return ""


def _elements(label: str, window: str = "") -> list[dict]:
    """On-screen controls whose name contains `label`, best matches first."""
    uia, U = _automation()
    hwnd = _window(window)
    if not hwnd:
        return []
    root = uia.ElementFromHandle(hwnd)
    condition = uia.CreatePropertyConditionEx(
        U.UIA_NamePropertyId, label, U.PropertyConditionFlags_IgnoreCase | U.PropertyConditionFlags_MatchSubstring)
    found = root.FindAll(U.TreeScope_Descendants, condition)
    results = []
    for i in range(min(found.Length, MAX_ELEMENTS)):
        el = found.GetElement(i)
        rect = el.CurrentBoundingRectangle
        if rect.right <= rect.left or rect.bottom <= rect.top or el.CurrentIsOffscreen:
            continue
        results.append({
            "name": el.CurrentName, "type": el.CurrentLocalizedControlType or "",
            "x": (rect.left + rect.right) // 2, "y": (rect.top + rect.bottom) // 2,
            "enabled": bool(el.CurrentIsEnabled),
        })
    wanted = label.lower().strip()
    # Exact names first, then clickable things (buttons, links, menu items) over plain text.
    clickable = ("button", "link", "menu item", "tab item", "list item", "check box", "radio button", "hyperlink")
    results.sort(key=lambda r: (r["name"].lower().strip() != wanted, r["type"].lower() not in clickable))
    return results


def _name_at(x: int, y: int) -> str:
    try:
        uia, _ = _automation()
        el = uia.ElementFromPoint(wintypes.POINT(x, y))
        return el.CurrentName or ""
    except Exception:
        return ""


def _to_screen(x: float, y: float, coordinates: str) -> tuple[int, int]:
    if coordinates == "screenshot":
        geo = desktop.last_screenshot
        return int(geo["left"] + x * geo["scale"]), int(geo["top"] + y * geo["scale"])
    return int(x), int(y)


def _click(x: int, y: int, button: str = "left", count: int = 1) -> None:
    down, up = {"left": (win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP),
                "right": (win32con.MOUSEEVENTF_RIGHTDOWN, win32con.MOUSEEVENTF_RIGHTUP)}[button]
    win32api.SetCursorPos((x, y))
    time.sleep(0.05)
    for _ in range(count):
        win32api.mouse_event(down, 0, 0, 0, 0)
        win32api.mouse_event(up, 0, 0, 0, 0)
        time.sleep(0.05)


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("padding", ctypes.c_byte * 32)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def _type(text: str) -> None:
    """Type any text (including emoji and accents) into whatever has focus."""
    KEYEVENTF_UNICODE, KEYEVENTF_KEYUP = 0x0004, 0x0002
    for ch in text:
        if ch == "\n":
            _press("enter")
            continue
        encoded = ch.encode("utf-16-le")  # characters outside the basic range are two units
        for unit in (int.from_bytes(encoded[i:i + 2], "little") for i in range(0, len(encoded), 2)):
            events = (_INPUT * 2)()
            for ev, flags in zip(events, (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)):
                ev.type = 1
                ev.ki = _KEYBDINPUT(0, unit, flags, 0, 0)
            ctypes.windll.user32.SendInput(2, events, ctypes.sizeof(_INPUT))
        time.sleep(0.005)


def _parse_keys(keys: str) -> list[int] | None:
    codes = []
    for part in keys.lower().replace(" ", "").split("+"):
        if part in KEY_NAMES:
            codes.append(KEY_NAMES[part])
        elif len(part) == 1 and part.isalnum():
            codes.append(ord(part.upper()))
        else:
            return None
    return codes


def _press(keys: str) -> None:
    codes = _parse_keys(keys) or []
    for vk in codes:
        win32api.keybd_event(vk, 0, 0, 0)
    for vk in reversed(codes):
        win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)


# ---------- tools ----------

def find_ui_element(label: str, window: str = "") -> str:
    matches = _elements(label, window)
    if not matches:
        return (f"No on-screen element named like '{label}'. If it's visible, use look_at_screen and click by "
                "position (coordinates='screenshot').")
    return "\n".join(f"{m['type'] or 'element'} '{m['name']}' at ({m['x']}, {m['y']})"
                     f"{'' if m['enabled'] else ' [disabled]'}" for m in matches[:10])


def ui_action(action: str, target: str = "", x: float | None = None, y: float | None = None,
              coordinates: str = "screen", text: str = "", keys: str = "", window: str = "") -> str:
    if window.strip():
        hwnd = _window(window)
        if not hwnd:
            return f"No open window matching '{window}'."
        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        win32api.keybd_event(0x12, 0, 0, 0)  # Windows only lets the front app take focus; Alt is the usual workaround
        win32api.keybd_event(0x12, 0, win32con.KEYEVENTF_KEYUP, 0)
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.2)

    if action in ("click", "double_click", "right_click", "move"):
        if target.strip():
            matches = _elements(target, window)
            if not matches:
                return f"Couldn't find '{target}' on screen. Try look_at_screen and click by position."
            px, py, what = matches[0]["x"], matches[0]["y"], f"'{matches[0]['name']}'"
        elif x is not None and y is not None:
            px, py = _to_screen(x, y, coordinates)
            what = f"({px}, {py})"
        else:
            return "Give a target label, or x and y."
        if action == "move":
            win32api.SetCursorPos((px, py))
            return f"Moved the mouse to {what}."
        _click(px, py, "right" if action == "right_click" else "left", 2 if action == "double_click" else 1)
        return f"{action.replace('_', ' ').capitalize()}ed {what}."
    if action == "type_text":
        if not text:
            return "Nothing to type."
        _type(text)
        return f"Typed {len(text)} characters."
    if action == "key_press":
        if not _parse_keys(keys):
            return f"Don't know the keys '{keys}'. Use names like ctrl+s, alt+tab, enter, f5."
        _press(keys)
        return f"Pressed {keys}."
    if action == "scroll":
        amount = int(float(text or "-5"))  # negative = down
        win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, amount * 120, 0)
        return f"Scrolled {'down' if amount < 0 else 'up'}."
    return f"Unknown action '{action}'."


def confirmation(name: str, inputs: dict) -> str | None:
    if name != "ui_action":
        return None
    action = inputs.get("action", "")
    if action in ("click", "double_click"):
        label = inputs.get("target", "")
        if not label and inputs.get("x") is not None and inputs.get("y") is not None:
            label = _name_at(*_to_screen(inputs["x"], inputs["y"], inputs.get("coordinates", "screen")))
        if label and DESTRUCTIVE.search(label):
            return f"Click '{label}'?"
    if action == "key_press":
        keys = inputs.get("keys", "").lower().replace(" ", "")
        if keys in RISKY_KEYS:
            return f"Press {keys}? It may close or delete something."
        if keys in ("enter", "return") and _process_name(_window(inputs.get("window", ""))) in TERMINALS:
            return "Press Enter in the terminal? That runs whatever is typed there."
    if action == "type_text":
        hwnd = _window(inputs.get("window", ""))
        if _process_name(hwnd) in TERMINALS:
            return f"Type this into the terminal: {inputs.get('text', '')}?"
    return None


TOOLS = [
    {
        "name": "find_ui_element",
        "description": "Find buttons, links, menu items or fields by their visible label in the front window (or "
                       "`window`), using Windows accessibility info. Returns screen positions.",
        "input_schema": {
            "type": "object",
            "properties": {"label": {"type": "string"}, "window": {"type": "string", "description": "Part of a window title"}},
            "required": ["label"]
        }
    },
    {
        "name": "ui_action",
        "description": (
            "Operate any app like a person would. Actions: click / double_click / right_click / move (by `target` "
            "label, or by x,y), type_text (`text`, typed into whatever has focus; '\\n' presses Enter), key_press "
            "(`keys`, e.g. 'ctrl+s', 'alt+tab', 'enter'), scroll (`text` = clicks, negative = down). Use `window` "
            "to bring a window to the front first. Be efficient: prefer typing and keyboard shortcuts when the app "
            "supports them; otherwise call find_ui_element to learn the exact labels (e.g. Calculator's '7' is "
            "'Seven') before clicking by target. Only for things without a label, call look_at_screen and pass x,y "
            "from that image with coordinates='screenshot'. Check the result with look_at_screen when unsure. Destructive clicks/keys and typing into terminals are confirmed with the user."),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "enum": ["click", "double_click", "right_click", "move", "type_text", "key_press", "scroll"]},
                "target": {"type": "string", "description": "Visible label of the thing to click"},
                "x": {"type": "number"}, "y": {"type": "number"},
                "coordinates": {"type": "string", "enum": ["screen", "screenshot"]},
                "text": {"type": "string"}, "keys": {"type": "string"},
                "window": {"type": "string", "description": "Part of a window title to focus first"}
            },
            "required": ["action"]
        }
    },
]

TOOL_MAP = {"find_ui_element": find_ui_element, "ui_action": ui_action}
