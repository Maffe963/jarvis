"""Yes/no questions from agents (or dashboard commands) that need the user: shown on the dashboard and, if phone
control is on, sent to the phone too. Whichever is answered first counts; no answer in time means no.

In the team service the team's Telegram bot puts agents' questions in their own topic instead (phone_prompts off)."""
import threading
import uuid

from team import events

TIMEOUT_S = 600
_pending: dict[str, dict] = {}
_lock = threading.Lock()
phone_prompts = True  # also ask through Jarvis's phone bot


def ask(question: str, who: str = "jarvis", agent: str = "") -> bool:
    approval_id = uuid.uuid4().hex[:8]
    done, answer = threading.Event(), []
    with _lock:
        _pending[approval_id] = {"question": question, "who": who, "done": done, "answer": answer}
    events.emit("approval", approval=approval_id, who=who, question=question, agent=agent)
    phone = _phone() if phone_prompts else None
    if phone:
        threading.Thread(target=lambda: decide(approval_id, phone._ask(f"({who}) {question}")), daemon=True).start()
    done.wait(TIMEOUT_S)
    decide(approval_id, False)  # no-op if already answered; otherwise a timeout counts as no
    return bool(answer and answer[0])


def decide(approval_id: str, yes: bool) -> bool:
    with _lock:
        item = _pending.pop(approval_id, None)
    if item is None:
        return False
    item["answer"].append(bool(yes))
    item["done"].set()
    events.emit("approval_done", approval=approval_id, who=item["who"], approved=bool(yes))
    return True


def pending() -> list[dict]:
    with _lock:
        return [{"approval": k, "who": v["who"], "question": v["question"]} for k, v in _pending.items()]


def _phone():
    try:
        import telegram_bot

        bot = telegram_bot.active
        return bot if bot is not None and bot.chat_id is not None else None
    except Exception:
        return None
