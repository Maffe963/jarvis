"""Everything the agent team does is reported here as small events; the dashboard shows them live.

Events are plain dicts with a "type" (agent, status, activity, message, approval, approval_done, jarvis) plus
details. Listeners (the dashboard's live connections) each get their own queue.
"""
import itertools
import queue
import threading
import time

_counter = itertools.count(1)
_history: list[dict] = []          # recent events, so a newly opened dashboard can catch up
_listeners: list[queue.Queue] = []
_lock = threading.Lock()
MAX_HISTORY = 2000


def emit(event_type: str, **details) -> dict:
    event = {"id": next(_counter), "type": event_type, "time": time.time(), **details}
    with _lock:
        _history.append(event)
        del _history[:-MAX_HISTORY]
        listeners = list(_listeners)
    for listener in listeners:
        listener.put(event)
    return event


def recent() -> list[dict]:
    with _lock:
        return list(_history)


def subscribe() -> queue.Queue:
    listener: queue.Queue = queue.Queue()
    with _lock:
        _listeners.append(listener)
    return listener


def unsubscribe(listener: queue.Queue) -> None:
    with _lock:
        if listener in _listeners:
            _listeners.remove(listener)


class ActivityLog(list):
    """An agent's action log (the `log` run_tool appends to) that also reports each action as an event."""

    def __init__(self, agent_id: str) -> None:
        super().__init__()
        self.agent_id = agent_id

    def append(self, entry: str) -> None:
        super().append(entry)
        tool, _, result = entry.partition(": ")
        emit("activity", agent=self.agent_id, tool=tool, result=result[:200])
