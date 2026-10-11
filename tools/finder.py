"""Find files and folders by name, and open them.

Uses the Windows Search index where it covers a folder (near-instant), and otherwise walks the folder
directly with a time limit. Matching is by words, so "tax return 2025" finds "2025_Tax-Return.pdf".
"""
import os
import re
import time
from datetime import datetime

from tools import files

WALK_BUDGET_S = 8
SKIP_DIRS = {"appdata", "node_modules", "__pycache__", "$recycle.bin", "system volume information",
             "windows", "program files", "program files (x86)", "programdata", ".git", ".venv", "venv"}
RUNNABLE = {".exe", ".bat", ".cmd", ".com", ".ps1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".msi", ".msp",
            ".scr", ".lnk", ".url", ".reg", ".hta", ".cpl", ".jar", ".py", ".pyw"}


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[\s_\-.,()\[\]]+", text.lower()) if w]


def _matches(name: str, words: list[str]) -> bool:
    lowered = name.lower()
    return all(w in lowered for w in words)


def _search_index(roots: list[str], words: list[str], kind: str) -> list[str] | None:
    """Paths from the Windows Search index, or None if the index can't answer for these folders."""
    try:
        import win32com.client

        conn = win32com.client.Dispatch("ADODB.Connection")
        conn.Open("Provider=Search.CollatorDSO;Extended Properties='Application=Windows';")
    except Exception:
        return None

    def like(word: str) -> str:
        word = word.replace("'", "''").replace("[", "[[]").replace("%", "[%]").replace("_", "[_]")
        return f"System.FileName LIKE '%{word}%'"

    conditions = [like(w) for w in words]
    if kind == "folder":
        conditions.append("System.ItemType = 'Directory'")
    elif kind == "file":
        conditions.append("System.ItemType <> 'Directory'")
    scopes = " OR ".join(f"SCOPE='file:{r.replace(os.sep, '/')}'" for r in roots)
    query = (f"SELECT TOP 200 System.ItemPathDisplay FROM SYSTEMINDEX "
             f"WHERE ({scopes}) AND {' AND '.join(conditions)}")
    try:
        rs = win32com.client.Dispatch("ADODB.Recordset")
        rs.Open(query, conn)
        paths = []
        while not rs.EOF:
            paths.append(rs.Fields.Item(0).Value)
            rs.MoveNext()
        # Check the index actually covers these folders; an empty answer from an unindexed drive means nothing.
        if not paths:
            probe = win32com.client.Dispatch("ADODB.Recordset")
            probe.Open(f"SELECT TOP 1 System.ItemPathDisplay FROM SYSTEMINDEX WHERE {scopes}", conn)
            if probe.EOF:
                return None
        return paths
    except Exception:
        return None


def _walk(roots: list[str], words: list[str], kind: str) -> tuple[list[str], bool]:
    """Walk the folders directly. Returns (paths, finished_in_time)."""
    deadline = time.monotonic() + WALK_BUDGET_S
    found = []
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            if time.monotonic() > deadline:
                return found, False
            dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS and not d.startswith(".")]
            if kind != "file":
                found += [os.path.join(dirpath, d) for d in dirnames if _matches(d, words)]
            if kind != "folder":
                found += [os.path.join(dirpath, f) for f in filenames if _matches(f, words)]
    return found, True


def search_roots(location: str) -> list[str]:
    return [files.resolve(location)] if location.strip() else files.allowed_folders()


def find_files(query: str, kind: str = "any", location: str = "", limit: int = 10) -> str:
    words = _words(query)
    if not words:
        return "Tell me what to look for."
    roots = [r for r in search_roots(location) if os.path.isdir(r)]
    if not roots:
        return f"There's no folder at {files.resolve(location)}."
    limit = max(1, min(int(limit or 10), 25))

    paths = _search_index(roots, words, kind)
    complete = True
    if paths is None:
        paths, complete = _walk(roots, words, kind)
    if kind != "file":  # the starting folders themselves ("find my Documents folder")
        paths = [r for r in roots if _matches(os.path.basename(r), words)] + paths
    paths = [p for p in dict.fromkeys(paths)
             if "\\appdata\\" not in p.lower() and _matches(os.path.basename(p), words)]
    if not paths:
        where = location or "your usual folders"
        more = "" if complete else " (the search ran out of time; try a narrower location)"
        return f"Nothing matching '{query}' in {where}{more}."

    def rank(path: str):
        stem = os.path.splitext(os.path.basename(path))[0].lower()
        try:
            modified = os.path.getmtime(path)
        except OSError:
            modified = 0
        return (stem != query.lower().strip(), -modified)

    paths.sort(key=rank)
    lines = []
    for p in paths[:limit]:
        try:
            when = datetime.fromtimestamp(os.path.getmtime(p)).strftime("%b %d, %Y")
        except OSError:
            when = "?"
        lines.append(f"{p}  ({'folder' if os.path.isdir(p) else 'file'}, modified {when})")
    extra = f"\n...and {len(paths) - limit} more." if len(paths) > limit else ""
    partial = "" if complete else "\n(Search ran out of time, so there may be more.)"
    return "\n".join(lines) + extra + partial


def open_path(path: str, show_in_folder: bool = False) -> str:
    real = files.resolve(path)
    if not os.path.exists(real):
        return f"Nothing at {real}."
    if show_in_folder:
        import subprocess

        subprocess.Popen(["explorer.exe", "/select,", real])
        return f"Showing {os.path.basename(real)} in File Explorer."
    os.startfile(real)
    return f"Opened {os.path.basename(real) or real}."


def confirmation(name: str, inputs: dict) -> str | None:
    if name == "find_files" and inputs.get("location", "").strip():
        root = files.resolve(inputs["location"])
        if not files.is_allowed(root):
            return f"Search in {root}? It's outside your usual folders."
    if name == "open_path" and not inputs.get("show_in_folder"):
        real = files.resolve(inputs.get("path", ""))
        if os.path.splitext(real)[1].lower() in RUNNABLE:
            return f"Run {os.path.basename(real)}? It's a program or script."
        if not files.is_allowed(real):
            return f"Open {real}? It's outside your usual folders."
    return None


TOOLS = [
    {
        "name": "find_files",
        "description": "Search the user's PC for files or folders by name (words in any order). Searches their "
                       "usual folders (Desktop, Documents, Downloads, ...) unless `location` is given, e.g. "
                       "'C:\\' or 'E:\\' to search a whole drive. Results include full paths for open_path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Words in the name, e.g. 'resume' or 'tax return 2025'"},
                "kind": {"type": "string", "enum": ["any", "file", "folder"]},
                "location": {"type": "string", "description": "Folder or drive to search instead"},
                "limit": {"type": "integer", "description": "Max results (default 10)"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "open_path",
        "description": "Open a file in its default app or a folder in File Explorer. With show_in_folder, open "
                       "Explorer with the item selected instead.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path, e.g. from find_files"},
                "show_in_folder": {"type": "boolean"}
            },
            "required": ["path"]
        }
    },
]

TOOL_MAP = {"find_files": find_files, "open_path": open_path}
