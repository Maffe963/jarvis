"""Control Jarvis from your phone through a private Telegram bot.

Text or voice-note it like you'd talk to Jarvis. Only your own Telegram account (TELEGRAM_ALLOWED_USER_ID)
is listened to; everyone else is ignored. Risky actions ask you with Yes/No buttons. After each task the
bot sends what each action actually reported, plus a screenshot when something on screen changed, so you
can check it was done right.

Commands: /stop (stop the current task), /screenshot [left|right|main|all], /status, /dashboard,
/restart [team|all], /off, /help
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
LIVE_EDIT_EVERY_S = 1.0  # while a reply is being written, the message on the phone is updated this often
TYPING_EVERY_S = 4.5     # Telegram drops "typing..." after about 5 seconds, so it's sent again
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
if os.name != "nt":
    PHONE_NOTE += ("[Right now you're running on the user's Mac because their PC is off (Mac mode): you can't see or "
                   "control the PC's screen, apps or sound, but email, the web, files on this Mac, reminders, lists and "
                   "the agent team all work. If they ask for something on the PC, say it will work once the PC is on.]\n")
HELP = ("Message me like you'd talk to Jarvis (text or a voice note), e.g. \"open Spotify on my left screen\" "
        "or \"what are my top jobs?\".\n\n"
        "After each task I send what actually happened and, if the screen changed, a screenshot.\n\n"
        "/stop - stop what I'm doing\n/screenshot - see your screen (add left, right, main or all)\n"
        "/status - CPU, memory and open apps\n/dashboard - a link to the agent dashboard\n"
        "/restart - restart Jarvis (/restart team: the agent team, /restart all: both)\n"
        "/off - turn Jarvis off on the PC\n/help - this message")


# Shown by Telegram when you type "/" or tap the Menu button.
COMMANDS = [
    ("on", "Turn Jarvis on (when it's off)"),
    ("off", "Turn Jarvis off on the PC"),
    ("restart", "Restart Jarvis (add team or all)"),
    ("stop", "Stop what Jarvis is doing"),
    ("screenshot", "See your screen (add left, right, main or all)"),
    ("status", "CPU, memory and open apps"),
    ("dashboard", "Link to the agent dashboard"),
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

    def _upload(self, method: str, field: str, filename: str, data: bytes, content_type: str, **fields) -> None:
        boundary = uuid.uuid4().hex
        parts = [f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
                 for name, value in fields.items()]
        safe_name = filename.replace('"', "'")
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{safe_name}\"\r\n"
                     f"Content-Type: {content_type}\r\n\r\n".encode() + data + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        req = urllib.request.Request(f"{self.base}/{method}", data=b"".join(parts),
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            resp.read()

    def send_photo(self, chat_id: int, jpeg: bytes, caption: str = "") -> None:
        self._upload("sendPhoto", "photo", "screen.jpg", jpeg, "image/jpeg", chat_id=chat_id, caption=caption[:1000])

    def send_document(self, chat_id: int, path: str, caption: str = "") -> None:
        with open(path, "rb") as f:
            data = f.read()
        self._upload("sendDocument", "document", os.path.basename(path), data, "application/octet-stream",
                     chat_id=chat_id, caption=caption[:1000])

    def download(self, file_id: str) -> bytes:
        path = self.call("getFile", file_id=file_id)["file_path"]
        with urllib.request.urlopen(f"{self.files}/{path}", timeout=60) as resp:
            return resp.read()


UNEXPLAINED_S = 30 * 60  # a file sent without a caption counts as what the next message is about for this long


def incoming_file(message: dict) -> tuple[str, str, str] | None:
    """(file id, file name, kind) for a photo, video, GIF or file in a message, or None."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if message.get("photo"):  # several sizes; the last is the largest
        return message["photo"][-1]["file_id"], f"photo-{stamp}.jpg", "photo"
    for kind, default in (("video", f"video-{stamp}.mp4"), ("animation", f"gif-{stamp}.mp4"),
                          ("video_note", f"video-note-{stamp}.mp4"), ("document", f"file-{stamp}")):
        item = message.get(kind)
        if item:
            return item["file_id"], item.get("file_name") or default, "video" if kind != "document" else "file"
    return None


def save_incoming(api: "TelegramAPI", file_id: str, name: str) -> str:
    """Save a file sent from the phone into Downloads\\From phone, never overwriting anything. Raises RuntimeError
    (with a message for the user) if it can't be downloaded."""
    folder = os.path.join(os.path.expanduser("~"), "Downloads", "From phone")
    os.makedirs(folder, exist_ok=True)
    name = os.path.basename(name).strip() or "file"
    stem, ext = os.path.splitext(name)
    path, n = os.path.join(folder, name), 1
    while os.path.exists(path):
        n += 1
        path = os.path.join(folder, f"{stem} ({n}){ext}")
    try:
        data = api.download(file_id)
    except Exception as e:
        raise RuntimeError(f"Couldn't download that file ({e}). Telegram only lets bots download files up to "
                           "20 MB: for a bigger video, put it in a folder on the PC (OneDrive, Google Drive or a "
                           "USB stick) and tell the agent where.") from None
    with open(path, "wb") as f:
        f.write(data)
    return path


def _plain(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), text)  # Telegram shows ** literally


class LiveReply:
    """Shows Jarvis's reply on the phone while Claude is still writing it: one message, edited as more arrives
    (at most once a LIVE_EDIT_EVERY_S), then replaced by the finished reply. Text Claude writes before using a
    tool ("Let me check your calendar.") stays as its own message, and the answer starts a new one."""

    def __init__(self, bot: "PhoneJarvis") -> None:
        self.bot, self.text, self.shown, self.message_id = bot, "", "", None
        self.last, self.queued, self.done = 0.0, False, False
        self.lock = threading.Lock()

    def __call__(self, piece) -> None:
        from brain import NEW_MESSAGE

        with self.lock:
            if self.done:
                return
            if piece is NEW_MESSAGE:
                part, self.text = self.text.strip(), ""
                if self.message_id is not None or self.queued:  # keep what's shown; the next part gets its own message
                    self.bot.later(lambda: self._next_message(part))
                return
            self.text += piece
            draft = self.text.strip()
            if self.queued or len(draft) < 15 or draft.startswith("[") or time.monotonic() - self.last < LIVE_EDIT_EVERY_S:
                return
            self.queued = True
        self.bot.later(self._show)

    # _next_message, _show and _put only run on the bot's send thread (or after finish), one at a time.
    def _next_message(self, part: str) -> None:
        if part and not part.startswith("["):
            self._put(part)  # the whole of it, without the cursor
        self.message_id, self.shown = None, ""

    def _show(self) -> None:
        with self.lock:
            self.queued = False
            draft = self.text.strip()
            if self.done or not draft or draft.startswith("["):
                return
        self._put(draft[:MAX_MESSAGE - 2] + " ▍")
        self.last = time.monotonic()

    def _put(self, text: str) -> None:
        """Send the message, or edit the one already sent."""
        text = _plain(text)[:MAX_MESSAGE] or "(empty)"
        if text == self.shown:
            return
        if self.message_id is None:
            sent = self.bot.send(text)
            self.message_id = sent.get("message_id") if sent else None
        else:
            try:
                self.bot.api.call("editMessageText", chat_id=self.bot.chat_id, message_id=self.message_id, text=text)
            except Exception as e:
                if "not modified" not in str(e):
                    print(f"[telegram] couldn't update the reply: {e}")
        self.shown = text

    def finish(self, _=None) -> bool:
        """Stop updating. True if part of the answer is already on the phone (then use replace)."""
        with self.lock:
            self.done = True
        self.bot._flush()
        return self.message_id is not None

    def settle(self) -> None:
        """Leave what was written so far as it is, without the cursor (the task was stopped or failed)."""
        if self.shown.endswith(" ▍"):
            self._put(self.shown[:-2])

    def replace(self, reply: str) -> None:
        """Put the finished reply in place of the one being written (or remove it if there's nothing to say)."""
        if reply:
            self._put(reply)
            return
        try:
            self.bot.api.call("deleteMessage", chat_id=self.bot.chat_id, message_id=self.message_id)
        except Exception:
            self._put("✅")


class PhoneJarvis:
    def __init__(self, token: str, allowed_user: int | None, respond, history_loader, cancel_task, turn_off) -> None:
        self.api = TelegramAPI(token)
        self.allowed = allowed_user
        self.respond, self.cancel_task, self.turn_off = respond, cancel_task, turn_off
        self.history = history_loader(HISTORY_FILE)
        self.chat_id: int | None = allowed_user  # in a private chat, the chat ID is the user's ID
        self.jobs: queue.Queue = queue.Queue()
        self.working = threading.Event()
        self.unexplained: list[tuple[str, float]] = []  # files sent without a caption: the next message is about them
        self._answers: dict[str, tuple[threading.Event, list]] = {}
        self._outbox: queue.Queue = queue.Queue()

    # ---------- plumbing ----------

    def start(self) -> None:
        try:
            register_commands(self.api.call)
        except Exception as e:
            print(f"[telegram] couldn't register the command menu: {e}")
        threading.Thread(target=self._poll, daemon=True, name="telegram-poll").start()
        threading.Thread(target=self._work, daemon=True, name="telegram-work").start()
        threading.Thread(target=self._send_queued, daemon=True, name="telegram-send").start()
        print("[telegram] phone control on" + ("" if self.allowed else " (waiting for you to set TELEGRAM_ALLOWED_USER_ID)"))

    def send(self, text: str, **extra) -> dict | None:
        if self.chat_id is None:
            return None
        try:
            return self.api.call("sendMessage", chat_id=self.chat_id, text=_plain(text)[:MAX_MESSAGE] or "(empty)", **extra)
        except Exception as e:
            print(f"[telegram] couldn't send: {e}")
            return None

    def later(self, job) -> None:
        """Do a Telegram call in the background, in order with the others, so a task never waits on the network
        for a side message (the "Yes sir, ..." note, "You said: ...")."""
        self._outbox.put(job)

    def send_later(self, text: str) -> None:
        self.later(lambda: self.send(text))

    def _flush(self) -> None:
        """Wait until every background send has gone out, so the reply arrives after them."""
        self._outbox.join()

    def _send_queued(self) -> None:
        while True:
            job = self._outbox.get()
            try:
                job()
            except Exception as e:
                print(f"[telegram] couldn't send: {e}")
            finally:
                self._outbox.task_done()

    def _keep_typing(self, done: threading.Event) -> None:
        while not done.is_set():
            try:
                self.api.call("sendChatAction", chat_id=self.chat_id, action="typing")
            except Exception:
                pass
            done.wait(TYPING_EVERY_S)

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
        elif incoming_file(message):
            file_id, name, _ = incoming_file(message)
            self.jobs.put(("file", (file_id, name, (message.get("caption") or "").strip())))
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
        elif command in ("/screenshot", "/status") and os.name != "nt":
            from tools import system

            self.send(f"💻 Your PC is off, so I'm running on your Mac: I can't see or control the PC's screen right "
                      f"now.\n\nThis Mac: {system.system_info()}")
        elif command == "/screenshot":
            self._send_screenshot(arg.strip(), "Your screen right now.")
        elif command == "/status":
            from tools import system

            apps = sorted({system._app_label(w) for w in system._app_windows()})
            self.send(f"{system.system_info()}\nOpen apps: {', '.join(apps) or 'none'}")
        elif command == "/dashboard":
            from dashboard import server

            link = server.public_url()
            self.send(f"🖥 Your agent dashboard: {link}\n\nKeep this link private: it controls Jarvis." if link else
                      "The dashboard only opens on the PC right now. To open it from your phone, install Tailscale on "
                      "the PC and your phone, run `tailscale serve --bg 8765` on the PC, and put the https address it "
                      "shows in .env as JARVIS_DASHBOARD_URL. Then restart Jarvis and send /dashboard again.")
        elif command == "/on":
            self.send("Jarvis is already on. 👍")
        elif command == "/restart":
            # In the background: restarting the team can take a while and shouldn't hold up reading messages.
            threading.Thread(target=self._restart, args=(arg.strip().lower(),), daemon=True).start()
        elif command == "/off":
            # Not on this thread: it's the one that reads your Yes/No tap, so waiting for it here would never end.
            threading.Thread(target=self._off, daemon=True).start()
        else:
            self.send("I don't know that command. /help shows what I can do.")

    def _restart(self, what: str) -> None:
        """/restart: Jarvis; /restart team: the agent team service (agents carry on); /restart all: both."""
        if what in ("team", "agents", "all", "both", "everything"):
            from team import client

            self.send("🔄 Restarting the agent team...")
            self.send("✅ The agent team is back; unfinished agents carry on." if client.restart_service()
                      else "⚠️ The agent team didn't come back. Check memory/local/service.log on the PC.")
            if what in ("team", "agents"):
                return
        if self.working.is_set():
            self.cancel_task()  # a restart shouldn't wait for the task in progress
        from tools import updater

        self.send("🔄 Restarting Jarvis. I'll message you when I'm back.")  # the new Jarvis says so
        updater._restart_after_reply()

    def _off(self) -> None:
        if self._ask("Turn Jarvis off on the PC?" + ("" if launcher_running() else " You won't be able to "
                     "reach me from your phone until it's started on the PC again.")):
            self.send("Turning off. 👋" + (" Send /on when you want me back." if launcher_running() else ""))
            self.turn_off()

    # ---------- doing tasks ----------

    def _work(self) -> None:
        while True:
            kind, value = self.jobs.get()
            try:
                if kind == "file":
                    file_id, name, caption = value
                    path = self._save_incoming(file_id, name)
                    if not path:
                        continue
                    if not caption:  # keep it in mind: the next message says what it's for
                        asked = any(time.time() - at < UNEXPLAINED_S for _, at in self.unexplained)
                        self.unexplained.append((path, time.time()))
                        self.send(f"📥 Saved to {path}" + ("" if asked else "\nWhat should I do with it? (e.g. "
                                                           "\"send it to the Video Editor and make it a Short\")"))
                        continue
                    self.send(f"📥 Saved to {path}")
                    value = f"{caption}\n[The user just sent this file from their phone; it's saved at {path}]"
                    kind = "text"
                if kind == "voice":
                    value = self._transcribe(value)
                    if not value:
                        self.send("🎙 I couldn't make out that voice note.")
                        continue
                    self.send_later(f"🎙 You said: {value}")
                recent = [p for p, at in self.unexplained if time.time() - at < UNEXPLAINED_S]
                self.unexplained.clear()
                if recent:
                    value += ("\n[Just before this message the user sent these files from their phone, saved at: "
                              + "; ".join(recent) + ". This message is probably about them.]")
                self._task(value)
            except Exception as e:
                traceback.print_exc()
                self.send(f"Something went wrong: {e}")

    def _save_incoming(self, file_id: str, name: str) -> str | None:
        try:
            return save_incoming(self.api, file_id, name)
        except RuntimeError as e:
            self.send(str(e))
            return None

    def send_file(self, path: str, caption: str = "") -> None:
        self.api.send_document(self.chat_id, path, caption)

    def _transcribe(self, file_id: str) -> str:
        from faster_whisper.audio import decode_audio

        from voice import stt

        audio = decode_audio(io.BytesIO(self.api.download(file_id)), sampling_rate=16000)
        return stt.transcribe(audio)

    def _task(self, text: str) -> None:
        self.working.set()
        typing_done = threading.Event()
        threading.Thread(target=self._keep_typing, args=(typing_done,), daemon=True).start()
        live = LiveReply(self)
        try:
            actions: list[str] = []
            self.history, reply = self.respond(self.history, PHONE_NOTE + text, self._ask, self.send_later,
                                               history_file=HISTORY_FILE, actions_out=actions, on_text=live)
        except Exception:
            if live.finish():
                live.settle()
            raise
        finally:
            typing_done.set()
            self.working.clear()
        shown = live.finish()  # stop updating; the final text is put in below
        if reply is None:
            if shown:
                live.settle()
            self.send("🛑 Stopped." + (" Before that I'd done:\n" + self._proof(actions) if actions else ""))
            return
        if shown:
            live.replace(reply)
        elif reply:
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
            lines.append(f"{mark} {name.replace('_', ' ')} → {result}" if result else f"{mark} {name.replace('_', ' ')}")
        return "\n".join(lines)

    def _ask(self, question: str) -> bool:
        """Yes/No buttons on your phone for anything Jarvis would normally confirm out loud."""
        self._flush()  # anything queued (the "Yes sir, ..." note) comes before the question
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


active: PhoneJarvis | None = None  # the running bot, so tools can send things to the phone


def start(respond, history_loader, cancel_task, turn_off) -> PhoneJarvis | None:
    """Start phone control if TELEGRAM_BOT_TOKEN is set. Returns the bot, or None."""
    token = _env("TELEGRAM_BOT_TOKEN")
    if not token:
        return None
    allowed = _env("TELEGRAM_ALLOWED_USER_ID")
    global active
    bot = PhoneJarvis(token, int(allowed) if allowed.isdigit() else None, respond, history_loader, cancel_task, turn_off)
    bot.start()
    active = bot
    restarted = os.environ.pop("JARVIS_RESTART_NOTE", "")  # set by a restart or an update (tools/updater.py)
    if restarted:
        bot.send(restarted)
    if os.environ.pop("JARVIS_STARTED_FROM_PHONE", "") and not restarted:
        bot.send("✅ Jarvis is on. Send /help to see what I can do.")
    return bot


def launcher_running() -> bool:
    """Whether launcher.py is waiting in the background, so /on works again after Jarvis turns off."""
    import ctypes

    if os.name != "nt":
        return False  # Mac mode has no launcher: mac.py starts Jarvis
    handle = ctypes.windll.kernel32.OpenMutexW(0x00100000, False, "Local\\JarvisLauncher")
    if handle:
        ctypes.windll.kernel32.CloseHandle(handle)
    return bool(handle)
