"""Control Jarvis from your phone through a private Telegram bot.

Text or voice-note it like you'd talk to Jarvis. Only your own Telegram account (TELEGRAM_ALLOWED_USER_ID)
is listened to; everyone else is ignored. Risky actions ask you with Yes/No buttons. After each task the
bot sends what each action actually reported, plus a screenshot when something on screen changed, so you
can check it was done right.

Commands: /stop (stop the current task), /screenshot [left|right|main|all], /status, /off, /help
"""
import io
import json
import os
import queue
import re
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid

ROOT = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(ROOT, "memory", "telegram_history.json")
MAX_MESSAGE = 4000
CONFIRM_TIMEOUT_S = 180
# Tools whose effects you can see on screen: after these, a screenshot is sent as proof.
VISIBLE_TOOLS = {"open_app", "close_app", "open_url", "focus_window", "show_desktop", "ui_action",
                 "move_window_to_monitor", "open_path", "set_clipboard"}
# Wording tools use when something didn't work, so the proof list can flag it.
PROBLEM_WORDS = ("couldn't", "could not", "can't", "cannot", "don't know", "tool error", "unknown", "failed",
                 "declined", "isn't", "not found", "no such", "error", "refused", "stopped after", "no open",
                 "no installed", "no running", "no window", "no folder", "nothing matching", "nothing at",
                 "not set up", "still open", "no voice", "no reminder", "no contact", "no email", "interrupted")
PHONE_NOTE = ("[Sent from the user's phone via Telegram, so your reply is read, not spoken: plain text, no "
              "markdown, though a short list or a link is fine. They may be away from the PC, so say clearly "
              "what happened.]\n")
HELP = ("Message me like you'd talk to Jarvis (text or a voice note), e.g. \"open Spotify on my left screen\" "
        "or \"what are my top jobs?\".\n\n"
        "After each task I send what actually happened and, if the screen changed, a screenshot.\n\n"
        "/stop - stop what I'm doing\n/screenshot - see your screen (add left, right, main or all)\n"
        "/status - CPU, memory and open apps\n/off - turn Jarvis off on the PC\n/help - this message")


# Shown by Telegram when you type "/" or tap the Menu button.
COMMANDS = [
    ("on", "Turn Jarvis on (when it's off)"),
    ("off", "Turn Jarvis off on the PC"),
    ("stop", "Stop what Jarvis is doing"),
    ("screenshot", "See your screen (add left, right, main or all)"),
    ("status", "CPU, memory and open apps"),
    ("help", "What Jarvis can do"),
]


def register_commands(call) -> None:
    call("setMyCommands", commands=[{"command": c, "description": d} for c, d in COMMANDS])


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    return "" if value.startswith("your_") else value


class TelegramAPI:
    def __init__(self, token: str) -> None:
        self.base = f"https://api.telegram.org/bot{token}"
        self.files = f"https://api.telegram.org/file/bot{token}"

    def call(self, method: str, timeout: float = 30, **params):
        data = json.dumps(params).encode("utf-8")
        req = urllib.request.Request(f"{self.base}/{method}", data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read())
        if not body.get("ok"):
            raise RuntimeError(body.get("description", "Telegram error"))
        return body["result"]

    def send_photo(self, chat_id: int, jpeg: bytes, caption: str = "") -> None:
        boundary = uuid.uuid4().hex
        parts = []
        for name, value in (("chat_id", str(chat_id)), ("caption", caption[:1000])):
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"screen.jpg\"\r\n"
                     "Content-Type: image/jpeg\r\n\r\n".encode() + jpeg + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        req = urllib.request.Request(f"{self.base}/sendPhoto", data=b"".join(parts),
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            resp.read()

    def download(self, file_id: str) -> bytes:
        path = self.call("getFile", file_id=file_id)["file_path"]
        with urllib.request.urlopen(f"{self.files}/{path}", timeout=60) as resp:
            return resp.read()


class PhoneJarvis:
    def __init__(self, token: str, allowed_user: int | None, respond, history_loader, cancel_task, turn_off) -> None:
        self.api = TelegramAPI(token)
        self.allowed = allowed_user
        self.respond, self.cancel_task, self.turn_off = respond, cancel_task, turn_off
        self.history = history_loader(HISTORY_FILE)
        self.chat_id: int | None = allowed_user  # in a private chat, the chat ID is the user's ID
        self.jobs: queue.Queue = queue.Queue()
        self.working = threading.Event()
        self._answers: dict[str, tuple[threading.Event, list]] = {}

    # ---------- plumbing ----------

    def start(self) -> None:
        try:
            register_commands(self.api.call)
        except Exception as e:
            print(f"[telegram] couldn't register the command menu: {e}")
        threading.Thread(target=self._poll, daemon=True, name="telegram-poll").start()
        threading.Thread(target=self._work, daemon=True, name="telegram-work").start()
        print("[telegram] phone control on" + ("" if self.allowed else " (waiting for you to set TELEGRAM_ALLOWED_USER_ID)"))

    def send(self, text: str, **extra) -> dict | None:
        if self.chat_id is None:
            return None
        text = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), text)  # Telegram shows ** literally
        try:
            return self.api.call("sendMessage", chat_id=self.chat_id, text=text[:MAX_MESSAGE] or "(empty)", **extra)
        except Exception as e:
            print(f"[telegram] couldn't send: {e}")
            return None

    def notify(self, text: str) -> None:
        """Timers and reminders also reach your phone, in case you're away from the PC."""
        if self.chat_id is not None:
            self.send(f"🔔 {text}")

    def _poll(self) -> None:
        offset, backoff = None, 1
        while True:
            try:
                updates = self.api.call("getUpdates", timeout=40, offset=offset, allowed_updates=["message", "callback_query"])
                backoff = 1
            except Exception as e:
                print(f"[telegram] connection problem ({e}); retrying in {backoff}s")
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue
            for update in updates:
                offset = update["update_id"] + 1
                try:
                    self._handle(update)
                except Exception:
                    traceback.print_exc()

    def _handle(self, update: dict) -> None:
        if "callback_query" in update:
            query = update["callback_query"]
            if query["from"]["id"] != self.allowed:
                return
            key, _, choice = query.get("data", "").rpartition(":")
            self.api.call("answerCallbackQuery", callback_query_id=query["id"])
            if key in self._answers:
                event, answer = self._answers[key]
                answer.append(choice == "yes")
                event.set()
                label = "✅ Yes" if choice == "yes" else "❌ No"
                self.api.call("editMessageText", chat_id=query["message"]["chat"]["id"],
                              message_id=query["message"]["message_id"],
                              text=f"{query['message'].get('text', '')}\n\n{label}")
            return

        message = update.get("message") or {}
        sender = (message.get("from") or {}).get("id")
        if not sender:
            return
        if not self.allowed:
            # Setup: tell the person their ID so they can allow themselves. Nothing else is answered.
            self.api.call("sendMessage", chat_id=message["chat"]["id"],
                          text=f"Hi! To finish setup, put this line in Jarvis's .env file and restart Jarvis:\n\n"
                               f"TELEGRAM_ALLOWED_USER_ID={sender}")
            return
        if sender != self.allowed:
            print(f"[telegram] ignored a message from someone else (user {sender})")
            return
        self.chat_id = message["chat"]["id"]
        text = (message.get("text") or "").strip()
        if text.startswith("/"):
            self._command(text)
        elif "voice" in message or "audio" in message:
            self.jobs.put(("voice", (message.get("voice") or message.get("audio"))["file_id"]))
        elif text:
            if self.working.is_set() or not self.jobs.empty():
                self.send("⏳ Got it, I'll do that once the current task is finished. (/stop to cancel it)")
            self.jobs.put(("text", text))

    def _command(self, text: str) -> None:
        command, _, arg = text.partition(" ")
        command = command.split("@")[0].lower()
        if command in ("/start", "/help", "/commands"):
            self.send(HELP)
        elif command == "/stop":
            if self.working.is_set():
                self.cancel_task()
                self.send("🛑 Stopping.")
            else:
                self.send("Nothing is running.")
        elif command == "/screenshot":
            self._send_screenshot(arg.strip(), "Your screen right now.")
        elif command == "/status":
            from tools import system

            apps = sorted({system._app_label(w) for w in system._app_windows()})
            self.send(f"{system.system_info()}\nOpen apps: {', '.join(apps) or 'none'}")
        elif command == "/on":
            self.send("Jarvis is already on. 👍")
        elif command == "/off":
            if self._ask("Turn Jarvis off on the PC?" + ("" if launcher_running() else " You won't be able to "
                         "reach me from your phone until it's started on the PC again.")):
                self.send("Turning off. 👋" + (" Send /on when you want me back." if launcher_running() else ""))
                self.turn_off()
        else:
            self.send("I don't know that command. /help shows what I can do.")

    # ---------- doing tasks ----------

    def _work(self) -> None:
        while True:
            kind, value = self.jobs.get()
            try:
                if kind == "voice":
                    value = self._transcribe(value)
                    if not value:
                        self.send("🎙 I couldn't make out that voice note.")
                        continue
                    self.send(f"🎙 You said: {value}")
                self._task(value)
            except Exception as e:
                traceback.print_exc()
                self.send(f"Something went wrong: {e}")

    def _transcribe(self, file_id: str) -> str:
        from faster_whisper.audio import decode_audio

        from voice import stt

        audio = decode_audio(io.BytesIO(self.api.download(file_id)), sampling_rate=16000)
        return stt.transcribe(audio)

    def _task(self, text: str) -> None:
        self.working.set()
        try:
            self.api.call("sendChatAction", chat_id=self.chat_id, action="typing")
            actions: list[str] = []
            self.history, reply = self.respond(self.history, PHONE_NOTE + text, self._ask, self.send,
                                               history_file=HISTORY_FILE, actions_out=actions)
        finally:
            self.working.clear()
        if reply is None:
            self.send("🛑 Stopped." + (" Before that I'd done:\n" + self._proof(actions) if actions else ""))
            return
        if reply:
            self.send(reply)
        if actions:
            self.send("What actually happened:\n" + self._proof(actions))
        if _env("TELEGRAM_AUTO_SCREENSHOT").lower() not in ("off", "no", "false", "0") and \
                any(a.split(":")[0] in VISIBLE_TOOLS for a in actions):
            time.sleep(1.5)  # let windows finish opening or redrawing
            self._send_screenshot("", "Your screen now, so you can check.")

    @staticmethod
    def _proof(actions: list[str]) -> str:
        lines = []
        for action in actions:
            name, _, result = action.partition(": ")
            mark = "⚠️" if any(word in result.lower() for word in PROBLEM_WORDS) else "✅"
            lines.append(f"{mark} {result or name}")
        return "\n".join(lines)

    def _ask(self, question: str) -> bool:
        """Yes/No buttons on your phone for anything Jarvis would normally confirm out loud."""
        key = uuid.uuid4().hex[:12]
        event, answer = threading.Event(), []
        self._answers[key] = (event, answer)
        try:
            sent = self.send(f"❓ {question}", reply_markup={"inline_keyboard": [[
                {"text": "Yes", "callback_data": f"{key}:yes"}, {"text": "No", "callback_data": f"{key}:no"}]]})
            if not sent:
                return False
            if not event.wait(CONFIRM_TIMEOUT_S):
                self.api.call("editMessageText", chat_id=self.chat_id, message_id=sent["message_id"],
                              text=f"❓ {question}\n\n⌛ No answer, so I didn't do it.")
                return False
            return bool(answer and answer[0])
        finally:
            self._answers.pop(key, None)

    def _send_screenshot(self, monitor: str, caption: str) -> None:
        import base64

        from tools import desktop

        blocks = desktop.look_at_screen(all_monitors=monitor.lower() == "all",
                                        monitor="" if monitor.lower() in ("", "all") else monitor)
        image = next((b for b in blocks if b["type"] == "image"), None)
        if not image:
            self.send(blocks[0].get("text", "Couldn't take a screenshot."))
            return
        try:
            self.api.send_photo(self.chat_id, base64.b64decode(image["source"]["data"]), caption)
        except Exception as e:
            self.send(f"Couldn't send the screenshot: {e}")


def start(respond, history_loader, cancel_task, turn_off) -> PhoneJarvis | None:
    """Start phone control if TELEGRAM_BOT_TOKEN is set. Returns the bot, or None."""
    token = _env("TELEGRAM_BOT_TOKEN")
    if not token:
        return None
    allowed = _env("TELEGRAM_ALLOWED_USER_ID")
    bot = PhoneJarvis(token, int(allowed) if allowed.isdigit() else None, respond, history_loader, cancel_task, turn_off)
    bot.start()
    if os.environ.pop("JARVIS_STARTED_FROM_PHONE", ""):
        bot.send("✅ Jarvis is on. Send /help to see what I can do.")
    return bot


def launcher_running() -> bool:
    """Whether launcher.py is waiting in the background, so /on works again after Jarvis turns off."""
    import ctypes

    handle = ctypes.windll.kernel32.OpenMutexW(0x00100000, False, "Local\\JarvisLauncher")
    if handle:
        ctypes.windll.kernel32.CloseHandle(handle)
    return bool(handle)
