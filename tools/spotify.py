"""Spotify: play specific songs, playlists, albums and artists, and see what's playing (Spotify Premium).

Setup (once): create an app at developer.spotify.com, add the redirect URI http://127.0.0.1:8888/callback,
put its Client ID in .env as SPOTIFY_CLIENT_ID, then run `.venv/Scripts/python.exe connect.py spotify`.
"""
import os
import time
import urllib.error

from tools import oauth

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"
PORT = 8888
SCOPES = ["user-read-playback-state", "user-modify-playback-state", "user-read-currently-playing",
          "playlist-read-private", "playlist-read-collaborative", "user-library-read"]
NOT_CONNECTED = ("Spotify isn't connected yet. On the PC run: .venv/Scripts/python.exe connect.py spotify "
                 "(needs SPOTIFY_CLIENT_ID in .env).")


def _client_id() -> str:
    return os.environ.get("SPOTIFY_CLIENT_ID", "").strip()


def connect() -> None:
    oauth.authorize("spotify", AUTH_URL, TOKEN_URL, _client_id(), SCOPES, PORT)


def _call(method: str, path: str, body: dict | None = None, **params):
    token = oauth.access_token("spotify", TOKEN_URL, _client_id())
    return oauth.api(method, f"{API}{path}", token, body, params or None)


def _device() -> str | None:
    """A device to play on: the active one, else the Spotify app on this PC (opening it if needed)."""
    for attempt in range(2):
        devices = (_call("GET", "/me/player/devices") or {}).get("devices", [])
        active = next((d for d in devices if d["is_active"]), None)
        if active:
            return active["id"]
        computer = next((d for d in devices if d["type"] == "Computer"), None) or (devices[0] if devices else None)
        if computer:
            return computer["id"]
        if attempt == 0:  # nothing open: start the Spotify app and give it a moment to register
            from tools import system

            system.open_app("Spotify")
            time.sleep(6)
    return None


def _friendly(fn):
    def wrapper(*args, **kwargs):
        if not _client_id():
            return NOT_CONNECTED
        try:
            return fn(*args, **kwargs)
        except oauth.NotConnected:
            return NOT_CONNECTED
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:200]
            if e.code == 403:
                return f"Spotify refused that ({detail}). Playback control needs Spotify Premium."
            if e.code == 404:
                return "Spotify has no active device. Open Spotify on the PC (or phone) and try again."
            return f"Spotify error {e.code}: {detail}"
    wrapper.__name__ = fn.__name__
    return wrapper


def _find_my_playlist(name: str) -> dict | None:
    wanted = name.lower().strip()
    offset = 0
    while offset < 500:
        page = _call("GET", "/me/playlists", limit=50, offset=offset) or {}
        for p in page.get("items", []):
            if p and wanted in (p.get("name") or "").lower():
                return p
        if not page.get("next"):
            return None
        offset += 50
    return None


@_friendly
def spotify_play(query: str = "", kind: str = "track") -> str:
    device = _device()
    if not device:
        return "I couldn't find Spotify running anywhere. Open the Spotify app and try again."
    if not query.strip():
        _call("PUT", "/me/player/play", device_id=device)
        return "Resumed playback."
    if kind == "liked":
        tracks = (_call("GET", "/me/tracks", limit=50) or {}).get("items", [])
        if not tracks:
            return "Your Liked Songs are empty."
        _call("PUT", "/me/player/play", {"uris": [t["track"]["uri"] for t in tracks]}, device_id=device)
        return "Playing your Liked Songs."
    if kind == "playlist":
        mine = _find_my_playlist(query)
        if mine:
            _call("PUT", "/me/player/play", {"context_uri": mine["uri"]}, device_id=device)
            return f"Playing your playlist {mine['name']}."
    found = _call("GET", "/search", q=query, type=kind, limit=1) or {}
    items = (found.get(f"{kind}s") or {}).get("items") or []
    items = [i for i in items if i]
    if not items:
        return f"Nothing on Spotify matching '{query}' ({kind})."
    item = items[0]
    if kind == "track":
        _call("PUT", "/me/player/play", {"uris": [item["uri"]]}, device_id=device)
        return f"Playing {item['name']} by {', '.join(a['name'] for a in item['artists'])}."
    _call("PUT", "/me/player/play", {"context_uri": item["uri"]}, device_id=device)
    return f"Playing the {kind} {item['name']}."


@_friendly
def spotify_control(action: str, value: str = "") -> str:
    if action == "pause":
        _call("PUT", "/me/player/pause")
        return "Paused."
    if action == "resume":
        _call("PUT", "/me/player/play")
        return "Resumed."
    if action == "next":
        _call("POST", "/me/player/next")
        return "Skipped."
    if action == "previous":
        _call("POST", "/me/player/previous")
        return "Went back."
    if action == "shuffle":
        on = value.lower() not in ("off", "false", "no")
        _call("PUT", "/me/player/shuffle", state=str(on).lower())
        return f"Shuffle {'on' if on else 'off'}."
    if action == "repeat":
        mode = value.lower() if value.lower() in ("track", "context", "off") else "context"
        _call("PUT", "/me/player/repeat", state=mode)
        return f"Repeat set to {mode}."
    if action == "volume":
        level = max(0, min(100, int(value or 50)))
        _call("PUT", "/me/player/volume", volume_percent=level)
        return f"Spotify volume {level}%."
    if action == "queue":
        found = (_call("GET", "/search", q=value, type="track", limit=1) or {}).get("tracks", {}).get("items") or []
        if not found:
            return f"No track matching '{value}'."
        _call("POST", "/me/player/queue", uri=found[0]["uri"])
        return f"Queued {found[0]['name']} by {found[0]['artists'][0]['name']}."
    return f"Unknown Spotify action '{action}'."


@_friendly
def spotify_now_playing() -> str:
    now = _call("GET", "/me/player")
    if not now or not now.get("item"):
        return "Nothing is playing on Spotify."
    item = now["item"]
    who = ", ".join(a["name"] for a in item.get("artists", [])) or item.get("show", {}).get("name", "")
    state = "Playing" if now.get("is_playing") else "Paused"
    return f"{state}: {item['name']} by {who} on {now['device']['name']}."


@_friendly
def spotify_playlists() -> str:
    page = _call("GET", "/me/playlists", limit=50) or {}
    names = [p["name"] for p in page.get("items", []) if p]
    return "Your playlists: " + ", ".join(names) if names else "You have no playlists."


TOOLS = [
    {
        "name": "spotify_play",
        "description": "Play music on Spotify: a song, the user's own playlist, an album, an artist, or their Liked "
                       "Songs (kind='liked'). With no query, resumes. Prefer this over media_control for anything "
                       "specific. Note: Spotify's own mixes (Discover Weekly, Daily Mix) may not be playable this way.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "e.g. 'Blinding Lights', 'lofi beats', 'Drake'"},
                "kind": {"type": "string", "enum": ["track", "playlist", "album", "artist", "liked"]}
            },
            "required": []
        }
    },
    {
        "name": "spotify_control",
        "description": "Control Spotify playback: pause, resume, next, previous, shuffle (value on/off), repeat "
                       "(value track/context/off), volume (value 0-100), queue (value = a song to add next).",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "enum": ["pause", "resume", "next", "previous", "shuffle", "repeat", "volume", "queue"]},
                "value": {"type": "string"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "spotify_now_playing",
        "description": "What's playing on Spotify right now.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "spotify_playlists",
        "description": "List the user's Spotify playlists.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
]

TOOL_MAP = {"spotify_play": spotify_play, "spotify_control": spotify_control,
            "spotify_now_playing": spotify_now_playing, "spotify_playlists": spotify_playlists}
