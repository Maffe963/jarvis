"""What the dashboard's tabs show besides the live map: tasks, Claude Code sessions, Claude usage, projects,
the PC's processes, and what Jarvis remembers. Everything here only reads; nothing is changed.

The plan's usage limits (the 5-hour and weekly windows Claude Code's /usage shows) come from Anthropic's usage
endpoint, signed in with Claude Code's own login on this PC (~/.claude/.credentials.json). The token is only ever
sent to api.anthropic.com. Set JARVIS_PLAN_USAGE=off in .env to turn that off.
"""
import json
import os
import re
import threading
import time
import urllib.request
from datetime import datetime, timedelta

import psutil

from memory.conversation import HISTORY_FILE
from team import coding, messages, standing
from tools import background, facts, lists, reminders, usage

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECTS_FILE = os.path.join(ROOT, "projects.json")
CREDENTIALS = os.path.join(os.path.expanduser("~"), ".claude", ".credentials.json")
ACTIVE_S = 15 * 60  # a Claude Code session touched this recently counts as active now
LIMIT_NAMES = {"five_hour": "5-hour window", "seven_day": "Weekly, all models", "seven_day_opus": "Weekly, Opus",
               "seven_day_sonnet": "Weekly, Sonnet", "seven_day_oauth_apps": "Weekly, apps"}

_cache: dict[str, tuple[float, object]] = {}
_cache_lock = threading.Lock()
_procs: dict[int, psutil.Process] = {}  # kept between calls so CPU use is measured over the time between them


def _cached(key: str, seconds: float, make):
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < seconds:
            return hit[1]
    value = make()
    with _cache_lock:
        _cache[key] = (time.time(), value)
    return value


def _json_file(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "just now"
    for size, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if seconds >= size:
            return f"{seconds // size}{unit} ago"
    return "just now"


def _when(stamp: str) -> float:
    """A transcript's UTC ISO timestamp as a Unix time (0 if it can't be read)."""
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0


# ---------- tasks ----------

def tasks(team: dict) -> dict:
    agents = team.get("agents", [])
    working = [a for a in agents if a.get("status") not in ("done", "stopped", "failed")]
    day_ago = time.time() - 86400
    finished = [a for a in agents if a.get("status") in ("done", "stopped", "failed")
                and _started(a) >= day_ago][-10:]
    running = [{"title": t.get("title", ""), "task": t.get("task", ""), "status": t.get("status", "running"),
                "started": t.get("started", ""), "steps": len(t.get("log", []))}
               for t in list(background._tasks.values())]
    done = [{k: r.get(k, "") for k in ("title", "status", "started", "finished")} for r in background._load()[-6:]][::-1]
    upcoming = [r for r in reminders._load() if r.get("due", "") >= datetime.now().isoformat(timespec="minutes")]
    upcoming.sort(key=lambda r: r.get("due", ""))
    return {"agents": working, "finished": finished[::-1], "background": running, "background_done": done,
            "scheduled": standing.next_runs()[:14], "reminders": upcoming[:10],
            "approvals": len(team.get("approvals", []))}


def _started(agent: dict) -> float:
    started = agent.get("started")
    return float(started) if isinstance(started, (int, float)) else time.time()  # unknown: show it


# ---------- Claude Code sessions ----------

def _claude_processes() -> list[dict]:
    found = []
    for p in psutil.process_iter(["pid", "name", "create_time", "memory_info"]):
        try:
            name = (p.info["name"] or "").lower()
            if name not in ("claude", "claude.exe") and not (name.startswith("node") and _is_claude(p)):
                continue
            cmd = p.cmdline()
            try:
                label = p.environ().get("JARVIS_SESSION", "")
            except (psutil.AccessDenied, psutil.ZombieProcess, OSError):
                label = ""
            try:
                cwd = p.cwd()
            except (psutil.AccessDenied, OSError):
                cwd = ""
            found.append({"pid": p.info["pid"], "who": label or ("You (Claude Code)" if "-p" not in cmd else "Claude Code (script)"),
                          "jarvis": bool(label), "folder": "" if label else cwd,
                          "started": p.info["create_time"], "uptime": _ago(time.time() - p.info["create_time"]).replace(" ago", ""),
                          "memory_mb": round((p.info["memory_info"].rss if p.info["memory_info"] else 0) / 1048576)})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return sorted(found, key=lambda x: (x["jarvis"], -x["started"]))


def _is_claude(p: psutil.Process) -> bool:
    try:
        return any("claude-code" in part or part.endswith(("claude", "claude.js")) for part in p.cmdline()[1:3])
    except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
        return False


def _session_row(s: dict, now: float) -> dict:
    last = _when(s["last"])
    today = usage.session_tokens(s, usage.iso_since((now - _midnight(now)) / 3600))
    total = usage.session_tokens(s)
    return {"id": s["id"], "title": s["summary"] or s["title"] or "(no prompt yet)", "folder": s["cwd"],
            "project": os.path.basename(s["cwd"].rstrip("\\/")) if s["cwd"] else "?", "branch": s["branch"],
            "model": s["model"], "prompts": s["prompts"], "last": _ago(now - last), "active": now - last < ACTIVE_S,
            "started": _ago(now - _when(s["first"])), "tokens_today": today["in"] + today["out"],
            "tokens": total["in"] + total["out"], "cached": total["cached"], "calls": total["calls"]}


def _midnight(now: float) -> float:
    return datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def sessions(team: dict) -> dict:
    now = time.time()
    transcripts = [_session_row(s, now) for s in usage.claude_code_sessions()[:40]]
    agents = [a for a in team.get("agents", []) if a.get("status") in ("starting", "working")]
    return {"processes": _claude_processes(), "active": [t for t in transcripts if t["active"]],
            "recent": [t for t in transcripts if not t["active"]][:20],
            "agents_working": [{"name": a["name"], "kind": a.get("kind", "agent"), "doing": a.get("doing", ""),
                                "task": a.get("task", "")} for a in agents],
            "background": [{"title": t.get("title", "")} for t in list(background._tasks.values())]}


# ---------- usage ----------

def _plan_usage() -> dict:
    """{"windows": [...]} from Anthropic's usage endpoint, or {"error": why} when it isn't available."""
    if os.environ.get("JARVIS_PLAN_USAGE", "").strip().lower() in ("off", "0", "no", "false"):
        return {"error": "turned off (JARVIS_PLAN_USAGE=off)"}
    oauth = _json_file(CREDENTIALS, {}).get("claudeAiOauth") or {}
    token = oauth.get("accessToken")
    if not token:
        return {"error": "Claude Code's login wasn't found on this PC"}
    if oauth.get("expiresAt") and oauth["expiresAt"] / 1000 < time.time():
        return {"error": "Claude Code's login has expired; it renews the next time Claude Code runs"}
    req = urllib.request.Request("https://api.anthropic.com/api/oauth/usage", headers={
        "Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20", "User-Agent": "jarvis-dashboard"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        return {"error": f"couldn't get it ({getattr(e, 'code', '') or type(e).__name__})"}
    windows = []
    for key, value in data.items() if isinstance(data, dict) else []:
        if isinstance(value, dict) and isinstance(value.get("utilization"), (int, float)):
            windows.append({"key": key, "name": LIMIT_NAMES.get(key, key.replace("_", " ").capitalize()),
                            "percent": round(float(value["utilization"]), 1), "resets": value.get("resets_at") or ""})
    order = list(LIMIT_NAMES)
    windows.sort(key=lambda w: order.index(w["key"]) if w["key"] in order else 99)
    return {"windows": windows} if windows else {"error": "no usage figures in the reply"}


def plan_usage() -> dict:
    return _cached("plan", 300, _plan_usage)


def usage_report() -> dict:
    now = time.time()
    midnight = _midnight(now)
    days = [midnight - 86400 * n for n in range(6, -1, -1)]
    labels = [datetime.fromtimestamp(d).strftime("%a") for d in days]
    jarvis = usage.jarvis_entries(days[0])
    by_day = {"jarvis": [0] * 7, "you": [0] * 7}
    who: dict[str, dict] = {}
    totals = {"jarvis": {"today": 0, "5h": 0, "week": 0, "turns_today": 0}, "you": {"today": 0, "5h": 0, "week": 0, "turns_today": 0}}
    for e in jarvis:
        n = e.get("in", 0) + e.get("out", 0)
        slot = min(6, int((e["t"] - days[0]) // 86400))
        by_day["jarvis"][slot] += n
        row = who.setdefault(e.get("who") or "Jarvis", {"today": 0, "week": 0, "turns": 0})
        row["week"] += n
        if e["t"] >= midnight:
            row["today"] += n
            row["turns"] += 1
            totals["jarvis"]["today"] += n
            totals["jarvis"]["turns_today"] += 1
        if e["t"] >= now - 5 * 3600:
            totals["jarvis"]["5h"] += n
        totals["jarvis"]["week"] += n
    for s in usage.claude_code_sessions():
        for when, t in s["messages"].values():
            stamp = _when(when)
            if stamp < days[0]:
                continue
            n = t["in"] + t["out"]
            by_day["you"][min(6, int((stamp - days[0]) // 86400))] += n
            totals["you"]["week"] += n
            if stamp >= midnight:
                totals["you"]["today"] += n
                totals["you"]["turns_today"] += 1
            if stamp >= now - 5 * 3600:
                totals["you"]["5h"] += n
    ranked = sorted(who.items(), key=lambda kv: -kv[1]["week"])
    return {"plan": plan_usage(), "limits": usage.limits(), "days": labels, "by_day": by_day, "totals": totals,
            "by_who": [{"who": k, **v} for k, v in ranked[:12]]}


def summary() -> dict:
    """For the header: the plan's tightest window, or today's tokens if that isn't available."""
    plan = plan_usage()
    windows = plan.get("windows") or []
    top = max(windows, key=lambda w: w["percent"]) if windows else None
    totals = _cached("report", 20, usage_report)["totals"] if top is None else None
    return {"window": top, "today": totals["jarvis"]["today"] + totals["you"]["today"] if totals else 0}


# ---------- projects ----------

def _git_info(folder: str) -> dict | None:
    if not os.path.isdir(os.path.join(folder, ".git")) and not os.path.isfile(os.path.join(folder, ".git")):
        return None

    def run(*args):
        code, out = coding._git(folder, *args, timeout=10)
        return out if code == 0 else ""

    commits = []
    for line in run("log", "--since=14.days", "-n", "8", "--pretty=%h\x1f%an\x1f%at\x1f%s").splitlines():
        parts = line.split("\x1f")
        if len(parts) == 4:
            commits.append({"sha": parts[0], "author": parts[1], "ago": _ago(time.time() - int(parts[2])), "subject": parts[3][:120]})
    status = [l for l in run("status", "--porcelain").splitlines() if l.strip()]
    agent_branches = [b.strip("* +").strip() for b in run("branch", "--list", coding.BRANCH_PREFIX + "*").splitlines() if b.strip()]
    return {"branch": run("rev-parse", "--abbrev-ref", "HEAD").strip(), "commits": commits,
            "week": int(run("rev-list", "--count", "--since=7.days", "HEAD").strip() or 0),
            "changed": len(status), "agent_branches": agent_branches[:10], "github": coding._github_repo(folder) or ""}


def _open_prs(slug: str) -> list[dict] | None:
    if not slug or not os.environ.get("GITHUB_TOKEN", "").strip():
        return None

    def get():
        try:
            pulls = coding._github("GET", f"/repos/{slug}/pulls?state=open&per_page=15")
        except Exception:
            return None
        return [{"number": p["number"], "title": p["title"][:120], "branch": p["head"]["ref"], "draft": p.get("draft", False),
                 "url": p["html_url"], "team": p["head"]["ref"].startswith(coding.BRANCH_PREFIX)} for p in pulls]
    return _cached(f"prs:{slug}", 180, get)


def _same_folder(a: str, b: str) -> bool:
    norm = lambda p: os.path.normcase(os.path.normpath(p)).rstrip("\\/")  # noqa: E731
    return bool(a and b) and (norm(a) == norm(b) or norm(a).startswith(norm(b) + os.sep))


def projects(team: dict) -> list[dict]:
    def make():
        found: list[dict] = []
        seen: list[str] = []

        def add(name: str, folder: str, kind: str, description: str = "", commands: list | None = None):
            if any(_same_folder(folder, s) and _same_folder(s, folder) for s in seen):
                return
            seen.append(folder)
            found.append({"name": name, "folder": folder, "kind": kind, "description": description,
                          "commands": commands or [], "git": _git_info(folder) if os.path.isdir(folder) else None})

        add("Jarvis", ROOT, "jarvis", "This assistant")
        for name, spec in coding._repos().items():
            if spec.get("folder"):
                add(name, spec["folder"], "repo", spec.get("description", ""))
        for name, spec in _json_file(PROJECTS_FILE, {}).items():
            if isinstance(spec, dict) and spec.get("folder"):
                add(name, spec["folder"], "project", spec.get("description", ""), sorted(spec.get("commands", {})))
        # folders the user has worked on with Claude Code that aren't listed anywhere
        for s in usage.claude_code_sessions():
            folder = s["cwd"]
            if folder and not any(_same_folder(folder, f) for f in seen) and os.path.isdir(folder) and len(found) < 14:
                add(os.path.basename(folder.rstrip("\\/")) or folder, folder, "claude", "Worked on with Claude Code")
        return found

    listed = _cached("projects", 60, make)
    now = time.time()
    sessions_ = usage.claude_code_sessions()
    out = []
    for p in listed:
        mine = [s for s in sessions_ if _same_folder(s["cwd"], p["folder"])]
        agents = [{"name": a["name"], "status": a.get("status", ""), "kind": a.get("kind", ""), "task": a.get("task", "")}
                  for a in team.get("agents", []) if a.get("repo") and a["repo"].lower() == p["name"].lower()
                  and a.get("status") not in ("stopped",)]
        git = p["git"] or {}
        last = max([_when(s["last"]) for s in mine] or [0])
        out.append({**p, "prs": _open_prs(git.get("github", "")), "agents": agents,
                    "sessions": [{"title": s["summary"] or s["title"], "last": _ago(now - _when(s["last"])),
                                  "active": now - _when(s["last"]) < ACTIVE_S} for s in mine[:4]],
                    "session_count": len(mine), "last_session": _ago(now - last) if last else ""})
    out.sort(key=lambda p: (p["kind"] != "jarvis", not (p["agents"] or any(s["active"] for s in p["sessions"])),
                            -(p["git"] or {}).get("week", 0)))
    return out


# ---------- the PC ----------

def system() -> dict:
    cpus = psutil.cpu_count() or 1
    mine = {os.getpid()}
    service = _json_file(os.path.join(messages.FOLDER, "service.json"), {}).get("pid")
    labels = {os.getpid(): "Jarvis"}
    if service:
        labels[service] = "Team service"
    for root in list(labels):
        try:
            for child in psutil.Process(root).children(recursive=True):
                mine.add(child.pid)
        except psutil.Error:
            continue
    mine |= set(labels)
    rows = []
    alive = set()
    for p in psutil.process_iter(["pid", "name", "memory_info"]):
        pid = p.info["pid"]
        if pid == 0 or (p.info["name"] or "").lower() in ("system idle process", "idle"):
            continue  # Windows counts idle time as a process
        alive.add(pid)
        cached = _procs.setdefault(pid, p)
        try:
            cpu = cached.cpu_percent(None) / cpus
        except psutil.Error:
            continue
        mem = p.info["memory_info"].rss if p.info["memory_info"] else 0
        rows.append({"pid": pid, "name": p.info["name"] or "?", "cpu": round(cpu, 1), "memory_mb": round(mem / 1048576),
                     "jarvis": pid in mine, "label": labels.get(pid, "")})
    for pid in set(_procs) - alive:
        _procs.pop(pid, None)
    vm, boot = psutil.virtual_memory(), psutil.boot_time()
    disk_root = os.path.splitdrive(ROOT)[0] + os.sep if os.name == "nt" else "/"
    try:
        du = psutil.disk_usage(disk_root)
        disk = {"path": disk_root, "percent": du.percent, "free_gb": round(du.free / 1e9, 1), "total_gb": round(du.total / 1e9)}
    except OSError:
        disk = None
    battery = None
    try:
        b = psutil.sensors_battery()
        if b:
            battery = {"percent": round(b.percent), "plugged": bool(b.power_plugged)}
    except (AttributeError, OSError, NotImplementedError):
        pass
    top_cpu = sorted(rows, key=lambda r: -r["cpu"])[:10]
    top_mem = sorted(rows, key=lambda r: -r["memory_mb"])[:10]
    jarvis = sorted([r for r in rows if r["jarvis"]], key=lambda r: (not r["label"], -r["memory_mb"]))
    return {"cpu": psutil.cpu_percent(None), "cores": cpus, "memory": {"percent": vm.percent, "used_gb": round(vm.used / 1e9, 1),
            "total_gb": round(vm.total / 1e9, 1)}, "disk": disk, "battery": battery, "uptime": _ago(time.time() - boot).replace(" ago", ""),
            "processes": len(rows), "top_cpu": top_cpu, "top_memory": top_mem, "jarvis": jarvis[:16],
            "jarvis_memory_mb": sum(r["memory_mb"] for r in jarvis)}


# ---------- memory ----------

def memory() -> dict:
    notebooks = []
    for name in sorted(standing.definitions()):
        path = os.path.join(standing.NOTEBOOKS, f"{standing._slug(name)}.md")
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            updated = _ago(time.time() - os.path.getmtime(path))
        except FileNotFoundError:
            text, updated = "", ""
        notebooks.append({"agent": name, "chars": len(text), "updated": updated, "text": text[-6000:]})
    jobs = _json_file(standing.JOBS_FILE, {})
    statuses: dict[str, int] = {}
    for job in jobs.values() if isinstance(jobs, dict) else []:
        statuses[job.get("status", "?")] = statuses.get(job.get("status", "?"), 0) + 1
    trips_file = getattr(standing, "TRIPS_FILE", "")
    trips = _json_file(trips_file, {}) if trips_file else {}
    holdings = _json_file(standing.PORTFOLIO_FILE, {})
    history = _json_file(HISTORY_FILE, [])
    return {"facts": facts._load()[::-1], "lists": lists._load(), "reminders": reminders._load(),
            "notebooks": notebooks, "conversation_turns": len(history) if isinstance(history, list) else 0,
            "trackers": {"jobs": statuses, "trips": [{"name": n, "status": t.get("status", "planning"), "destination": t.get("destination", "")}
                                                      for n, t in trips.items()] if isinstance(trips, dict) else [],
                         "holdings": len(holdings) if isinstance(holdings, dict) else 0}}


SECTIONS = {"tasks": tasks, "sessions": sessions, "projects": projects}  # these need the team's state
PLAIN = {"usage": usage_report, "system": system, "memory": memory, "summary": summary}


def get(tab: str, team: dict) -> object:
    if tab in SECTIONS:
        return SECTIONS[tab](team)
    if tab in PLAIN:
        return PLAIN[tab]()
    raise KeyError(tab)
