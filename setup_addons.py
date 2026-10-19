"""Add-ons for your own Claude Code on this PC (run once: `python setup_addons.py`; `--dry-run` shows what it would do;
`--unattended` skips the question about a GitHub token).

Installs, for your Claude Code (not Jarvis's sessions, which run with these off):
  - Anthropic's document and example skills (Word, PDF, PowerPoint, Excel files; skill-creator, mcp-builder, ...)
  - Superpowers (plan, test and debug step by step), code-review, hookify, pyright-lsp (+ Pyright itself)
  - Context7 (current library docs) and the GitHub MCP server (with a read-only token you create)
  - ccstatusline (model, context and usage in Claude Code's status line)
  - Jarvis hooks (claude_hooks.py): Jarvis says when Claude Code is waiting on you, and a guard that blocks
    recursive deletes of a drive, your home folder or Windows

Safe to run again: anything already set up is left as it is. ~/.claude/settings.json is backed up before it changes.
"""
import getpass
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")
MARKETPLACES = ["anthropics/skills", "anthropics/claude-plugins-official"]
PLUGINS = ["document-skills@anthropic-agent-skills", "example-skills@anthropic-agent-skills",
           "superpowers@claude-plugins-official", "code-review@claude-plugins-official",
           "hookify@claude-plugins-official", "pyright-lsp@claude-plugins-official",
           "context7@claude-plugins-official", "github@claude-plugins-official"]
STATUS_LINE = {"type": "command", "command": "npx -y ccstatusline@latest", "padding": 0}
TOKEN_VAR = "GITHUB_PERSONAL_ACCESS_TOKEN"
TOKEN_HELP = """
The GitHub add-on needs a token. Make it read-only, so Claude can look but never change anything:
  1. Open https://github.com/settings/personal-access-tokens/new
  2. Name it "Claude Code read-only", pick an expiry, Repository access: All repositories (or just yours)
  3. Permissions > Repository: Contents, Issues, Pull requests, Actions, Metadata: all "Read-only"
  4. Generate, copy it, and paste it below (it's saved as a Windows user setting, not in any file here)
"""

DRY = "--dry-run" in sys.argv
UNATTENDED = "--unattended" in sys.argv  # run by Jarvis on a schedule (tools/upkeep.py): nobody to type a token
done: list[str] = []
todo: list[str] = []


def run(cmd: list[str], what: str) -> bool:
    print(f"- {what}")
    if DRY:
        print("    would run: " + " ".join(cmd))
        return True
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    output = (result.stdout + result.stderr).strip().splitlines()
    if output:
        print("    " + output[-1])
    if result.returncode != 0:
        todo.append(f"{what} failed: {output[-1] if output else result.returncode}")
        return False
    done.append(what)
    return True


def plugins(claude: str) -> None:
    for source in MARKETPLACES:
        run([claude, "plugin", "marketplace", "add", source], f"marketplace {source}")
    for plugin in PLUGINS:
        run([claude, "plugin", "install", plugin], f"plugin {plugin.split('@')[0]}")


def pyright() -> None:
    if shutil.which("pyright-langserver"):
        print("- Pyright: already installed")
        return
    npm = shutil.which("npm")
    if npm:
        run([npm, "install", "-g", "pyright"], "Pyright (for pyright-lsp)")
    else:
        run([sys.executable, "-m", "pip", "install", "--user", "pyright"], "Pyright (for pyright-lsp)")


def github_token() -> None:
    if os.environ.get(TOKEN_VAR):
        print(f"- GitHub token: already set ({TOKEN_VAR})")
        return
    if DRY:
        print(f"- GitHub token: would ask for a read-only token and save it as {TOKEN_VAR}")
        return
    if UNATTENDED:
        todo.append("GitHub add-on: run `python setup_addons.py` once yourself to paste a read-only token")
        return
    print(TOKEN_HELP)
    token = getpass.getpass("Token (or just Enter to skip for now): ").strip()
    if not token:
        todo.append(f"GitHub add-on: create a read-only token and run this again (or set {TOKEN_VAR} yourself)")
        return
    if os.name == "nt":
        if run(["setx", TOKEN_VAR, token], "GitHub token saved as a Windows user setting"):
            todo.append("Open a new terminal before starting Claude Code, so it sees the GitHub token")
    else:
        todo.append(f"Add `export {TOKEN_VAR}=...` to your shell profile (only Windows is set up automatically)")


def _hook(command: str) -> dict:
    return {"type": "command", "command": command, "timeout": 10}


def settings() -> None:
    """The status line and Jarvis's hooks, merged into ~/.claude/settings.json (after the plugin installs wrote it)."""
    try:
        with open(SETTINGS, "r", encoding="utf-8") as f:
            current = json.load(f)
    except FileNotFoundError:
        current = {}
    except json.JSONDecodeError:
        todo.append(f"{SETTINGS} isn't valid JSON, so the status line and hooks weren't added: fix it and rerun")
        return
    updated = json.loads(json.dumps(current))
    python = sys.executable.replace("\\", "/")
    script = os.path.join(ROOT, "claude_hooks.py").replace("\\", "/")
    if "statusLine" not in updated:
        if shutil.which("npx"):
            updated["statusLine"] = STATUS_LINE
            print("- status line: ccstatusline (run `npx -y ccstatusline@latest` in a terminal to customise it)")
        else:
            todo.append("Status line: install Node.js (nodejs.org) and rerun, for ccstatusline")
    else:
        print("- status line: you already have one, left as it is")
    hooks = updated.setdefault("hooks", {})
    wanted = {"Notification": {"hooks": [_hook(f'"{python}" "{script}" notify')]},
              "PreToolUse": {"matcher": "Bash|PowerShell", "hooks": [_hook(f'"{python}" "{script}" guard')]}}
    for event, entry in wanted.items():
        if "claude_hooks.py" in json.dumps(hooks.get(event, [])):
            print(f"- Jarvis {event} hook: already there")
            continue
        hooks.setdefault(event, []).append(entry)
        print(f"- Jarvis {event} hook: " + ("says when Claude Code is waiting on you" if event == "Notification"
                                            else "blocks deleting a whole drive, your home folder or Windows"))
    if updated == current:
        return
    if DRY:
        print(f"    would update {SETTINGS}")
        return
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    if os.path.exists(SETTINGS):
        backup = f"{SETTINGS}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(SETTINGS, backup)
        print(f"    backed up the old settings to {backup}")
    with open(SETTINGS + ".tmp", "w", encoding="utf-8") as f:
        json.dump(updated, f, indent=2)
    os.replace(SETTINGS + ".tmp", SETTINGS)
    done.append("status line and Jarvis hooks")


def main() -> int:
    claude = shutil.which("claude")
    if not claude:
        print("Claude Code isn't installed (or not on PATH). Install it first: https://claude.com/claude-code")
        return 1
    print("Setting up Claude Code add-ons" + (" (dry run: nothing changes)" if DRY else "") + "\n")
    plugins(claude)
    pyright()
    github_token()
    settings()
    print("\nDone." if not todo else "\nDone, with a few things left for you:")
    for item in todo:
        print(f"  * {item}")
    if UNATTENDED:
        print("SUMMARY " + json.dumps({"done": done, "todo": todo}))
    print("\nAlso worth doing (can't be scripted):\n"
          "  * Google Workspace (Gmail, Calendar, Drive) for Claude: claude.ai > Settings > Connectors\n"
          "  * Market data for Trade Analyst and Portfolio Advisor: a free key from "
          "https://www.alphavantage.co/support/#api-key in Jarvis's .env as ALPHAVANTAGE_API_KEY, then restart Jarvis\n"
          "  * Usage report any time: `npx ccusage@latest` (daily) or `npx ccusage@latest blocks` (5-hour windows)\n"
          "Restart any open Claude Code sessions to pick everything up.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
