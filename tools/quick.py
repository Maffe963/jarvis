"""Instant answers for the most common simple commands, without waiting for Claude.

Only short, exact phrasings match ("pause", "next song", "volume up", "what time is it"); anything else,
including anything with more to it ("pause and open Notion"), still goes to Claude.
"""
import re
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
    # "send me a screenshot (of my left screen) (to my phone)": straight to the phone, no questions.
    (r"(send|text)( me)?( a)? (screenshot|screen shot|picture of my screens?)( of( my| the)? (?P<m>[a-z]+)"
     r"( screens?| monitors?)?)?( to( my)? phone)?",
     lambda m: ("send_screenshot", {"monitor": m.group("m") or ""})),
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
            return action(found)
    return None
