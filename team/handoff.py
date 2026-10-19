"""Who's on: lets a Mac in Mac mode (mac.py) stand in for the PC only while the PC is off, with never both at once.

Every team service writes a heartbeat into memory/team/heartbeats/ (shared between the computers through OneDrive,
see sync_setup.py): its state is "waiting" while it waits for the other computer to let go, "running" while it runs
the team, and "stopped" once it has saved everything and stopped. A heartbeat counts as alive while its time keeps
changing, judged on *this* computer's clock (so the two clocks needn't agree), or for GONE_S after it's first seen.

- The Mac takes over when the PC's heartbeat says "stopped", or stops changing for GONE_S (shut down or asleep).
- A team service starting (on either computer) first waits until no other computer is running the team, then reads
  the shared files; the Mac stands down as soon as it sees the PC's heartbeat, so the PC waits about a minute.
- After a sleep, a team service stops touching the shared files and restarts itself, since the other computer may
  have run the team meanwhile (see team_service.py).
"""
import json
import os
import platform
import socket
import threading
import time

from team import messages

FOLDER = os.path.join(messages.FOLDER, "heartbeats")
HEARTBEAT_S = 30
GONE_S = 180         # a heartbeat that hasn't changed for this long: that computer is off or asleep
WAIT_MAX_S = 6 * 60  # the longest a starting team service waits for the other computer to let go
SLEPT_S = 90         # a gap this long between checks means this computer was asleep

frozen = threading.Event()  # set after waking from sleep: don't write the shared files until restarted


def host() -> str:
    return socket.gethostname().split(".")[0] or "this computer"


def _path(name: str) -> str:
    return os.path.join(FOLDER, "".join(c if c.isalnum() or c in "-_" else "_" for c in name) + ".json")


def beat(state: str) -> None:
    os.makedirs(FOLDER, exist_ok=True)
    tmp = _path(host()) + f".{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"host": host(), "windows": os.name == "nt", "system": platform.system(), "state": state,
                   "time": time.time()}, f)
    os.replace(tmp, _path(host()))


class Watch:
    """Other computers' heartbeats, judged by when they last changed as seen from here."""

    def __init__(self) -> None:
        self.seen: dict[str, tuple[float, float]] = {}  # host -> (its time value, our monotonic time it changed)

    def others(self) -> list[dict] | None:
        """[{host, windows, state, alive}] for the other computers, or None if the shared folder can't be read
        (OneDrive not running, signed out, no permission): then nobody can tell, so callers change nothing."""
        try:
            if not os.path.isdir(messages.FOLDER):
                return None
            names = os.listdir(FOLDER) if os.path.isdir(FOLDER) else []
        except OSError:
            return None
        now, found = time.monotonic(), []
        for name in names:
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(FOLDER, name), "r", encoding="utf-8") as f:
                    beat_ = json.load(f)
            except (OSError, ValueError):
                continue
            other = beat_.get("host")
            if not other or other == host():
                continue
            stamp = beat_.get("time", 0)
            last = self.seen.get(other)
            if last is None and time.time() - stamp > GONE_S + 600:
                self.seen[other] = (stamp, now - GONE_S)  # long stale even allowing for the clocks differing
            elif last is None or last[0] != stamp:
                self.seen[other] = (stamp, now)
            changed_at = self.seen[other][1]
            alive = beat_.get("state") != "stopped" and now - changed_at < GONE_S
            found.append({**beat_, "alive": alive})
        return found

    def pc(self) -> str:
        """'on', 'off' or 'unknown': is a Windows computer (not this one) running or about to run the team?"""
        others = self.others()
        if others is None:
            return "unknown"
        return "on" if any(o.get("windows") and o["alive"] for o in others) else "off"

    def team_elsewhere(self) -> bool:
        """Whether another computer is running the team right now (or may be: it's on but hasn't said)."""
        others = self.others()
        return bool(others) and any(o["alive"] and o.get("state") != "waiting" for o in others)


def wait_for_others(say=print) -> None:
    """Before running the team: wait until no other computer is running it (bounded by WAIT_MAX_S), saying
    "waiting" meanwhile so a Mac in Mac mode stands down."""
    watch = Watch()
    if not watch.team_elsewhere():
        return
    say("[team] the other computer is running the team: waiting for it to hand over")
    deadline = time.monotonic() + WAIT_MAX_S
    while time.monotonic() < deadline:
        try:
            beat("waiting")
        except OSError:
            pass
        time.sleep(5)
        if not watch.team_elsewhere():
            say("[team] handed over")
            time.sleep(5)  # let OneDrive bring the last of its files
            return
    say("[team] the other computer didn't hand over in time; carrying on")


def start_heartbeat(on_wake=None) -> None:
    """Beat every HEARTBEAT_S. If this computer was asleep, call `on_wake()` (after freezing the shared files)."""
    def loop():
        last_beat = last_check = time.time()
        try:
            beat("running")
        except OSError as e:
            print(f"[team] couldn't write the heartbeat: {e}")
        while True:
            time.sleep(5)
            now = time.time()
            if now - last_check > SLEPT_S and on_wake is not None and os.path.isdir(FOLDER) and len(os.listdir(FOLDER)) > 1:
                frozen.set()  # the other computer may have run the team while this one slept
                on_wake()
                return
            last_check = now
            if now - last_beat >= HEARTBEAT_S:
                try:
                    beat("running")
                except OSError as e:
                    print(f"[team] couldn't write the heartbeat: {e}")
                last_beat = now

    threading.Thread(target=loop, daemon=True, name="heartbeat").start()
