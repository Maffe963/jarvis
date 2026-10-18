# Jarvis

A voice assistant for your Windows PC. Say "hey Jarvis", then ask it to open apps, switch windows,
control media and volume, set timers, check system status, search the web, or work with files.

## How it works

| Part | Runs on | Notes |
|---|---|---|
| Wake word ("hey Jarvis") | Your PC | openWakeWord, offline |
| Speech to text | Your PC | faster-whisper `base.en`, offline |
| Understanding and tool use | Claude | Through Claude Code on your Claude plan, or the Anthropic API |
| PC control tools | Your PC | `tools/` |
| Voice | Your PC | Windows' built-in voice, or ElevenLabs if you add a key |

Risky actions (closing apps, shutdown, restart, PowerShell, overwriting files) are always confirmed
with you first.

## Setup

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
copy .env.example .env
```

Then choose how Jarvis reaches Claude in `.env`:

- `JARVIS_BACKEND=subscription` (default): uses [Claude Code](https://claude.com/claude-code), signed
  in with your Claude plan. Install it, run `claude` once and sign in with `/login`. Usage counts
  against your plan's limits. Jarvis keeps one Claude Code process running, so replies take about
  1 to 3 seconds.
- `JARVIS_BACKEND=api`: uses `ANTHROPIC_API_KEY` from console.anthropic.com, billed per request.

## Running

```bash
.venv/Scripts/python.exe main.py          # voice mode
.venv/Scripts/python.exe main.py --text   # type instead of talking
```

Say "hey Jarvis" to start a conversation, then just keep talking: after each reply Jarvis listens
for your next request without the wake word. It goes back to waiting for "hey Jarvis" after 10
seconds of quiet, or when you say "that's all" or "go to sleep" (you'll hear a low beep). Say
"hey Jarvis" while it's talking to cut it off. Say "goodbye" or press Ctrl+C to quit.

Jarvis can also set reminders ("remind me tomorrow at 9 to email Professor Lee"). They're kept in
`memory/reminders.json`, and any that came due while Jarvis was off are announced when it starts.

## Email and Discord

Jarvis can send and read Gmail and Discord ("email Mom that I'll be late", "any new emails?",
"what's new in the study group?"). Every send is read back to you and needs a yes. Messages it reads
are passed to Claude marked as untrusted, so instructions hidden inside them aren't followed.

- **Gmail:** turn on 2-Step Verification, create an app password (Google Account > Security > App
  passwords), and put `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` in `.env`. Reading uses IMAP, which is
  on by default for Gmail; nothing gets marked as read.
- **Discord channel (send only):** in a server you manage, Channel settings > Integrations > Webhooks >
  New Webhook > Copy URL, then set `DISCORD_WEBHOOK_URL`. Say "post to my channel".
- **Discord bot (send, DM and read):** at discord.com/developers create an application, add a Bot, copy
  its token into `DISCORD_BOT_TOKEN`, and turn on **Message Content Intent** (needed to read messages).
  Invite it to your server (OAuth2 > URL Generator > `bot` scope, with Send Messages and Read Message
  History). People must share a server with the bot to get DMs from it. It sends as the bot, not as you.

Names are looked up in `contacts.json` (copy `contacts.example.json`), or just tell Jarvis "save Mom's
email as ...". Discord IDs come from right-clicking a user or channel with Developer Mode on (Discord
Settings > Advanced).

## Agent team

Ask Jarvis to put agents on a bigger job ("get a researcher and a writer on a report about home batteries").
Each agent is its own Claude conversation with a role; they message each other, ask you when they need a
decision, and finish with a report. Say "open the dashboard" to watch them live.

The team runs in its own process (`team_service.py`), which Jarvis starts when it isn't running and which
keeps going after Jarvis turns off, so agents carry on working and talking to each other. Agents, their
conversations and every message are saved in `memory/team/`, so they also pick up again after a restart.
Jarvis can read every message the agents sent or received ("what has the team been saying?"), and when it
comes back on it tells you what the team sent while it was away. To have the team start at sign-in even
when Jarvis doesn't: `.venv/Scripts/python.exe startup.py install-team`.

**Each agent's own Telegram chat:** create a second bot with @BotFather and put its token in
`TEAM_TELEGRAM_BOT_TOKEN` (it also needs `TELEGRAM_ALLOWED_USER_ID`). Make a Telegram group, turn on Topics in
its settings, add the bot as an admin that can manage topics, and send `/setup` in the group. Every new agent
then gets its own topic with its task, messages, updates and result; whatever you write there goes to that
agent, and approvals have Yes/No buttons. The "All messages" topic shows everything agents say to each other.
`/new Name | role | task` starts an agent from Telegram, even while Jarvis is off; `/team`, `/stop` and
`/result` do what they say. Without a team bot, agents' questions and results go to your Jarvis bot chat.

**Reviews:** when an agent writes files or runs commands, a Reviewer agent (started automatically, read-only)
checks that work for safety, correctness and efficiency before the agent can hand it in, and sends back
anything to fix; after 3 rounds the open concerns go in the agent's report.

## Starting with Windows

```bash
.venv/Scripts/python.exe startup.py install     # start Jarvis when you sign in
.venv/Scripts/python.exe startup.py uninstall   # stop doing that
```

At sign-in Jarvis runs in the background (`main.py --tray`) with an icon by the clock: right-click
it to mute the microphone, open the log (`memory/jarvis.log`), or quit. Only one Jarvis runs at a
time; starting another says it's already running.

## If Jarvis doesn't hear you

Run the microphone test and say "hey Jarvis" when each mic is tested:

```bash
.venv/Scripts/python.exe mic_test.py
```

Then in `.env`:

- `JARVIS_MIC`: the mic to use, by number or part of its name (e.g. `Blue Snowball`).
- `JARVIS_WAKE_THRESHOLD` (default 0.5): lower it if "hey Jarvis" isn't picked up; raise it if Jarvis
  wakes up on its own.
- `JARVIS_SPEECH_THRESHOLD` (default 0.015): lower it if Jarvis says `[nothing heard]` after waking.

Turning up the mic's input volume in Windows Sound settings works better than lowering thresholds.
After each recording Jarvis prints a line like `[mic] recorded 2.3s, loudest 0.012` to help tune these.

## Other settings

- `JARVIS_FOLLOWUP_SECONDS` (default 10): how long Jarvis waits for a follow-up before needing
  "hey Jarvis" again. `0` = say it every time; `-1` = stay listening until you say "that's all".
- `JARVIS_FILE_FOLDERS`: folders Jarvis may read and write without asking, separated by `;`.
  Default: Desktop, Documents, Downloads, Pictures, Music, Videos (including OneDrive copies).
  Anything else is confirmed with you first.

- `JARVIS_MODEL`: in subscription mode an alias like `sonnet` or `opus` (default `sonnet`); in API
  mode a full model ID (default `claude-sonnet-5-5`).
- `JARVIS_EFFORT`: subscription mode only, `low` (default, fastest) up to `max`.
- `ELEVENLABS_API_KEY` / `ELEVENLABS_VOICE_ID`: a more natural voice.

## Reviewing changes to Jarvis

`REVIEW.md` is the checklist for changes to this code (safety first, then correctness and efficiency).
`.github/workflows/claude-review.yml` has Claude review every pull request against it once the PR is out of
draft. It needs one repository secret: run `claude setup-token` and add the token as `CLAUDE_CODE_OAUTH_TOKEN`
(Settings > Secrets and variables > Actions). Without it the review step is skipped.

## Layout

- `main.py`: voice and text loops, confirmations
- `brain.py`: talks to Claude (API or Claude Code)
- `mcp_server.py`, `tools/bridge.py`: let Claude Code call Jarvis's tools in subscription mode
- `tools/`: everything Jarvis can do (`registry.py` lists them)
- `team_service.py`, `team/`: the agent team, its message log and its Telegram topics; `team/client.py` is
  Jarvis's side of it
- `dashboard/`: the live agent dashboard
- `voice/`: microphone, wake word, speech to text, text to speech
- `memory/`: conversation history (`history.json`, not committed)
