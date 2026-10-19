"""Who's on: lets a Mac in Mac mode (mac.py) stand in for the PC only while the PC is off.

Every team service writes a heartbeat into memory/team/heartbeats/ every HEARTBEAT_S. With memory/team shared
between the computers through OneDrive (sync_setup.py), the Mac sees the PC's heartbeat and stays quiet while it's
fresh; once the PC has been silent for PC_GONE_S (it was shut down or went to sleep), the Mac takes over, and it hands
back as soon as the PC's heartbeat comes back. The PC never waits for the Mac.
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
PC_GONE_S = 180  # OneDrive can take a minute or so to pass a file on, so this leaves room for that


def host() -> str:
    return socket.gethostname().split(".")[0] or "this computer"


def _path(name: str) -> str:
    return os.path.join(FOLDER, "".join(c if c.isalnum() or c in "-_" else "_" for c in name) + ".json")


def beat() -> None:
    os.makedirs(FOLDER, exist_ok=True)
    tmp = _path(host()) + f".{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"host": host(), "windows": os.name == "nt", "system": platform.system(), "time": time.time()}, f)
    os.replace(tmp, _path(host()))


def start_heartbeat() -> None:
    def loop():
        while True:
            try:
                beat()
            except OSError as e:
                print(f"[team] couldn't write the heartbeat: {e}")
            time.sleep(HEARTBEAT_S)

    threading.Thread(target=loop, daemon=True, name="heartbeat").start()


def pc_on() -> dict | None:
    """The PC's latest heartbeat, if a Windows computer (not this one) has been on within PC_GONE_S."""
    try:
        names = os.listdir(FOLDER)
    except FileNotFoundError:
        return None
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(FOLDER, name), "r", encoding="utf-8") as f:
                found = json.load(f)
        except (OSError, ValueError):
            continue
        if found.get("windows") and found.get("host") != host() and time.time() - found.get("time", 0) < PC_GONE_S:
            return found
    return None
