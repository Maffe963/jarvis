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

**Coders:** for programming, Jarvis starts coder agents ("get two coders on adding dark mode to my site").
List the repositories they may work on, and the checks they may run (tests, lint), in `code_repos.json` (copy
`code_repos.example.json`), and put a GitHub token in `GITHUB_TOKEN` (fine-grained, with Contents and Pull
requests read/write on those repositories). Each coder works in its own copy of the repository
(`memory/team/worktrees/`) on its own `agents/...` branch, so several can work at once and your own checkout is
never touched. They can only change files in that copy and only run the listed checks. When done they push and open
a pull request; the Reviewer reads it, runs the checks, comments, and merges it into main once it's good (it only
merges the team's own pull requests, never with failing checks or conflicts). Set `JARVIS_AGENT_MERGE=ask` to
approve each merge from your phone first. From Telegram: `/code repo | task`.

**Standing agents:** specialists that stay on the team for good, defined in `standing_agents.json`. They never
finish: each message or scheduled job is a new piece of work, and between jobs nothing runs, so they cost nothing
while idle. Each keeps a notebook (`memory/team/notebooks/`) as its long-term memory, has its own Telegram topic to
chat in, and doesn't count towards the 5-agent limit. Talk to one in its topic, or through Jarvis ("tell my Trade
Analyst I'm taking Friday off").

- **Ghostwriter** learns how you write from your sent Gmail and checks your inbox 5 times a day. Each reply it
  drafts comes to its topic with Send / No buttons and is only sent if you tap Send. Needs Gmail set up.
- **Trade Analyst** reads your TradeLog trades and journal (read only): a recap after the close on days you
  traded, and a deeper weekly review on Saturday mornings. Needs the four `TRADELOG_*` settings in `.env`.
- **Portfolio Advisor** keeps the holdings you tell it about ("I bought 10 NVDA at 120"), gets prices from Stooq,
  and reviews your portfolio every Sunday at 6 PM.
- **Real Estate Advisor** helps you plan a Section 8 and Airbnb business on demand, and keeps your plan in its notes.
- **Job Hunter** learns your resume and what you want, searches three times every weekday (8:30, 12:30, 5:30:
  Adzuna, remote boards, your target companies' own job boards and the web) and only messages you about strong
  matches (8/10 or better), so you can apply early. For a job you pick, it writes a tailored cover letter and resume
  changes into `Documents\Jarvis\Jobs\`; you submit the application. It tracks every application and reminds you
  to follow up. Optional: a free Adzuna key in `.env` for broader search.
- **Travel Planner** is your travel deal expert. It compares flights and stays (hotels, Airbnbs, rentals) in cash,
  points or both, working out cents per point for every points option, and knows the Chase and Amex transfer partners
  and sweet spots. Every morning it scans flight-deal and points-news feeds and tells you only when something is
  worth it: a new Chase or Amex transfer bonus that makes a trip you want cheaper, or a great fare or award to a
  place on your wishlist, with the numbers and exact steps to book. It keeps your points balances and wishlist, and
  re-checks prices on trips you watch. It never books, pays or transfers points: you do.
- **Video Editor** makes TikToks and Reels from your footage: a strong hook, fast cuts, bold captions in the safe
  zone, captions and hashtags to post with. For bigger jobs (several clips from one long video) it splits the work
  between up to 3 helpers that cut at the same time, then checks and hands you all of them.

Specialists only ping you when something needs you: a question, a Yes/No approval, a finished result, or a scheduled
report with something worth telling you (a check that finds nothing stays silent). Everything else (agents talking
to each other, status changes) still shows in the group and on the dashboard, but arrives silently.

Change instructions or schedules in `standing_agents.json`, then send `/restart team`. Set `"enabled": false` to turn
one off. `/stop` in an agent's topic pauses it until the team restarts. They give information and analysis, not
financial, legal or tax advice.

**Reviews:** when an agent writes files or runs commands, a Reviewer agent (started automatically, read-only)
checks that work for safety, correctness and efficiency before the agent can hand it in, and sends back
anything to fix; after 3 rounds the open concerns go in the agent's report.

## The dashboard from your phone

The dashboard only listens on the PC itself. To open it from your phone anywhere, put it on your own private
network with [Tailscale](https://tailscale.com) (free): install it on the PC and your phone and sign in to both
with the same account, then on the PC run `tailscale serve --bg 8765`. It shows an address like
`https://my-pc.tail1234.ts.net`; put that in `.env` as `JARVIS_DASHBOARD_URL` and restart Jarvis. Send `/dashboard`
to your Jarvis bot (or ask Jarvis to "send me the dashboard link") to get the link. Only your own devices on
Tailscale can reach it, and the link carries a secret key that stays the same across restarts; keep it private,
since the dashboard can give Jarvis commands. To change the key, delete `memory/dashboard_token` and restart. The
dashboard runs while Jarvis is on; when Jarvis is off, use the team's Telegram topics.

**From any browser (a live website):** to open it on a computer that isn't yours, or share nothing but a
password, set `JARVIS_DASHBOARD_PASSWORD` in `.env` to a long password and run `tailscale funnel --bg 8765` instead
of `tailscale serve` (Funnel puts the same `https://...ts.net` address on the public internet, with HTTPS). Anyone
opening the address gets a sign-in page; after 5 wrong passwords sign-in pauses for 15 minutes, and Jarvis tells
you about wrong tries in the dashboard's feed. Signing in lasts 30 days on that browser (delete
`memory/dashboard_sessions.json` to sign everyone out). The secret link from `/dashboard` still works without the
password. Since the dashboard can give Jarvis commands, use a password you don't use anywhere else, or keep to
`tailscale serve` if you only need your own devices.

**The panel's tabs** (right side, or below the map on a phone; Shift+1-7 switches): **Feed** is the live
activity log; **Tasks** what each agent is doing right now, background tasks, scheduled jobs and reminders;
**Sessions** your Claude Code sessions on the PC (active now and recent, with tokens) and Jarvis's own; **Usage**
your plan's 5-hour and weekly limits, tokens per day and who used them; **Projects** Jarvis, your code repos,
projects and any folder you've used Claude Code in, with commits, open pull requests, agents and sessions on each;
**PC** CPU, memory, disk and the busiest processes, with Jarvis's own marked; **Memory** what Jarvis remembers,
your lists, reminders and each agent's notebook. The chip in the header shows your tightest plan limit.

**Sending files:** attach files to a command with 📎 next to the command box, by dropping them on the page, or by
pasting an image into the box; they show as chips until you press Send. They're saved in `Downloads\From phone`
(like files sent on Telegram, never overwriting anything; up to 50 MB each) and Jarvis is told where they are. An
agent's drawer has the same 📎 for files meant for that agent.

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
- `JARVIS_CAVEMAN`: a test of [Caveman](https://github.com/JuliusBrussee/caveman) (terse replies to save tokens). `test` (default): for `JARVIS_CAVEMAN_DAYS` days (default 3) each Claude session randomly has it on or off, the dashboard's Usage tab compares them, and the results go to your phone when it ends. `on` keeps it on; `off` skips it.
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
