"""How much Claude gets used: every Claude Code turn Jarvis, its background tasks and its agents run (by who ran
it), the plan's rate-limit notices Claude Code passes on, and the user's own Claude Code sessions on this PC.

Jarvis's sessions run with --no-session-persistence, so they never show up in Claude Code's own transcripts
(~/.claude/projects); those transcripts are the user's own sessions, read here for the dashboard.
"""
import contextlib
import glob
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_FILE = os.path.join(ROOT, "memory", "usage.jsonl")
LIMITS_FILE = os.path.join(ROOT, "memory", "usage_limits.json")
TRANSCRIPTS = os.path.join(os.path.expanduser("~"), ".claude", "projects")
MAX_LOG_BYTES = 3_000_000
KEEP_DAYS = 8

_lock = threading.Lock()


@contextlib.contextmanager
def _shared_lock():
    """Jarvis and the team service both write these files: one at a time, across both processes."""
    with _lock:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE + ".lock", "a+b") as handle:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)  # retries for up to 10 seconds
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)


def tokens(usage: dict) -> dict:
    """An API usage block as {in, cached, out}: fresh input (with cache writes), cache reads, output."""
    usage = usage or {}
    return {"in": int(usage.get("input_tokens") or 0) + int(usage.get("cache_creation_input_tokens") or 0),
            "cached": int(usage.get("cache_read_input_tokens") or 0), "out": int(usage.get("output_tokens") or 0)}


def record(who: str, by_message: dict, model: str = "") -> None:
    """One turn's usage. `by_message` is {message id: usage block}: the last block seen for each API message,
    because Claude Code repeats a message's usage on each of its parts."""
    total = {"in": 0, "cached": 0, "out": 0}
    for usage in by_message.values():
        for k, v in tokens(usage).items():
            total[k] += v
    if not any(total.values()):
        return
    line = json.dumps({"t": round(time.time()), "who": who, "model": model, "calls": len(by_message), **total})
    try:
        with _shared_lock():
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            if os.path.getsize(LOG_FILE) > MAX_LOG_BYTES:
                _trim()
    except OSError as e:
        print(f"[usage] couldn't record: {e}")


def _trim() -> None:
    cutoff = time.time() - KEEP_DAYS * 86400
    keep = [e for e in _entries() if e.get("t", 0) >= cutoff]
    tmp = f"{LOG_FILE}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(e) + "\n" for e in keep[-20000:])
    os.replace(tmp, LOG_FILE)


def record_limit(event: dict) -> None:
    """A rate_limit_event from Claude Code: the plan's limit status (and when it resets), by limit type."""
    info = event.get("rate_limit_info") or {}
    if not isinstance(info, dict) or not info:
        return
    kind = str(info.get("rateLimitType") or info.get("type") or "limit")
    try:
        with _shared_lock():
            limits = _limits()
            limits[kind] = {**{k: v for k, v in info.items() if isinstance(v, (str, int, float, bool))},
                            "seen": round(time.time())}
            tmp = f"{LIMITS_FILE}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(limits, f, indent=2)
            os.replace(tmp, LIMITS_FILE)
    except OSError:
        pass


def _limits() -> dict:
    try:
        with open(LIMITS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def limits() -> dict:
    return _limits()


def _entries() -> list[dict]:
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []
    found = []
    for line in lines:
        try:
            found.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a line cut short by a crash
    return found


def jarvis_entries(since: float) -> list[dict]:
    """Turns run by Jarvis and its agents since `since` (a timestamp)."""
    return [e for e in _entries() if e.get("t", 0) >= since]


# ---------- the user's own Claude Code sessions ----------

_files: dict[str, dict] = {}  # transcript path -> what's been read of it so far
_files_lock = threading.Lock()


def _read_transcript(path: str) -> dict | None:
    """A transcript's session, reading only what was added since last time."""
    try:
        size, mtime = os.path.getsize(path), os.path.getmtime(path)
    except OSError:
        return None
    s = _files.get(path)
    if s is None or size < s["offset"]:  # new, or rewritten
        s = _files[path] = {"offset": 0, "messages": {}, "title": "", "summary": "", "cwd": "", "model": "",
                            "first": "", "last": "", "prompts": 0, "branch": "", "id": os.path.basename(path)[:-6]}
    if size > s["offset"]:
        with open(path, "rb") as f:
            f.seek(s["offset"])
            chunk = f.read(size - s["offset"])
        end = chunk.rfind(b"\n") + 1  # leave a half-written last line for next time
        s["offset"] += end
        for raw in chunk[:end].splitlines():
            try:
                line = json.loads(raw)
            except ValueError:
                continue
            kind, when = line.get("type"), line.get("timestamp") or ""
            if kind == "summary" and line.get("summary"):
                s["summary"] = str(line["summary"])[:120]
                continue
            if when:
                s["first"] = s["first"] or when
                s["last"] = max(s["last"], when)
            s["cwd"] = line.get("cwd") or s["cwd"]
            s["branch"] = line.get("gitBranch") or s["branch"]
            message = line.get("message") or {}
            if kind == "assistant" and message.get("usage"):
                key = message.get("id") or line.get("uuid")
                s["messages"][key] = (when, tokens(message["usage"]))  # the last copy of a message wins
                s["model"] = message.get("model") or s["model"]
            elif kind == "user" and not line.get("isMeta") and not line.get("toolUseResult"):
                content = message.get("content")
                text = content if isinstance(content, str) else " ".join(
                    b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")
                text = text.strip()
                if text and not text.startswith("<"):  # not a command or a system note
                    s["prompts"] += 1
                    s["title"] = s["title"] or text.splitlines()[0][:100]
    s["mtime"] = mtime
    return s


def claude_code_sessions(days: int = KEEP_DAYS) -> list[dict]:
    """The user's Claude Code sessions on this PC active in the last `days` days, most recent first."""
    cutoff = time.time() - days * 86400
    found = []
    with _files_lock:
        for path in glob.glob(os.path.join(TRANSCRIPTS, "*", "*.jsonl")):
            try:
                if os.path.getmtime(path) < cutoff:
                    continue
            except OSError:
                continue
            s = _read_transcript(path)
            if s and s["messages"]:
                found.append({**s, "messages": dict(s["messages"])})  # a copy: other requests keep reading files
        for path in [p for p, s in _files.items() if s.get("mtime", 0) < cutoff]:
            _files.pop(path, None)  # don't keep old sessions in memory
    return sorted(found, key=lambda s: s["last"], reverse=True)


def session_tokens(s: dict, since: str = "") -> dict:
    total = {"in": 0, "cached": 0, "out": 0, "calls": 0}
    for when, t in s["messages"].values():
        if when >= since:
            for k, v in t.items():
                total[k] += v
            total["calls"] += 1
    return total


def iso_since(hours: float) -> str:
    """A transcript-style UTC timestamp for `hours` ago, to compare with theirs."""
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
