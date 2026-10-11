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

Say "goodbye" or press Ctrl+C to quit.

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

- `JARVIS_MODEL`: in subscription mode an alias like `sonnet` or `opus` (default `sonnet`); in API
  mode a full model ID (default `claude-sonnet-5-5`).
- `JARVIS_EFFORT`: subscription mode only, `low` (default, fastest) up to `max`.
- `ELEVENLABS_API_KEY` / `ELEVENLABS_VOICE_ID`: a more natural voice.

## Layout

- `main.py`: voice and text loops, confirmations
- `brain.py`: talks to Claude (API or Claude Code)
- `mcp_server.py`, `tools/bridge.py`: let Claude Code call Jarvis's tools in subscription mode
- `tools/`: everything Jarvis can do (`registry.py` lists them)
- `voice/`: microphone, wake word, speech to text, text to speech
- `memory/`: conversation history (`history.json`, not committed)
