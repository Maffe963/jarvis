"""A test of Caveman (github.com/JuliusBrussee/caveman), which makes Claude answer tersely to save tokens.

For a few days each Claude Code session Jarvis or an agent starts flips a coin: Caveman on or off. Each turn's
usage is logged with which it was (tools/usage.py), so both sides see the same mix of days, tasks and agents.
When the test ends Jarvis sends the comparison to the phone and saves it to memory/caveman_report.txt;
sessions go back to normal until the user decides.

JARVIS_CAVEMAN: "test" (the default), "on" (always), or "off" (never, and no test). JARVIS_CAVEMAN_DAYS: test length.
"""
import json
import math
import os
import random
import threading
import time
from datetime import datetime

from tools import usage

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_FILE = os.path.join(ROOT, "memory", "caveman_test.json")
REPORT_FILE = os.path.join(ROOT, "memory", "caveman_report.txt")
RULES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "caveman_rules.md")
CHECK_EVERY_S = 1800
MIN_TURNS = 30  # per side, before a difference means anything

# Per million tokens: fresh input (cache writes count here too), cache reads, output. Jarvis is on the
# subscription, so this isn't a bill; it's the API-price equivalent, a fair measure of how fast limits go.
PRICES = {"opus": (5.0, 0.5, 25.0), "haiku": (1.0, 0.1, 5.0), "sonnet": (3.0, 0.3, 15.0)}


def _mode() -> str:
    return os.environ.get("JARVIS_CAVEMAN", "test").strip().lower()


def _rules() -> str:
    with open(RULES_FILE, "r", encoding="utf-8") as f:
        text = f.read()
    return text.split("-->", 1)[-1].strip()


def _test() -> dict:
    """The test's dates, starting it the first time Jarvis or the team service runs with it."""
    try:
        with open(TEST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    days = float(os.environ.get("JARVIS_CAVEMAN_DAYS") or 3)
    now = int(time.time())  # not rounded up past the moment it's checked against
    test = {"start": now, "end": now + round(days * 86400), "reported": False}
    os.makedirs(os.path.dirname(TEST_FILE), exist_ok=True)
    try:
        with open(TEST_FILE, "x", encoding="utf-8") as f:  # Jarvis and the team service may both get here first
            json.dump(test, f, indent=2)
    except FileExistsError:
        time.sleep(0.2)
        return _test()
    return test


def pick() -> str:
    """Caveman or normal for a session starting now: "caveman", "normal", or "" when there's no test on."""
    mode = _mode()
    if mode == "on":
        return "caveman"
    if mode != "test":
        return ""
    test = _test()
    if not test["start"] <= time.time() < test["end"]:
        return ""
    return random.choice(("caveman", "normal"))


def prompt_addition(arm: str) -> str:
    if arm != "caveman":
        return ""
    return ("\n\n# Reply style\nOn top of everything above, follow these style rules. Where they disagree about "
            "tone or length, they win; spoken replies stay plain spoken sentences.\n\n" + _rules())


# ---------- the report ----------

def _cost(e: dict) -> float:
    model = (e.get("model") or "").lower()
    fresh, cached, out = next((p for k, p in PRICES.items() if k in model), PRICES["sonnet"])
    return (e.get("in", 0) * fresh + e.get("cached", 0) * cached + e.get("out", 0) * out) / 1e6


def _side(entries: list[dict]) -> dict:
    costs = [_cost(e) for e in entries]
    n = len(entries)
    mean = sum(costs) / n if n else 0.0
    spread = math.sqrt(sum((c - mean) ** 2 for c in costs) / (n - 1)) if n > 1 else 0.0
    avg = lambda k: sum(e.get(k, 0) for e in entries) / n if n else 0.0
    return {"turns": n, "cost": mean, "error": spread / math.sqrt(n) if n else 0.0,
            "in": avg("in"), "cached": avg("cached"), "out": avg("out"), "calls": avg("calls"), "total": sum(costs)}


def _compare(entries: list[dict]) -> dict:
    cave = _side([e for e in entries if e.get("arm") == "caveman"])
    normal = _side([e for e in entries if e.get("arm") == "normal"])
    change = (cave["cost"] - normal["cost"]) / normal["cost"] * 100 if normal["cost"] else 0.0
    out_change = (cave["out"] - normal["out"]) / normal["out"] * 100 if normal["out"] else 0.0
    noise = math.hypot(cave["error"], normal["error"])
    if min(cave["turns"], normal["turns"]) < MIN_TURNS:
        verdict = "too few turns to tell"
    elif abs(cave["cost"] - normal["cost"]) < 2 * noise:
        verdict = "no clear difference (within the noise)"
    else:
        verdict = "cheaper with Caveman" if change < 0 else "more expensive with Caveman"
    return {"caveman": cave, "normal": normal, "change": change, "out_change": out_change, "verdict": verdict}


def results() -> dict | None:
    """The test so far (or final): overall, for Jarvis's own chats, and for agents and background tasks."""
    if not os.path.exists(TEST_FILE):
        return None
    test = _test()
    entries = [e for e in usage.jarvis_entries(test["start"]) if e.get("arm") and e["t"] < test["end"]]
    jarvis = [e for e in entries if (e.get("who") or "Jarvis") == "Jarvis"]
    others = [e for e in entries if (e.get("who") or "Jarvis") != "Jarvis"]
    return {"start": test["start"], "end": test["end"], "done": time.time() >= test["end"],
            "all": _compare(entries), "jarvis": _compare(jarvis), "agents": _compare(others)}


def _k(n: float) -> str:
    return f"{n / 1000:.1f}K" if n >= 1000 else f"{n:.0f}"


def report_text(r: dict) -> str:
    day = lambda t: datetime.fromtimestamp(t).strftime("%a %b %d, %I:%M %p")
    lines = [f"Caveman test, {day(r['start'])} to {day(r['end'])}" + ("" if r["done"] else " (still running)"),
             "Each Claude session was randomly Caveman or normal. Cost is per turn at API prices; "
             "you're on a flat plan, so read it as how fast your limits go.", ""]
    for key, title in (("all", "Everything"), ("jarvis", "Your chats with Jarvis"), ("agents", "Agents and tasks")):
        c = r[key]
        cave, normal = c["caveman"], c["normal"]
        lines += [f"{title}: {c['verdict']}"]
        if cave["turns"] and normal["turns"]:
            lines += [f"  cost per turn: ${cave['cost']:.4f} with vs ${normal['cost']:.4f} without ({c['change']:+.0f}%)",
                      f"  output tokens per turn: {_k(cave['out'])} vs {_k(normal['out'])} ({c['out_change']:+.0f}%)",
                      f"  input per turn (fresh + cached): {_k(cave['in'])} + {_k(cave['cached'])} vs "
                      f"{_k(normal['in'])} + {_k(normal['cached'])}"]
        lines += [f"  turns: {cave['turns']} with, {normal['turns']} without", ""]
    c = r["all"]
    turns = c["caveman"]["turns"] + c["normal"]["turns"]
    if turns and c["normal"]["cost"]:
        saved = (c["normal"]["cost"] - c["caveman"]["cost"]) * turns
        spent = c["caveman"]["total"] + c["normal"]["total"]
        lines.append(f"If every turn had used Caveman: about ${abs(saved):.2f} {'saved' if saved >= 0 else 'extra'} "
                     f"on ${spent:.2f} of usage over the test.")
    if r["done"]:
        lines.append("Sessions are back to normal. To keep Caveman on, add JARVIS_CAVEMAN=on to .env.")
    return "\n".join(lines)


def _finish(send) -> None:
    test = _test()
    if test.get("reported") or time.time() < test["end"] or _mode() != "test":
        return
    r = results()
    text = report_text(r)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    test["reported"] = True
    with open(TEST_FILE + ".tmp", "w", encoding="utf-8") as f:
        json.dump(test, f, indent=2)
    os.replace(TEST_FILE + ".tmp", TEST_FILE)
    send(text)


def _to_phone(text: str) -> None:
    import telegram_bot

    print(f"[caveman] {text}")
    if telegram_bot.active is not None:
        telegram_bot.active.send(text)


_started = False


def start(send=_to_phone) -> None:
    """Watch for the test's end, then `send(report)` once (to the phone)."""
    global _started
    if _started or _mode() != "test":
        return
    _started = True
    _test()

    def loop():
        while True:
            try:
                _finish(send)
            except Exception as e:
                print(f"[caveman] {e}")
            time.sleep(CHECK_EVERY_S)

    threading.Thread(target=loop, daemon=True).start()
