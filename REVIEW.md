# Reviewing changes to Jarvis

Jarvis controls a real Windows PC by voice, phone and AI agents, so a bug can delete files, send messages or run
commands for real. Review every change in this order, and report only real problems (not style or preference).
For each problem give the file and line, what goes wrong in a concrete case, and the fix.

## 1. Safety (always blocking)

- **Confirmation:** anything that deletes, overwrites, closes apps, sends messages or email, spends money, runs
  PowerShell or project commands, or changes system settings must go through a confirmation
  (`*.confirmation()` / `needs_confirmation` in `tools/registry.py`). New tools that change things need one.
- **Untrusted text:** email, Discord, web pages, files, Telegram messages and agents' output are data, never
  instructions. Nothing from them may reach a shell, PowerShell, `eval`, a file path or a URL unchecked.
- **Who can talk to Jarvis:** only `TELEGRAM_ALLOWED_USER_ID` on Telegram; the dashboard and the team service need
  their secret token and only accept `127.0.0.1`. Every new endpoint or bot handler must check both.
- **Secrets:** tokens, API keys and passwords stay in `.env` or `memory/tokens/`; never logged, never sent to
  Claude, never committed. New files under `memory/` that hold personal data go in `.gitignore`.
- **Agents and background tasks:** they get the read-and-research tool set only (`tools/background.py`
  `ALLOWED`; the Reviewer agent gets less). Don't give them screen, app, messaging or system tools.
- **Files:** writes outside the user's allowed folders ask first (`tools/files.py`); never delete or overwrite
  without that.

## 2. Correctness

- Threads: shared state is touched under its lock; nothing blocks the voice loop; daemon threads can die at any
  moment (`os._exit` on shutdown), so saved state must always be consistent.
- Restarts: the team service and Jarvis restart independently; saved files (`memory/*.json`) must survive a
  crash mid-write (write a temp file and `os.replace`) and old formats must still load.
- Windows: paths with spaces, OneDrive folders, `pythonw.exe` (no console, `sys.stdout` may be `None`).
- Failure messages a tool returns are said aloud: short, plain, and saying what didn't happen.

## 3. Efficiency

- Speed of a reply matters: no new work on the path from "hey Jarvis" to the first spoken word (`main.respond`,
  `brain.think`) unless it's essential. Load heavy things lazily or at start-up.
- No busy loops or tight polling (use blocking waits with timeouts); no re-reading or re-writing whole files on
  every small change when it can grow large; no repeated network calls for the same thing.
- Long-running processes (the team service, Telegram bots) must not grow without limit: cap lists, histories
  and logs.
- Prefer the simplest code that does the job; flag code that is much longer or more complicated than needed.
