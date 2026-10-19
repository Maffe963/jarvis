"""The agent team on Telegram: every agent gets its own chat, as a topic in your team group.

Setup (once): make a second bot with @BotFather for the team, put its token in TEAM_TELEGRAM_BOT_TOKEN, create a
Telegram group, turn on Topics in its settings, add the bot and make it an admin that can manage topics. Then send
/setup in the group. Only your own account (TELEGRAM_ALLOWED_USER_ID) is listened to.

Each new agent gets a topic showing its task, its messages to and from teammates, its step-by-step updates and its
result; whatever you write there goes to that agent. Its approvals come with Yes/No buttons. The "All messages"
topic shows everything the agents say to each other. Photos, videos and files sent in a topic are saved in
Downloads\\From phone and handed to that agent. Commands: /new, /team, /stop, /result, /help.

Without a team bot, agents' questions and results go to your Jarvis bot chat instead (send-only, so approvals
are answered on the dashboard there).
"""
import json
import os
import re
import threading
import time
import traceback
import urllib.error
import urllib.request

from team import agents, approvals, events, messages
from telegram_bot import MAX_MESSAGE, TelegramAPI, _env, incoming_file, save_incoming

STATE_FILE = os.path.join(messages.FOLDER, "telegram.json")  # the group, and which topic is whose
HELP = ("This is your agent team. Each agent gets its own topic here: whatever you write in it goes to that "
        "agent, and the 📜 All messages topic shows everything they say to each other.\n\n"
        "/new Name | role | task - start an agent (or just /new task)\n"
        "/code repo | task - start a coder on one of your code repositories\n"
        "/team - what each agent is doing\n"
        "/stop - in an agent's topic, stop it (or /stop name, /stop all)\n"
        "/result - in an agent's topic, its final result (or /result name)\n"
        "/clear - take finished agents off the team and close their topics\n"
        "/help - this message\n\n"
        "Send a photo, screenshot, video or file in an agent's topic to give it to that agent. In General, start "
        "with an agent's name instead: \"Video Editor: make this vertical\".")
COMMANDS = [("new", "Start an agent: Name | role | task"), ("code", "Start a coder: repo | task"), ("team", "What each agent is doing"),
            ("stop", "Stop this agent (or: /stop name, /stop all)"), ("result", "This agent's final result"),
            ("clear", "Take finished agents off the team"),
            ("help", "How the team chat works")]
MARKS = {"done": "✅", "failed": "⚠️", "stopped": "⏹"}


def addressed(words: str) -> tuple["agents.Agent | None", str]:
    """The active agent a message starts with the name of, and the rest of it: "Video Editor: cut this",
    "@video_editor cut this" and "hey job hunter, any news?" all work. The longest name wins ("Ghostwriter" isn't
    "Writer")."""
    text = (words or "").strip()
    lead = r"^(?:@|(?:hey|hi|ok|okay|yo|to|for|ask)\b[\s,]*)?"
    for agent in sorted(agents.active(), key=lambda a: -len(a.name)):
        # the name's words with anything (or nothing) between them: "video editor", "video_editor", "VideoEditor"
        name = r"[\s_.\-]*".join(re.escape(w) for w in agent.name.split())
        found = re.match(lead + name + r"(?![A-Za-z0-9])", text, re.IGNORECASE)
        if found:
            return agent, text[found.end():].lstrip(" ,:;-–—>").strip()
    return None, text


def _split(text: str) -> list[str]:
    text = text or "(empty)"
    return [text[i:i + MAX_MESSAGE] for i in range(0, len(text), MAX_MESSAGE)]


def _short_task(task: str) -> str:
    first = task.split("\n(You're helping")[0].strip()
    return first if len(first) <= 200 else first[:199] + "…"


class TeamBot:
    def __init__(self, token: str, allowed: int | None) -> None:
        self.api = TelegramAPI(token)
        self.allowed = allowed
        self._lock = threading.Lock()
        self.state = self._load()
        configured = _env("TEAM_TELEGRAM_CHAT_ID")
        if configured.lstrip("-").isdigit():
            self.state["chat"] = int(configured)
        self._approvals: dict[str, tuple[int, int, str]] = {}  # approval id -> (thread, message id, text)

    # ---------- plumbing ----------

    def _load(self) -> dict:
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return {"topics": {}, **json.load(f)}
        except (FileNotFoundError, json.JSONDecodeError):
            return {"topics": {}}

    def _save(self) -> None:
        os.makedirs(messages.FOLDER, exist_ok=True)
        with self._lock, open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(self.state, f)

    @property
    def chat_id(self) -> int | None:
        return self.state.get("chat")

    def _call(self, method: str, http_timeout: float = 30, **params):
        """A Bot API call; waits and retries when Telegram says we're sending too fast."""
        for _ in range(5):
            req = urllib.request.Request(f"{self.api.base}/{method}", data=json.dumps(params).encode(),
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=http_timeout) as resp:
                    return json.loads(resp.read())["result"]
            except urllib.error.HTTPError as e:
                body = json.loads(e.read() or b"{}")
                wait = (body.get("parameters") or {}).get("retry_after")
                if e.code == 429 and wait:
                    time.sleep(wait + 0.5)
                    continue
                raise RuntimeError(body.get("description", str(e))) from None
        raise RuntimeError("Telegram kept saying to slow down")

    def post(self, text: str, thread: int | None = None, quiet: bool = False, **extra) -> dict | None:
        """Send to the team group (a topic, or General), splitting long text. Returns the last message sent.
        `quiet` posts it without a notification sound: only what needs the user (questions, approvals, results,
        problems, reports) pings their phone."""
        if self.chat_id is None:
            return None
        sent = None
        for i, part in enumerate(_split(text)):
            params = {"chat_id": self.chat_id, "text": part, **(extra if i == len(_split(text)) - 1 else {})}
            if quiet:
                params["disable_notification"] = True
            if thread:
                params["message_thread_id"] = thread
            try:
                sent = self._call("sendMessage", **params)
            except Exception as e:
                if thread and "thread" in str(e).lower():  # the topic was deleted: post in General instead
                    return self.post(text, None, quiet, **extra)
                print(f"[team telegram] couldn't send: {e}")
                return None
        return sent

    # So Jarvis's phone tools (send_to_phone) work from the team service too: they land in General.
    def send(self, text: str, **extra) -> dict | None:
        return self.post(text, None, **extra)

    def send_file(self, path: str, caption: str = "") -> None:
        self.api.send_document(self.chat_id, path, caption)

    def notify(self, text: str) -> None:
        self.post(f"🔔 {text}")

    # ---------- topics ----------

    def _topic(self, agent_id: str) -> int | None:
        return self.state["topics"].get(agent_id)

    def _agent_for(self, thread: int | None) -> "agents.Agent | None":
        agent_id = next((a for a, t in self.state["topics"].items() if t == thread), None) if thread else None
        return agents.find(agent_id) if agent_id else None

    def _make_topic(self, agent_id: str, name: str, role: str) -> int | None:
        if self.chat_id is None:
            return None
        try:
            topic = self._call("createForumTopic", chat_id=self.chat_id, name=f"🤖 {name} · {role}"[:128])
        except Exception as e:
            print(f"[team telegram] couldn't make a topic for {name} (is the bot an admin that can manage "
                  f"topics, with Topics on?): {e}")
            return None
        self.state["topics"][agent_id] = topic["message_thread_id"]
        live = {a.id for a in agents.everyone()}
        self.state["topics"] = {k: v for k, v in self.state["topics"].items() if k in live or k == agent_id}
        self._save()
        return topic["message_thread_id"]

    def _all_topic(self) -> int | None:
        if self.state.get("all") is None and self.chat_id is not None:
            try:
                self.state["all"] = self._call("createForumTopic", chat_id=self.chat_id,
                                               name="📜 All messages")["message_thread_id"]
                self._save()
            except Exception as e:
                print(f"[team telegram] couldn't make the All messages topic: {e}")
        return self.state.get("all")

    # ---------- what the team does -> Telegram ----------

    def _name(self, agent_id: str) -> str:
        if agent_id in ("user", "jarvis"):
            return "You" if agent_id == "user" else "Jarvis"
        agent = agents.find(agent_id)
        return agent.name if agent else agent_id

    def _follow(self) -> None:
        listener = events.subscribe()
        while True:
            event = listener.get()
            try:
                self._on_event(event)
            except Exception:
                traceback.print_exc()

    def _on_event(self, e: dict) -> None:
        if self.chat_id is None:
            return
        kind = e["type"]
        if kind == "cleared":  # finished agents taken off the team: close their topics (history stays readable)
            for agent_id in e.get("agents", []):
                thread = self.state["topics"].pop(agent_id, None)
                if thread and thread not in self.state["topics"].values():  # not a specialist's, shared by its helper
                    try:
                        self._call("closeForumTopic", chat_id=self.chat_id, message_thread_id=thread)
                    except Exception as err:
                        print(f"[team telegram] couldn't close a topic: {err}")
            self._save()
            return
        if kind == "agent" and e.get("parent"):  # a specialist's helper: lives quietly in the specialist's topic
            parent = agents.find(e["parent"])
            thread = self._topic(parent.id) if parent else None
            if thread:
                self.state["topics"][e["agent"]] = thread
                self._save()
            self.post(f"🧩 Started {e['name']}: {_short_task(e['task'])}", thread, quiet=True)
            return
        if kind == "agent":
            thread = self._make_topic(e["agent"], e["name"], e["role"])
            if e.get("kind") == "standing":
                welcomed = self.state.setdefault("welcomed", [])
                if e["name"] in welcomed:  # a specialist back after a restart: no second welcome
                    return
                welcomed.append(e["name"])
                self._save()
                self.post(f"⭐ {e['name']} is on your team for good: {e['role']}.\n\nWrite here any time to talk "
                          "to it. It keeps notes on what it learns, and its scheduled reports show up here.", thread, quiet=True)
            else:
                self.post(f"📋 {e['name']} ({e['role']}) is on it.\n\nTask:\n{e['task']}\n\n"
                          "Write here to talk to it.", thread, quiet=True)
            self.post(f"🆕 {e['name']} joined the team: {e['role']}", self._all_topic(), quiet=True)
        elif kind == "message":
            sender, to, text = e["sender"], e["to"], e["text"]
            if e.get("kind") == "update":
                if self._topic(sender):
                    self.post(f"💭 {text}", self._topic(sender))
                return
            if to == "user":
                self.post(f"❓ {text}\n\n(Reply here to answer.)", self._topic(sender))
                self.post(f"❓ {self._name(sender)} asked you: {text}", self._all_topic(), quiet=True)
                return
            self.post(f"{self._name(sender)} → {self._name(to)}: {text}", self._all_topic(), quiet=True)
            if self._topic(sender):
                self.post(f"➡️ To {self._name(to)}: {text}", self._topic(sender), quiet=True)
            if self._topic(to) and e.get("via") != "telegram":  # what you typed there is already there
                origin = {"jarvis": "You (via Jarvis)", "dashboard": "You (on the dashboard)"}.get(
                    e.get("via", ""), self._name(sender))
                self.post(f"💬 From {origin}: {text}", self._topic(to), quiet=True)
        elif kind == "result" and getattr(agents.find(e["agent"]), "parent", ""):
            self.post(f"🧩 {self._name(e['agent'])} is done.", self._topic(e["agent"]), quiet=True)  # the specialist reports
        elif kind == "result":
            self.post(f"✅ Result:\n\n{e['text']}", self._topic(e["agent"]))
            self.post(f"✅ {self._name(e['agent'])} finished.", self._all_topic(), quiet=True)
        elif kind == "status" and getattr(agents.find(e["agent"]), "parent", ""):
            return  # a helper's ups and downs don't rename its specialist's topic
        elif kind == "status" and e["status"] in MARKS:
            agent, thread = agents.find(e["agent"]), self._topic(e["agent"])
            if e["status"] == "failed":
                self.post(f"⚠️ It ran into a problem: {e.get('note', '')}", thread)
            elif e["status"] == "stopped":
                self.post("⏹ Stopped.", thread, quiet=True)
            if agent and thread:
                try:
                    self._call("editForumTopic", chat_id=self.chat_id, message_thread_id=thread,
                               name=f"{MARKS[e['status']]} {agent.name} · {agent.role}"[:128])
                    self.state.setdefault("marked", [])
                    if e["agent"] not in self.state["marked"]:
                        self.state["marked"].append(e["agent"])
                        self._save()
                except Exception:
                    pass
        elif kind == "status" and e["agent"] in self.state.get("marked", []):  # a standing agent is back
            agent, thread = agents.find(e["agent"]), self._topic(e["agent"])
            self.state["marked"].remove(e["agent"])
            self._save()
            if agent and thread:
                try:
                    self._call("editForumTopic", chat_id=self.chat_id, message_thread_id=thread,
                               name=f"🤖 {agent.name} · {agent.role}"[:128])
                    self.post("▶️ Back on the team.", thread, quiet=True)
                except Exception:
                    pass
        elif kind == "approval":
            thread = self._topic(e.get("agent", ""))
            text = f"🔐 {e['who']} wants your OK: {e['question']}"
            sent = self.post(text, thread, reply_markup={"inline_keyboard": [[
                {"text": "Yes", "callback_data": f"ap:{e['approval']}:yes"},
                {"text": "No", "callback_data": f"ap:{e['approval']}:no"}]]})
            if sent:
                self._approvals[e["approval"]] = (thread, sent["message_id"], text)
        elif kind == "approval_done" and e["approval"] in self._approvals:
            _, message_id, text = self._approvals.pop(e["approval"])
            try:
                self._call("editMessageText", chat_id=self.chat_id, message_id=message_id,
                           text=f"{text}\n\n{'✅ Yes' if e['approved'] else '❌ No'}")
            except Exception:
                pass

    # ---------- Telegram -> the team ----------

    def _poll(self) -> None:
        offset, backoff = None, 1
        while True:
            try:
                updates = self._call("getUpdates", http_timeout=60, timeout=40, offset=offset,
                                     allowed_updates=["message", "callback_query"])
                backoff = 1
            except Exception as e:
                print(f"[team telegram] connection problem ({e}); retrying in {backoff}s")
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue
            for update in updates:
                offset = update["update_id"] + 1
                try:
                    self._handle(update)
                except Exception:
                    traceback.print_exc()

    def _reply(self, message: dict, text: str) -> None:
        params = {"chat_id": message["chat"]["id"], "text": text[:MAX_MESSAGE]}
        if message.get("is_topic_message"):
            params["message_thread_id"] = message["message_thread_id"]
        try:
            self._call("sendMessage", **params)
        except Exception as e:
            print(f"[team telegram] couldn't reply: {e}")

    def _handle(self, update: dict) -> None:
        if "callback_query" in update:
            query = update["callback_query"]
            if query["from"]["id"] != self.allowed:
                return
            self._call("answerCallbackQuery", callback_query_id=query["id"])
            prefix, approval, choice = (query.get("data", "").split(":") + ["", ""])[:3]
            if prefix == "ap":
                approvals.decide(approval, choice == "yes")
            return

        message = update.get("message") or {}
        sender = (message.get("from") or {}).get("id")
        if not sender or sender != self.allowed:
            return
        chat = message.get("chat") or {}
        text = (message.get("text") or "").strip()
        command, _, arg = text.partition(" ")
        command = command.split("@")[0].lower() if text.startswith("/") else ""

        if chat.get("id") != self.chat_id:
            if command == "/setup" and chat.get("is_forum"):
                self.state.update(chat=chat["id"], topics={}, all=None)
                self._save()
                self._reply(message, "✅ This is now your agent team's chat. Each new agent gets its own topic "
                                     "here.\n\n" + HELP)
                self._all_topic()
            elif command == "/setup":
                self._reply(message, "Turn on Topics for this group first (group settings), make me an admin "
                                     "that can manage topics, then send /setup again.")
            elif chat.get("type") == "private":
                self._reply(message, "I run your agent team. Add me to a group with Topics turned on, make me "
                                     "an admin that can manage topics, and send /setup there.")
            return

        thread = message.get("message_thread_id") if message.get("is_topic_message") else None
        here = self._agent_for(thread)
        attached = incoming_file(message)
        if command:
            self._command(message, command, arg.strip(), here)
        elif here is None:  # General (or All messages): it goes to the agent it starts with the name of
            self._route_by_name(message, attached, (message.get("caption") or "") if attached else text)
        elif attached:
            if here.status in agents.FINISHED:
                self._reply(message, f"{here.name} has finished. Start a new agent with /new.")
            else:  # downloading can take a while: not on the thread that reads messages
                threading.Thread(target=self._pass_file, args=(message, attached, here), daemon=True).start()
        elif not text:
            self._reply(message, "I can pass on text, photos, videos and files to agents.")
        elif here.status in agents.FINISHED:
            self._reply(message, f"{here.name} has {here.status if here.status != 'done' else 'finished'}. "
                                 "Start a new agent with /new.")
        else:
            result = agents.deliver("user", here.id, text, via="telegram")
            if not result.startswith("Sent"):
                self._reply(message, result)

    def _route_by_name(self, message: dict, attached: tuple | None, words: str) -> None:
        """A message or file in General goes to the agent whose name it starts with ("Video Editor: cut this")."""
        agent, rest = addressed(words)
        if agent is None:
            names = ", ".join(a.name for a in agents.active()) or "none yet (start one with /new)"
            self._reply(message, ("Start the caption with an agent's name" if attached else
                                  "Start with an agent's name") +
                        f" to send it to that agent, e.g. \"Video Editor: make this vertical\".\nAgents: {names}")
            return
        if attached:
            threading.Thread(target=self._pass_file, args=(message, attached, agent, rest, True), daemon=True).start()
            return
        if not rest:
            self._reply(message, f"What should I tell {agent.name}?")
            return
        result = agents.deliver("user", agent.id, rest, via="telegram")
        self._reply(message, f"➡️ Sent to {agent.name}; its answer will be in its topic." if result.startswith("Sent")
                    else result)

    def _pass_file(self, message: dict, attached: tuple[str, str, str], agent, caption: str | None = None,
                   confirm: bool = False) -> None:
        """Save a photo, video or file sent in an agent's topic, and give that agent its path (and the caption)."""
        file_id, name, kind = attached
        try:
            path = save_incoming(self.api, file_id, name)
        except RuntimeError as e:
            self._reply(message, str(e))
            return
        caption = (message.get("caption") or "").strip() if caption is None else caption.strip()
        what = {"photo": "a photo", "video": "a video"}.get(kind, "a file")
        note = f"[The user sent {what} on Telegram, saved at {path}. Open it with view_file.]"
        result = agents.deliver("user", agent.id, note + (f"\n{caption}" if caption else ""), via="telegram")
        if not result.startswith("Sent"):
            self._reply(message, result)
        elif confirm:
            self._reply(message, f"➡️ Sent to {agent.name}; its answer will be in its topic.")

    def _command(self, message: dict, command: str, arg: str, here) -> None:
        if command in ("/start", "/help"):
            self._reply(message, HELP)
        elif command == "/team":
            self._reply(message, agents.team_status())
        elif command == "/clear":
            self._reply(message, agents.clear_finished())
        elif command == "/new":
            if not arg:
                self._reply(message, "Say what to do: /new Name | role | task, or just /new task.")
                return
            parts = [p.strip() for p in arg.split("|")]
            if len(parts) >= 3:
                name, role, task = parts[0], parts[1], " | ".join(parts[2:])
            else:
                name, role, task = f"Agent {len(agents.everyone()) + 1}", "Works on the task it was given", arg
            self._reply(message, agents.create_agent(name, role, task))
        elif command == "/code":
            repo, _, task = arg.partition("|")
            if not task.strip():
                from team import coding

                self._reply(message, "Say which repository and what to do: /code repo | task. Repositories: "
                                     f"{', '.join(coding.repo_names()) or 'none yet (code_repos.json)'}.")
                return
            coders = sum(a.kind == "coder" for a in agents.everyone())
            self._reply(message, agents.create_agent(f"Coder {coders + 1}", "Writes code for the task it was given",
                                                     task.strip(), kind="coder", repo=repo.strip()))
        elif command == "/stop":
            target = arg or (here.id if here else "")
            self._reply(message, agents.stop_agent(target) if target else "Stop which agent? /stop name, or /stop all.")
        elif command == "/result":
            target = arg or (here.id if here else "")
            self._reply(message, agents.agent_result(target) if target else "Whose result? /result name")
        else:
            self._reply(message, "I don't know that command. /help shows what I can do.")

    def start(self) -> None:
        try:
            self._call("setMyCommands", commands=[{"command": c, "description": d} for c, d in COMMANDS])
        except Exception as e:
            print(f"[team telegram] couldn't register the command menu: {e}")
        threading.Thread(target=self._poll, daemon=True, name="team-telegram-poll").start()
        threading.Thread(target=self._follow, daemon=True, name="team-telegram-follow").start()
        print("[team telegram] on" + ("" if self.chat_id else " (send /setup in your team group to finish)"))


class DirectMessages:
    """Without a team bot: agents' questions and results go to your Jarvis bot chat (sending only, so this never
    competes with Jarvis's own bot for your messages)."""

    def __init__(self, token: str, chat_id: int) -> None:
        self.api, self.chat_id = TelegramAPI(token), chat_id

    def send(self, text: str, **extra) -> dict | None:
        try:
            return self.api.call("sendMessage", chat_id=self.chat_id, text=text[:MAX_MESSAGE], **extra)
        except Exception as e:
            print(f"[team telegram] couldn't send: {e}")
            return None

    def send_file(self, path: str, caption: str = "") -> None:
        self.api.send_document(self.chat_id, path, caption)

    def notify(self, text: str) -> None:
        self.send(f"🔔 {text}")

    def _follow(self) -> None:
        listener = events.subscribe()
        while True:
            e = listener.get()
            agent = agents.find(e.get("agent") or e.get("sender") or "")
            if agent is None:
                continue
            if e["type"] == "message" and e["to"] == "user":
                text = f"❓ {agent.name} asks: {e['text']}\n\n(Answer through Jarvis or the dashboard.)"
            elif e["type"] == "message" and e["to"] == "jarvis" and e.get("kind") != "update":
                text = f"🤖 {agent.name}: {e['text']}"
            elif e["type"] == "result":
                text = f"✅ {agent.name} finished:\n\n{e['text']}"
            elif e["type"] == "status" and e["status"] == "failed":
                text = f"⚠️ {agent.name} ran into a problem: {e.get('note', '')}"
            else:
                continue
            for part in _split(text):
                self.send(part)

    def start(self) -> None:
        threading.Thread(target=self._follow, daemon=True, name="team-telegram-dm").start()


def start() -> None:
    """Start the team's Telegram side, as set up in .env (nothing if Telegram isn't set up at all)."""
    allowed = _env("TELEGRAM_ALLOWED_USER_ID")
    allowed_id = int(allowed) if allowed.isdigit() else None
    team_token = _env("TEAM_TELEGRAM_BOT_TOKEN")
    if team_token and allowed_id:
        bot = TeamBot(team_token, allowed_id)
    elif _env("TELEGRAM_BOT_TOKEN") and allowed_id:
        bot = DirectMessages(_env("TELEGRAM_BOT_TOKEN"), allowed_id)
    else:
        return
    bot.start()
    import telegram_bot

    telegram_bot.active = bot  # so send_to_phone from an agent reaches you too
