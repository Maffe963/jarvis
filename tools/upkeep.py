"""What's changed in Jarvis and what's planned (changes.json, the dashboard's Changes tab), the 9am morning report
in the dashboard feed, and the setup jobs a planned item asks Jarvis to run at its time.

A planned item with "run" (e.g. ["update", "addons"]) is done by Jarvis himself at its "when", or as soon as he's
on after that: "update" installs what's new on GitHub, "addons" runs setup_addons.py. Only those steps exist; the
file can't make Jarvis run anything else. If an update came in, Jarvis restarts so it takes effect.

JARVIS_MORNING_REPORT: the report's time (default 09:00), or "off".
"""
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHANGES_FILE = os.path.join(ROOT, "changes.json")
STATE_FILE = os.path.join(ROOT, "memory", "upkeep.json")
CHECK_EVERY_S = 60
STEPS = ("update", "addons")
GIVE_UP_AFTER = timedelta(days=3)  # a job missed by more than this (the PC was off) is left for the user

_lock = threading.Lock()


def changes() -> dict:
    try:
        with open(CHANGES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE + ".tmp", "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(STATE_FILE + ".tmp", STATE_FILE)


def _job_id(item: dict) -> str:
    """A job's own "id" (so fixing a typo in its title doesn't run it again), else its time and title."""
    return str(item.get("id") or f"{item.get('when', '')} {item.get('title', '')}")


def _when(item: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(item.get("when") or "")
    except ValueError:
        return None


def overview() -> dict:
    """For the Changes tab: the lists, with each scheduled job's outcome so far."""
    data = changes()
    jobs = _state().get("jobs", {})
    planned = [{**item, "outcome": jobs.get(_job_id(item))} for item in data.get("planned", [])]
    return {"done": data.get("done", []), "planned": planned}


# ---------- the morning report ----------

def _report_time() -> tuple[int, int] | None:
    value = os.environ.get("JARVIS_MORNING_REPORT", "09:00").strip().lower()
    if value in ("off", "no", "0", ""):
        return None
    try:
        hour, minute = (int(x) for x in value.split(":"))
        return hour, minute
    except ValueError:
        return 9, 0


def _fingerprint(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]


def report_text(data: dict, since: list[str] | None, first: bool = False) -> str:
    new = [d for d in data.get("done", []) if d.get("title") not in (since or [])]
    lines = ["📋 Morning report: what's changed in Jarvis and what's planned."]
    if first:  # the first one: the latest few, not the whole history
        recent = data.get("done", [])[:5]
        lines.append(f"{len(data.get('done', []))} changes so far; the latest: " + "; ".join(d["title"] for d in recent) + ".")
    elif new:
        lines.append("New: " + "; ".join(d["title"] for d in new[:6]) + (f"; and {len(new) - 6} more" if len(new) > 6 else "") + ".")
    upcoming = []
    for item in data.get("planned", []):
        when = _when(item)
        if when is None or (item.get("run") and _job_id(item) in _state().get("jobs", {})):
            continue  # to-dos, things on hold and jobs already run: in the Changes tab
        if when.date() < datetime.now().date():
            continue  # past dates nobody has moved yet
        label = when.strftime("%a %I:%M %p").replace(" 0", " ") if when.hour or when.minute else when.strftime("%a %b %d")
        upcoming.append(f"{item.get('title', '')} ({label})")
    if upcoming:
        lines.append("Planned: " + "; ".join(upcoming[:6]) + ".")
    lines.append("Full details in the Changes tab.")
    return "\n".join(lines)


def _morning(post, now: datetime) -> None:
    at = _report_time()
    if at is None or (now.hour, now.minute) < at:
        return
    today = now.date().isoformat()
    with _lock:
        state = _state()
        report = state.get("report", {})
        if report.get("date") == today:
            return
        data = changes()
        fingerprint = _fingerprint(data)
        state["report"] = {"date": today, "fingerprint": fingerprint,
                           "titles": [d.get("title") for d in data.get("done", [])]}
        _save(state)
    if data and fingerprint != report.get("fingerprint"):  # only when something changed since the last one
        post(report_text(data, report.get("titles"), first=not report))


# ---------- scheduled setup jobs ----------

def _addons() -> tuple[bool, str]:
    script = os.path.join(ROOT, "setup_addons.py")
    if not os.path.exists(script):
        return False, "setup_addons.py isn't here yet (its update hasn't been merged on GitHub)"
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    python = sys.executable.replace("pythonw.exe", "python.exe")
    try:
        result = subprocess.run([python, script, "--unattended"], cwd=ROOT, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=1800, creationflags=flags,
                                env={**os.environ, "PYTHONUTF8": "1"})
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"the add-on setup didn't finish ({e})"
    summary = next((line[8:] for line in result.stdout.splitlines() if line.startswith("SUMMARY ")), "")
    if not summary:
        tail = (result.stdout + result.stderr).strip().splitlines()[-3:]
        return False, "the add-on setup didn't finish: " + " / ".join(tail)
    try:
        outcome = json.loads(summary)
    except ValueError:
        return False, "the add-on setup's summary was unreadable"
    text = f"{len(outcome['done'])} setup steps done (skills, plugins, status line, hooks)"
    if outcome["todo"]:
        text += ". Left for you: " + "; ".join(outcome["todo"])
    return True, text


def _run(item: dict) -> tuple[str, str, bool]:
    """(status, what happened, whether Jarvis should restart)."""
    from tools import updater

    notes, restart = [], False
    for step in item.get("run", []):
        if step not in STEPS:
            return "failed", f"unknown step '{step}' (only {', '.join(STEPS)} exist)", False
        if step == "update":
            try:
                new, note = updater.pull()
            except updater.UpdateProblem as e:  # local changes need the user; a network blip just needs time
                return ("waiting" if e.retry else "failed"), f"couldn't update: {e}", restart
            except Exception as e:
                return "waiting", f"couldn't update: {e}", restart
            restart = restart or bool(new)
            notes.append(f"installed {len(new)} update(s){note}" if new else "already up to date")
        elif step == "addons":
            ok, text = _addons()
            if not ok:
                return "waiting", "; ".join(notes + [text]), restart
            notes.append(text)
    return "done", "; ".join(notes) or "done", restart


def _jobs(post, now: datetime) -> None:
    for item in changes().get("planned", []):
        when = _when(item)
        if not item.get("run") or when is None or now < when:
            continue
        key = _job_id(item)
        with _lock:
            state = _state()
            job = state.setdefault("jobs", {}).get(key) or {}
            if job.get("status") in ("done", "failed", "skipped"):
                continue
            if job.get("status") == "waiting" and time.time() - job.get("tried", 0) < 1800:
                continue  # try again every half hour
            if now - when > GIVE_UP_AFTER:
                state["jobs"][key] = {"status": "skipped", "detail": "missed while the PC was off; run it yourself"}
                _save(state)
                continue
        try:
            status, detail, restart = _run(item)
        except Exception as e:  # never let one job break the loop or run again every minute
            status, detail, restart = "waiting", f"something went wrong: {e}", False
        with _lock:
            state = _state()
            state.setdefault("jobs", {})[key] = {"status": status, "detail": detail, "tried": time.time(),
                                                 "at": now.isoformat(timespec="minutes")}
            _save(state)
        icon = {"done": "✅", "failed": "⚠️", "waiting": "⏳"}[status]
        message = f"{icon} {item.get('title', 'Scheduled job')}: {detail}" + (
            ". I'll try again in half an hour." if status == "waiting" else ".")
        if status == "waiting" and job.get("status") == "waiting":
            continue  # already told them it's waiting
        if restart:
            from tools import updater

            post(message + " Restarting to finish.", phone=False)
            updater._restart_after_reply(message)  # the new Jarvis sends this to the phone when it's back
            return
        post(message, phone=True)


# ---------- running it ----------

def _post(text: str, phone: bool = False) -> None:
    from team import events

    print(f"[upkeep] {text}")
    events.emit("jarvis", direction="out", text=text)
    if phone:
        import telegram_bot

        if telegram_bot.active is not None:
            telegram_bot.active.send(text)


_started = False


def start(busy=lambda: False) -> None:
    """Every minute: the morning report when it's due, and any setup job that's due (only while `busy()` is false,
    so a job's restart never lands in the middle of a request)."""
    global _started
    if _started:
        return
    _started = True

    def loop():
        time.sleep(20)  # let Jarvis finish starting
        while True:
            now = datetime.now()
            for task in (_morning,) if busy() else (_jobs, _morning):
                try:
                    task(_post, now)
                except Exception as e:
                    print(f"[upkeep] {e}")
            time.sleep(CHECK_EVERY_S)

    threading.Thread(target=loop, daemon=True).start()
