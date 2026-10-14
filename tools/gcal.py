"""Google Calendar: what's on, add / move / delete events.

Setup (once): in Google Cloud Console create a project, enable the Google Calendar API, create an OAuth
client of type "Desktop app", put its ID and secret in .env as GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET,
then run `.venv/Scripts/python.exe connect.py google`.
"""
import os
import urllib.error
from datetime import date, datetime, timedelta

from tools import oauth

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/calendar/v3"
PORT = 8889
SCOPES = ["https://www.googleapis.com/auth/calendar.events", "https://www.googleapis.com/auth/calendar.readonly"]
NOT_CONNECTED = ("Google Calendar isn't connected yet. On the PC run: .venv/Scripts/python.exe connect.py google "
                 "(needs GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env).")


def _creds() -> tuple[str, str]:
    return os.environ.get("GOOGLE_CLIENT_ID", "").strip(), os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()


def connect() -> None:
    client_id, secret = _creds()
    # offline + consent so Google returns a refresh token (otherwise access lasts only an hour)
    oauth.authorize("google", AUTH_URL, TOKEN_URL, client_id, SCOPES, PORT, client_secret=secret,
                    extra={"access_type": "offline", "prompt": "consent"})


def _call(method: str, path: str, body: dict | None = None, **params):
    client_id, secret = _creds()
    token = oauth.access_token("google", TOKEN_URL, client_id, secret)
    return oauth.api(method, f"{API}{path}", token, body, params or None)


def _friendly(fn):
    def wrapper(*args, **kwargs):
        if not all(_creds()):
            return NOT_CONNECTED
        try:
            return fn(*args, **kwargs)
        except oauth.NotConnected:
            return NOT_CONNECTED + " (If it worked before, Google may have expired the sign-in: just connect again.)"
        except urllib.error.HTTPError as e:
            return f"Google Calendar error {e.code}: {e.read().decode('utf-8', 'replace')[:200]}"
        except ValueError as e:
            return f"Couldn't read that date or time ({e}). Use ISO format like 2026-10-16T14:00."
    wrapper.__name__ = fn.__name__
    return wrapper


def _local(dt: datetime) -> datetime:
    return dt.astimezone() if dt.tzinfo else dt.astimezone()  # naive times are taken as this PC's local time


def _when(event: dict) -> str:
    start = event.get("start", {})
    if "date" in start:
        return f"{date.fromisoformat(start['date']):%a %b %d} (all day)"
    begin = datetime.fromisoformat(start["dateTime"]).astimezone()
    end = datetime.fromisoformat(event["end"]["dateTime"]).astimezone()
    return f"{begin:%a %b %d, %I:%M %p}".replace(" 0", " ") + f" to {end:%I:%M %p}".replace(" 0", " ")


def _describe(event: dict) -> str:
    where = f" @ {event['location']}" if event.get("location") else ""
    return f"[{event['id'][:10]}] {_when(event)}: {event.get('summary', '(no title)')}{where}"


def _events(start: datetime, end: datetime, query: str = "") -> list[dict]:
    found = _call("GET", "/calendars/primary/events", timeMin=start.isoformat(), timeMax=end.isoformat(),
                  singleEvents="true", orderBy="startTime", maxResults=50, q=query or None) or {}
    return found.get("items", [])


def _match(query: str, around: str = "") -> dict | list[dict]:
    """The one event the user means, searched over the next 60 days (or around a given date)."""
    start = _local(datetime.fromisoformat(around)) - timedelta(days=1) if around else datetime.now().astimezone()
    events = _events(start, start + timedelta(days=60), query)
    wanted = query.lower()
    exact = [e for e in events if e["id"].startswith(query)] or \
            [e for e in events if wanted in e.get("summary", "").lower()]
    return exact[0] if len(exact) == 1 else exact


@_friendly
def calendar_events(start_date: str = "", days: int = 1, query: str = "") -> str:
    first = date.fromisoformat(start_date) if start_date else date.today()
    start = datetime.combine(first, datetime.min.time()).astimezone()
    days = max(1, min(int(days or 1), 31))
    events = _events(start, start + timedelta(days=days), query)
    span = f"{first:%A %b %d}" + (f" + {days - 1} days" if days > 1 else "")
    if not events:
        return f"Nothing on your calendar for {span}" + (f" matching '{query}'." if query else ".")
    return f"Calendar, {span}:\n" + "\n".join(_describe(e) for e in events)


@_friendly
def calendar_add_event(title: str, start: str, end: str = "", duration_minutes: int = 60,
                       all_day: bool = False, location: str = "", description: str = "") -> str:
    body = {"summary": title, "location": location or None, "description": description or None}
    if all_day:
        day = date.fromisoformat(start[:10])
        body["start"] = {"date": day.isoformat()}
        body["end"] = {"date": (date.fromisoformat(end[:10]) if end else day + timedelta(days=1)).isoformat()}
    else:
        begin = _local(datetime.fromisoformat(start))
        finish = _local(datetime.fromisoformat(end)) if end else begin + timedelta(minutes=int(duration_minutes or 60))
        body["start"], body["end"] = {"dateTime": begin.isoformat()}, {"dateTime": finish.isoformat()}
    event = _call("POST", "/calendars/primary/events", {k: v for k, v in body.items() if v is not None})
    return f"Added: {_describe(event)}"


@_friendly
def calendar_update_event(event: str, new_start: str = "", new_end: str = "", new_title: str = "",
                          near_date: str = "") -> str:
    found = _match(event, near_date)
    if not isinstance(found, dict):
        return _ambiguous(event, found)
    patch = {}
    if new_title:
        patch["summary"] = new_title
    if new_start and "dateTime" in found["start"]:
        old_begin = datetime.fromisoformat(found["start"]["dateTime"])
        old_end = datetime.fromisoformat(found["end"]["dateTime"])
        begin = _local(datetime.fromisoformat(new_start))
        finish = _local(datetime.fromisoformat(new_end)) if new_end else begin + (old_end - old_begin)
        patch["start"], patch["end"] = {"dateTime": begin.isoformat()}, {"dateTime": finish.isoformat()}
    if not patch:
        return "Tell me what to change: a new time or a new title."
    updated = _call("PATCH", f"/calendars/primary/events/{found['id']}", patch)
    return f"Updated: {_describe(updated)}"


@_friendly
def calendar_delete_event(event: str, near_date: str = "") -> str:
    found = _match(event, near_date)
    if not isinstance(found, dict):
        return _ambiguous(event, found)
    _call("DELETE", f"/calendars/primary/events/{found['id']}")
    return f"Deleted: {_describe(found)}"


def _ambiguous(query: str, matches: list[dict]) -> str:
    if not matches:
        return f"No upcoming event matching '{query}' in the next 60 days."
    return ("Several events match; say which one (by its [id] or a more specific name):\n"
            + "\n".join(_describe(e) for e in matches[:8]))


def confirmation(name: str, inputs: dict) -> str | None:
    if name != "calendar_delete_event":
        return None
    try:
        found = _match(inputs.get("event", ""), inputs.get("near_date", ""))
    except Exception:
        return f"Delete the calendar event '{inputs.get('event')}'?"
    if isinstance(found, dict):
        return f"Delete '{found.get('summary', '(no title)')}' on {_when(found)} from your calendar?"
    return None  # nothing (or several) match: the tool just reports that, nothing is deleted


ISO = "Local date/time, ISO 8601, e.g. 2026-10-16T14:00"
TOOLS = [
    {
        "name": "calendar_events",
        "description": "What's on the user's Google Calendar: a day (default today) or several days, optionally "
                       "filtered by words. Each event has an [id] for updates/deletes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD (default today)"},
                "days": {"type": "integer", "description": "How many days from start_date (1-31, default 1)"},
                "query": {"type": "string"}
            },
            "required": []
        }
    },
    {
        "name": "calendar_add_event",
        "description": "Add an event to the user's Google Calendar (no guests are invited).",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"}, "start": {"type": "string", "description": ISO},
                "end": {"type": "string", "description": "Optional; otherwise duration_minutes"},
                "duration_minutes": {"type": "integer"}, "all_day": {"type": "boolean"},
                "location": {"type": "string"}, "description": {"type": "string"}
            },
            "required": ["title", "start"]
        }
    },
    {
        "name": "calendar_update_event",
        "description": "Move an event to a new time (keeping its length unless new_end is given) and/or rename it. "
                       "`event` is its [id] or words from its title.",
        "input_schema": {
            "type": "object",
            "properties": {
                "event": {"type": "string"}, "new_start": {"type": "string", "description": ISO},
                "new_end": {"type": "string"}, "new_title": {"type": "string"},
                "near_date": {"type": "string", "description": "Roughly when it is, if it's far off"}
            },
            "required": ["event"]
        }
    },
    {
        "name": "calendar_delete_event",
        "description": "Delete an event (the user confirms first). `event` is its [id] or words from its title.",
        "input_schema": {
            "type": "object",
            "properties": {"event": {"type": "string"}, "near_date": {"type": "string"}},
            "required": ["event"]
        }
    },
]

TOOL_MAP = {"calendar_events": calendar_events, "calendar_add_event": calendar_add_event,
            "calendar_update_event": calendar_update_event, "calendar_delete_event": calendar_delete_event}
