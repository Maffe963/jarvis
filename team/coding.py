"""Coding for the agent team: coder agents change code on their own branch and open a pull request; the Reviewer
reads pull requests, runs the checks, comments, and merges approved ones into the main branch.

Only the repositories listed in code_repos.json can be worked on (see code_repos.example.json), and only the checks
listed there can be run: agents never run arbitrary commands. Each coder works in its own git worktree under
memory/team/worktrees, on a branch named agents/<name>-<id>, so several coders can work on one repository at once
without getting in each other's way, and nobody touches your own checkout. Coders can only change files inside
their worktree, never push to the main branch and never force-push. The Reviewer only merges the team's own pull
requests (agents/ branches), only when GitHub says they can merge and no check has failed, and asks you first if
JARVIS_AGENT_MERGE=ask. Pushing and GitHub use GITHUB_TOKEN from .env (a fine-grained token with Contents and Pull
requests read and write on those repositories).
"""
import base64
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPOS_FILE = os.path.join(ROOT, "code_repos.json")
WORKTREES = os.path.join(ROOT, "memory", "team", "worktrees")
BRANCH_PREFIX = "agents/"
MAX_READ_CHARS = 30000
MAX_OUTPUT_CHARS = 6000
MAX_DIFF_CHARS = 40000


# ---------- repositories ----------

def _repos() -> dict:
    try:
        with open(REPOS_FILE, "r", encoding="utf-8") as f:
            return {name.lower(): spec for name, spec in json.load(f).items()}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def repo_names() -> list[str]:
    return sorted(_repos())


def _find_repo(name: str) -> tuple[str, dict] | None:
    """(name, settings) of a repository in code_repos.json, by name or part of it (or the only one)."""
    repos, wanted = _repos(), name.lower().strip()
    if wanted in repos:
        return wanted, repos[wanted]
    partial = [n for n in repos if wanted and wanted in n]
    if len(partial) == 1 or (not wanted and len(repos) == 1):
        found = partial[0] if partial else next(iter(repos))
        return found, repos[found]
    return None


def _repo(name: str) -> dict | None:
    found = _find_repo(name)
    return found[1] if found else None


def _clip(text: str, limit: int) -> str:
    text = text.strip() or "(nothing)"
    return text if len(text) <= limit else text[:limit] + f"\n[... cut after {limit} characters]"


def _git_env() -> dict:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")  # never wait for a password prompt nobody will see
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:  # through the environment, so the token never shows up in a command line
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
                   GIT_CONFIG_VALUE_0=f"AUTHORIZATION: basic {basic}")
    return env


def _git(cwd: str, *args: str, timeout: int = 120) -> tuple[int, str]:
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, env=_git_env(),
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return 1, f"git {args[0]} took longer than {timeout} seconds"
    except FileNotFoundError:
        return 1, "git isn't installed (or isn't on PATH)"
    return proc.returncode, (proc.stdout + ("\n" + proc.stderr if proc.stderr.strip() else "")).strip()


def _base(spec: dict) -> str:
    if spec.get("base"):
        return spec["base"]
    code, out = _git(spec["folder"], "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    return out.split("/", 1)[1] if code == 0 and "/" in out else "main"


def _github_repo(folder: str) -> str | None:
    """'owner/name' from the origin remote, if it's on GitHub."""
    code, url = _git(folder, "remote", "get-url", "origin")
    found = re.search(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", url) if code == 0 else None
    return f"{found.group(1)}/{found.group(2)}" if found else None


def _safe(handler):
    """Tool errors (a path outside the workspace, GitHub refusing) go back to the agent as a message."""
    def run(i: dict) -> str:
        try:
            return handler(i)
        except (ValueError, RuntimeError, OSError) as e:
            return str(e)
    return run


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:30] or "agent"


# ---------- GitHub ----------

def _github(method: str, path: str, body: dict | None = None, accept: str = "application/vnd.github+json"):
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise RuntimeError("GITHUB_TOKEN isn't set in .env, so I can't use GitHub")
    req = urllib.request.Request(f"https://api.github.com{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Accept": accept,
                                          "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "jarvis-agents"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        try:
            detail = json.loads(detail).get("message", detail)
        except ValueError:
            pass
        raise RuntimeError(f"GitHub said {e.code}: {detail}") from None
    if accept.endswith(("diff", "raw")):
        return raw.decode("utf-8", "replace")
    return json.loads(raw) if raw else {}


def _checks_summary(slug: str, sha: str) -> tuple[str, bool, bool]:
    """('3 passed, 1 failed: lint', any failed, any still running)."""
    runs = _github("GET", f"/repos/{slug}/commits/{sha}/check-runs?per_page=100").get("check_runs", [])
    failed = [r["name"] for r in runs if r.get("conclusion") in ("failure", "timed_out", "cancelled",
                                                                  "action_required")]
    running = [r["name"] for r in runs if r.get("status") != "completed"]
    passed = len(runs) - len(failed) - len(running)
    if not runs:
        return "no checks reported", False, False
    text = f"{passed} passed, {len(failed)} failed, {len(running)} running"
    return text + (f" (failed: {', '.join(failed)})" if failed else ""), bool(failed), bool(running)


# ---------- a coder's workspace ----------

def create_workspace(repo: str, agent_name: str, agent_id: str) -> dict | str:
    """A fresh worktree and branch from the latest main branch. Returns the workspace, or what went wrong."""
    found = _find_repo(repo)
    if found is None:
        known = ", ".join(repo_names()) or "none (add them to code_repos.json)"
        return f"No code repository called '{repo}'. Known: {known}."
    name, spec = found
    folder = spec["folder"]
    if not os.path.isdir(os.path.join(folder, ".git")):
        return f"{folder} isn't a git repository."
    base = _base(spec)
    code, out = _git(folder, "fetch", "origin", base)
    if code:
        return f"Couldn't fetch the latest {base}: {out}"
    branch = f"{BRANCH_PREFIX}{_slug(agent_name)}-{agent_id}"
    path = os.path.join(WORKTREES, f"{_slug(name)}-{agent_id}")
    os.makedirs(WORKTREES, exist_ok=True)
    code, out = _git(folder, "worktree", "add", "-b", branch, path, f"origin/{base}")
    if code:
        return f"Couldn't set up a workspace: {out}"
    return {"repo": name, "folder": folder, "path": path, "branch": branch, "base": base, "pushed": False}


def remove_workspace(ws: dict) -> str:
    """Tidy up after a coder: drop its worktree if everything was pushed. Returns a note for its report."""
    if not os.path.isdir(ws["path"]):
        return ""
    note = f"Its unpushed work is still in {ws['path']} (branch {ws['branch']})."
    code, status = _git(ws["path"], "status", "--porcelain")
    if code or status.strip():
        return note
    # Everything is committed; is all of it on GitHub (or was nothing done at all)?
    since = f"origin/{ws['branch']}..HEAD" if ws.get("pushed") else f"origin/{ws['base']}..HEAD"
    code, ahead = _git(ws["path"], "rev-list", "--count", since)
    if code or ahead.strip() != "0":
        return note
    _git(ws["folder"], "worktree", "remove", "--force", ws["path"])
    _git(ws["folder"], "branch", "-D", ws["branch"])
    return ""


def _inside(ws: dict, path: str) -> str | None:
    """The full path of `path` inside the workspace, or None if it would leave it (or touch .git)."""
    full = os.path.realpath(os.path.join(ws["path"], path.replace("\\", "/").lstrip("/")))
    root = os.path.realpath(ws["path"])
    if os.path.normcase(full) != os.path.normcase(root) and not os.path.normcase(full).startswith(
            os.path.normcase(root) + os.sep):
        return None
    if ".git" in os.path.relpath(full, root).replace("\\", "/").split("/"):
        return None
    return full


def _run_check(spec: dict, check: str, cwd: str) -> str:
    checks = spec.get("checks", {})
    wanted = next((n for n in checks if n.lower() == check.lower().strip()), None)
    if wanted is None:
        return f"No check called '{check}'. Checks: {', '.join(checks) or 'none set up in code_repos.json'}."
    argv = list(checks[wanted])
    if argv and argv[0].lower().endswith((".cmd", ".bat")):
        argv = ["cmd", "/c"] + argv
    try:
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=spec.get("timeout", 600), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return f"{wanted}: stopped after {spec.get('timeout', 600)} seconds"
    except FileNotFoundError as e:
        return f"{wanted}: couldn't run it ({e})"
    output = _clip(proc.stdout + ("\n" + proc.stderr if proc.stderr.strip() else ""), MAX_OUTPUT_CHARS)
    return f"{wanted}: {'passed' if proc.returncode == 0 else f'FAILED (exit code {proc.returncode})'}\n{output}"


def coder_tools(ws: dict) -> dict:
    """The coding tools for one coder, all working inside its own workspace."""
    def path_or_error(path: str) -> str:
        full = _inside(ws, path)
        if full is None:
            raise ValueError(f"'{path}' is outside your workspace; use paths relative to {ws['path']}")
        return full

    def read_code(i: dict) -> str:
        full = path_or_error(i.get("path", ""))
        if not os.path.isfile(full):
            return f"No file {i.get('path')}."
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            return _clip(f.read(MAX_READ_CHARS + 1), MAX_READ_CHARS)

    def list_code(i: dict) -> str:
        code, out = _git(ws["path"], "ls-files", "--cached", "--others", "--exclude-standard", "--",
                         i.get("folder") or ".")
        files = out.splitlines()
        return ("\n".join(files[:500]) + (f"\n[... and {len(files) - 500} more]" if len(files) > 500 else "")) \
            if code == 0 and files else (out or "No files there.")

    def search_code(i: dict) -> str:
        code, out = _git(ws["path"], "grep", "-n", "-I", "--untracked", "-F", "-e", i.get("text", ""))
        lines = out.splitlines()
        return "\n".join(lines[:150]) + (f"\n[... {len(lines) - 150} more matches]" if len(lines) > 150 else "") \
            if code == 0 else "No matches."

    def write_code(i: dict) -> str:
        full = path_or_error(i.get("path", ""))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8", newline="") as f:
            f.write(i.get("content", ""))
        return f"Wrote {i.get('path')} ({len(i.get('content', ''))} characters)."

    def edit_code(i: dict) -> str:
        full = path_or_error(i.get("path", ""))
        if not os.path.isfile(full):
            return f"No file {i.get('path')}."
        with open(full, "r", encoding="utf-8", newline="") as f:
            text = f.read()
        old = i.get("old", "")
        count = text.count(old) if old else 0
        if count != 1:
            return f"The text to replace was found {count} times in {i.get('path')}; it must be exactly once."
        with open(full, "w", encoding="utf-8", newline="") as f:
            f.write(text.replace(old, i.get("new", ""), 1))
        return f"Edited {i.get('path')}."

    def delete_code(i: dict) -> str:
        full = path_or_error(i.get("path", ""))
        if os.path.isdir(full):
            shutil.rmtree(full)
        elif os.path.isfile(full):
            os.remove(full)
        else:
            return f"Nothing at {i.get('path')}."
        return f"Deleted {i.get('path')}."

    def run_check(i: dict) -> str:
        return _run_check(_repo(ws["repo"]) or {}, i.get("check", ""), ws["path"])

    def git_status(_: dict) -> str:
        return _git(ws["path"], "status", "--short", "--branch")[1]

    def git_diff(_: dict) -> str:
        _git(ws["path"], "add", "-A")  # so new files show too (commit adds everything anyway)
        return _clip(_git(ws["path"], "diff", "--cached", f"origin/{ws['base']}")[1], MAX_DIFF_CHARS)

    def commit(i: dict) -> str:
        _git(ws["path"], "add", "-A")
        code, out = _git(ws["path"], "commit", "-m", i.get("message", "").strip() or "Changes by Jarvis's agent")
        return out if code == 0 else f"Nothing committed: {out}"

    def push(_: dict) -> str:
        code, out = _git(ws["path"], "push", "-u", "origin", f"HEAD:refs/heads/{ws['branch']}")
        if code:
            return f"Push failed: {out}"
        ws["pushed"] = True
        return f"Pushed {ws['branch']}."

    def update_from_base(_: dict) -> str:
        code, out = _git(ws["path"], "fetch", "origin", ws["base"])
        if code:
            return f"Couldn't fetch {ws['base']}: {out}"
        code, out = _git(ws["path"], "merge", "--no-edit", f"origin/{ws['base']}")
        if code:
            return f"Merge conflicts; fix the files marked with <<<<<<< and commit:\n{out}"
        return f"Up to date with {ws['base']}."

    def open_pull_request(i: dict) -> str:
        slug = _github_repo(ws["folder"])
        if not slug:
            return "This repository's origin isn't on GitHub, so there's no pull request to open."
        owner = slug.split("/")[0]
        existing = _github("GET", f"/repos/{slug}/pulls?state=open&head={owner}:{ws['branch']}")
        if existing:
            return f"Pull request #{existing[0]['number']} is already open: {existing[0]['html_url']}"
        pr = _github("POST", f"/repos/{slug}/pulls", {
            "title": i.get("title", "").strip() or ws["branch"], "head": ws["branch"], "base": ws["base"],
            "body": (i.get("body", "").strip() + "\n\n---\n_Opened by an agent on Jarvis's team._").strip()})
        ws["pr"] = pr["number"]
        return f"Opened pull request #{pr['number']}: {pr['html_url']}"

    text = {"type": "string"}

    def tool(name, description, props=None, required=None):
        return {"name": name, "description": description,
                "input_schema": {"type": "object", "properties": props or {}, "required": required or []}}

    return {name: (schema, _safe(handler)) for name, (schema, handler) in {
        "read_code": (tool("read_code", "Read a file in your workspace (path relative to the repository root).",
                           {"path": text}, ["path"]), read_code),
        "list_code": (tool("list_code", "List the repository's files (optionally only in one folder).",
                           {"folder": text}), list_code),
        "search_code": (tool("search_code", "Find lines containing some text across the repository.",
                             {"text": text}, ["text"]), search_code),
        "write_code": (tool("write_code", "Create or replace a whole file in your workspace.",
                            {"path": text, "content": text}, ["path", "content"]), write_code),
        "edit_code": (tool("edit_code", "Replace one exact piece of text in a file (it must appear exactly once). "
                           "Prefer this to rewriting whole files.", {"path": text, "old": text, "new": text},
                           ["path", "old", "new"]), edit_code),
        "delete_code": (tool("delete_code", "Delete a file or folder in your workspace.", {"path": text}, ["path"]),
                        delete_code),
        "run_check": (tool("run_check", "Run one of the repository's checks (tests, lint, ...) on your workspace. "
                           f"Checks: {', '.join((_repo(ws['repo']) or {}).get('checks', {})) or 'none set up'}.",
                           {"check": text}, ["check"]), run_check),
        "git_status": (tool("git_status", "Your branch and which files you've changed."), git_status),
        "git_diff": (tool("git_diff", f"Everything you've changed compared with {ws['base']}."), git_diff),
        "commit": (tool("commit", "Commit all your changes with a message saying what and why.",
                        {"message": text}, ["message"]), commit),
        "push": (tool("push", "Push your branch to GitHub (never the main branch)."), push),
        "update_from_base": (tool("update_from_base", f"Bring the latest {ws['base']} into your branch."),
                             update_from_base),
        "open_pull_request": (tool("open_pull_request", f"Open a pull request from your branch into {ws['base']}.",
                                   {"title": text, "body": text}, ["title", "body"]), open_pull_request),
    }.items()}


# ---------- the Reviewer's pull request tools ----------

def _review_spec(repo: str) -> tuple[dict, str]:
    spec = _repo(repo)
    if spec is None:
        raise ValueError(f"No code repository called '{repo}'. Known: {', '.join(repo_names()) or 'none'}.")
    slug = _github_repo(spec["folder"])
    if not slug:
        raise ValueError(f"{repo}'s origin isn't on GitHub.")
    return spec, slug


def reviewer_tools(ask_user) -> dict:
    """`ask_user(question) -> bool` asks the user before merging when JARVIS_AGENT_MERGE=ask."""
    def list_pull_requests(i: dict) -> str:
        _, slug = _review_spec(i.get("repo", ""))
        prs = _github("GET", f"/repos/{slug}/pulls?state=open&per_page=50")
        return "\n".join(f"#{p['number']} {p['title']} ({p['head']['ref']} -> {p['base']['ref']}, by "
                         f"{p['user']['login']}{', team' if p['head']['ref'].startswith(BRANCH_PREFIX) else ''})"
                         for p in prs) or "No open pull requests."

    def view_pull_request(i: dict) -> str:
        _, slug = _review_spec(i.get("repo", ""))
        number = int(i.get("number", 0))
        pr = _github("GET", f"/repos/{slug}/pulls/{number}")
        checks, _, _ = _checks_summary(slug, pr["head"]["sha"])
        diff = _github("GET", f"/repos/{slug}/pulls/{number}", accept="application/vnd.github.diff")
        return (f"#{number} {pr['title']} [{pr['state']}{', merged' if pr.get('merged') else ''}]\n"
                f"{pr['head']['ref']} -> {pr['base']['ref']}, by {pr['user']['login']}; "
                f"{pr['changed_files']} files, +{pr['additions']} -{pr['deletions']}; "
                f"can merge: {pr.get('mergeable')}; checks: {checks}\n\n{pr.get('body') or ''}\n\n"
                f"{_clip(diff, MAX_DIFF_CHARS)}")

    def read_pr_file(i: dict) -> str:
        _, slug = _review_spec(i.get("repo", ""))
        pr = _github("GET", f"/repos/{slug}/pulls/{int(i.get('number', 0))}")
        path = urllib.parse.quote(i.get("path", "").lstrip("/"))
        return _clip(_github("GET", f"/repos/{slug}/contents/{path}?ref={pr['head']['sha']}",
                             accept="application/vnd.github.raw"), MAX_READ_CHARS)

    def run_pr_check(i: dict) -> str:
        spec, slug = _review_spec(i.get("repo", ""))
        number = int(i.get("number", 0))
        path = os.path.join(WORKTREES, f"review-{_slug(i.get('repo', ''))}-{number}")
        code, out = _git(spec["folder"], "fetch", "origin", f"pull/{number}/head")
        if code:
            return f"Couldn't fetch pull request #{number}: {out}"
        if os.path.isdir(path):
            _git(path, "checkout", "--detach", "--force", "FETCH_HEAD")
        else:
            os.makedirs(WORKTREES, exist_ok=True)
            code, out = _git(spec["folder"], "worktree", "add", "--detach", path, "FETCH_HEAD")
            if code:
                return f"Couldn't check out pull request #{number}: {out}"
        return _run_check(spec, i.get("check", ""), path)

    def comment_on_pull_request(i: dict) -> str:
        _, slug = _review_spec(i.get("repo", ""))
        _github("POST", f"/repos/{slug}/issues/{int(i.get('number', 0))}/comments",
                {"body": i.get("text", "") + "\n\n---\n_Reviewer agent on Jarvis's team._"})
        return "Commented."

    def merge_pull_request(i: dict) -> str:
        spec, slug = _review_spec(i.get("repo", ""))
        number = int(i.get("number", 0))
        pr = _github("GET", f"/repos/{slug}/pulls/{number}")
        if pr["state"] != "open":
            return f"#{number} isn't open ({'merged' if pr.get('merged') else pr['state']})."
        if not pr["head"]["ref"].startswith(BRANCH_PREFIX) or pr["head"]["repo"]["full_name"] != slug:
            return f"#{number} wasn't made by the team (not an {BRANCH_PREFIX} branch here), so it's for the user to merge."
        if pr.get("mergeable") is False:
            return f"#{number} has merge conflicts; the coder should run update_from_base, fix them and push."
        summary, failed, running = _checks_summary(slug, pr["head"]["sha"])
        if failed:
            return f"Not merging #{number}: checks failed ({summary}). Send the failures to the coder."
        if running:
            return f"Checks on #{number} are still running ({summary}); try again in a minute or two."
        if os.environ.get("JARVIS_AGENT_MERGE", "auto").strip().lower() == "ask" and \
                not ask_user(f"Merge pull request #{number} \"{pr['title']}\" into {pr['base']['ref']}?"):
            return "The user said not to merge it."
        result = _github("PUT", f"/repos/{slug}/pulls/{number}/merge",
                         {"merge_method": "squash", "commit_title": f"{pr['title']} (#{number})"})
        try:
            _github("DELETE", f"/repos/{slug}/git/refs/heads/{pr['head']['ref']}")
        except RuntimeError:
            pass  # the branch may be deleted automatically
        review = os.path.join(WORKTREES, f"review-{_slug(i.get('repo', ''))}-{number}")
        if os.path.isdir(review):
            _git(spec["folder"], "worktree", "remove", "--force", review)
        return f"Merged #{number} into {pr['base']['ref']} ({result.get('sha', '')[:7]})."

    text, number = {"type": "string"}, {"type": "integer"}
    pr = {"repo": text, "number": number}

    def tool(name, description, props, required):
        return {"name": name, "description": description,
                "input_schema": {"type": "object", "properties": props, "required": required}}

    return {name: (schema, _safe(handler)) for name, (schema, handler) in {
        "list_pull_requests": (tool("list_pull_requests", "Open pull requests in a code repository.",
                                    {"repo": text}, ["repo"]), list_pull_requests),
        "view_pull_request": (tool("view_pull_request", "A pull request: description, checks, whether it can "
                                   "merge, and its full diff.", pr, ["repo", "number"]), view_pull_request),
        "read_pr_file": (tool("read_pr_file", "Read a whole file as it is in a pull request.",
                              {**pr, "path": text}, ["repo", "number", "path"]), read_pr_file),
        "run_pr_check": (tool("run_pr_check", "Run one of the repository's checks (tests, lint, ...) on a pull "
                              "request's code.", {**pr, "check": text}, ["repo", "number", "check"]), run_pr_check),
        "comment_on_pull_request": (tool("comment_on_pull_request", "Post a comment on a pull request (your "
                                         "review for the record).", {**pr, "text": text},
                                         ["repo", "number", "text"]), comment_on_pull_request),
        "merge_pull_request": (tool("merge_pull_request", "Merge an approved team pull request into the main "
                                    "branch (only after your review approved it).", pr, ["repo", "number"]),
                               merge_pull_request),
    }.items()}
