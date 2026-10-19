"""Instant answers for the most common simple commands, without waiting for Claude.

Only short, exact phrasings match ("pause", "next song", "volume up", "what time is it"); anything else,
including anything with more to it ("pause and open Notion"), still goes to Claude.
"""
import re
import threading
from datetime import datetime

FILLER = re.compile(r"^(hey |ok |okay )?(jarvis[, ]+)?(please |can you |could you |would you )*|( please| now| jarvis)+$")


def _spotify_connected() -> bool:
    import os

    from tools import oauth

    return bool(os.environ.get("SPOTIFY_CLIENT_ID")) and os.path.exists(oauth._path("spotify"))


def _media(action: str):
    """Use Spotify's API when connected (a real pause, not a toggle); otherwise the keyboard media keys."""
    if _spotify_connected():
        return "spotify_control", {"action": {"play_pause": "pause"}.get(action, action)}
    return "media_control", {"action": action}


def _move(m):
    """move_window_to_monitor for "move X to the left screen", or None (Claude decides) for things like "move on"."""
    if m.group("w") in ("on", "over", "up", "back", "along", "forward"):
        return None
    return "move_window_to_monitor", {"monitor": m.group("m"), "window": _window_name(m.group("w"))}


def _window_name(said: str) -> str:
    """"the spotify window" -> "spotify"; "this" / "it" -> "" (the window in front)."""
    words = [w for w in said.split() if w not in ("the", "my", "window", "app", "application")]
    name = " ".join(words)
    return "" if name in ("this", "it", "that", "this one", "that one", "") else name


def _open(m):
    """open_app for "open Spotify (on my left screen)", only when it's clearly an installed app; anything else
    ("open my downloads", "open youtube.com") goes to Claude."""
    from tools import system

    said = m.group("app").split()
    if not said or said[0] in ("my", "the", "a", "this", "that", "it") or len(said) > 3 \
            or any(w in said for w in ("and", "then", "folder", "file", "website")):
        return None  # "open my photos" may mean the Pictures folder: Claude decides
    name = " ".join(said)
    lowered = name.lower()
    if system._start_apps is None:  # reading the app list takes a few seconds: not now, but ready for next time
        threading.Thread(target=system._get_start_apps, daemon=True).start()
        return None
    apps = system._start_apps
    if not any(a["Name"].lower() == lowered for a in apps):  # "youtube" isn't "YouTube Music": Claude decides
        return None
    if m.group("m"):
        if system._matching_windows(name):  # already open: move it there rather than start another copy
            return "move_window_to_monitor", {"monitor": m.group("m"), "window": name}
        return "open_app", {"name": name, "monitor": m.group("m")}
    return "open_app", {"name": name, "monitor": ""}


def load_apps() -> None:
    from tools import system

    try:
        system._get_start_apps()
    except Exception as e:
        print(f"[quick] couldn't read the app list: {e}")


UNITS = {"second": 1, "sec": 1, "minute": 60, "min": 60, "hour": 3600}


def _timer(m):
    seconds = int(m.group("n") or m.group("n2")) * UNITS[m.group("u") or m.group("u2")]
    return ("set_timer", {"seconds": seconds}) if 0 < seconds <= 24 * 3600 else None


def _now(fmt: str):
    return None, datetime.now().strftime(fmt).replace(" 0", " ").lstrip("0")


# (pattern, what to do) -> a (tool, inputs) to run, or (None, text) to just say something.
COMMANDS = [
    # A bare "stop" means "stop what you're doing", not the music, so stopping needs a noun.
    (r"pause( the)?( music| song| spotify| playback)?|stop( the)? (music|song|spotify|playback)",
     lambda m: _media("play_pause")),
    (r"(play|resume|unpause|continue)( the)?( music| song| spotify| playback)", lambda m: (
        ("spotify_control", {"action": "resume"}) if _spotify_connected() else ("media_control", {"action": "play_pause"}))),
    (r"(next|skip)( song| track| this( song| track)?| it)?|play the next (song|track)", lambda m: _media("next")),
    (r"(previous|last|go back( a| one)?)( song| track)|play the (previous|last) (song|track)", lambda m: _media("previous")),
    (r"(turn( it| the volume| the music)? up|volume up|louder|turn up the (volume|music))",
     lambda m: ("volume", {"action": "up"})),
    (r"(turn( it| the volume| the music)? down|volume down|quieter|softer|turn down the (volume|music))",
     lambda m: ("volume", {"action": "down"})),
    (r"(set )?(the )?volume( to)? (\d{1,3})( ?percent| ?%)?", lambda m: ("volume", {"action": "set", "level": int(m.group(4))})),
    (r"mute( the volume| the sound| it)?", lambda m: ("volume", {"action": "mute"})),
    (r"unmute( the volume| the sound| it)?", lambda m: ("volume", {"action": "unmute"})),
    # "move Spotify to the left screen", "put this on my main monitor": no Claude, nothing to confirm.
    (r"(?:move|put|send|throw|drag|switch) (?P<w>.+?) (?:over )?(?:to|on|onto) (?:the |my )?"
     r"(?P<m>left|right|main|middle|center|centre|primary|other|second|first|third|top|bottom|\d)"
     r"(?: one)?(?: screen| monitor| display)",
     lambda m: _move(m)),
    (r"(?:move|put|throw|drag) (?P<w>.+?) (?:over )?(?:to|on|onto) the (?P<m>left|right)(?: side)?",  # "...to the left"
     lambda m: _move(m)),
    # "send me a screenshot (of my left screen) (to my phone)": straight to the phone, no questions.
    (r"(send|text)( me)?( a)? (screenshot|screen shot|picture of my screens?)( of( my| the)? (?P<m>[a-z]+)"
     r"( screens?| monitors?)?)?( to( my)? phone)?",
     lambda m: ("send_screenshot", {"monitor": m.group("m") or ""})),
    (r"(?:open|launch|start) (?P<app>[a-z0-9 .+'-]+?)(?: (?:on|to) (?:the |my )?(?P<m>left|right|main|"
     r"middle|center|centre|primary|other|second|first|third|top|bottom|\d)(?: one)?(?: screen| monitor| display))?",
     lambda m: _open(m)),
    (r"(?:set |start )?(?:a |an )?(?:timer for (?P<n>\d{1,3}) (?P<u>second|sec|minute|min|hour)s?"
     r"|(?P<n2>\d{1,3}) (?P<u2>second|sec|minute|min|hour)s? timer)",
     lambda m: _timer(m)),
    (r"lock (?:the |my )?(?:computer|pc|screen|laptop)", lambda m: ("power", {"action": "lock"})),
    (r"what time is it|what's the time|whats the time|what is the time", lambda m: _now("It's %I:%M %p, sir.")),
    (r"what('s| is) (the date|today's date)|what day is it( today)?|whats the date",
     lambda m: _now("It's %A, %B %d, sir.")),
]


def match(text: str):
    """(tool, inputs) or (None, reply) for an instant command, or None to hand it to Claude."""
    said = re.sub(r"[^a-z0-9%' ]", " ", text.lower())
    said = re.sub(r"\s+", " ", said).strip()
    said = FILLER.sub("", said).strip()
    for pattern, action in COMMANDS:
        found = re.fullmatch(pattern, said)
        if found:
            result = action(found)
            if result is not None:  # None: not this one after all ("start a timer" isn't an app)
                return result
    return None
