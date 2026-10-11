import json
import os
import re

MAX_READ_CHARS = 20000
_DEFAULT_FOLDERS = ("Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos")
GRANTS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory", "file_access.json")
# Always confirmed, even inside allowed folders: secrets, keys, and Windows itself.
ALWAYS_ASK = re.compile(
    r"(\\\.ssh\\|\\\.aws\\|\\\.gnupg\\|\\\.env$|\\\.env\.|\.pem$|\.key$|\.pfx$|\.p12$|\.kdbx$|id_rsa|id_ed25519"
    r"|^[a-z]:\\windows(\\|$)|\\appdata\\(local|roaming)\\microsoft\\(credentials|protect|vault)\\)",
    re.IGNORECASE,
)


def _grants() -> list[str]:
    try:
        with open(GRANTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def allowed_folders() -> list[str]:
    """Folders Jarvis may use without asking: JARVIS_FILE_FOLDERS (separated by ';') or the usual user
    folders, plus any the user granted by voice (grant_folder_access)."""
    configured = os.environ.get("JARVIS_FILE_FOLDERS", "").strip()
    if configured:
        folders = [os.path.expanduser(p.strip()) for p in configured.split(";") if p.strip()]
    else:
        home = os.path.expanduser("~")
        folders = [os.path.join(base, name) for base in (home, os.path.join(home, "OneDrive"))
                   for name in _DEFAULT_FOLDERS]
    return [os.path.realpath(f) for f in folders + _grants() if os.path.isdir(f)]


def grant_folder_access(folder: str) -> str:
    real = resolve(folder)
    if not os.path.isdir(real):
        return f"There's no folder at {real}."
    grants = _grants()
    if real in grants:
        return f"Jarvis already has access to {real}."
    with open(GRANTS_FILE, "w", encoding="utf-8") as f:
        json.dump(grants + [real], f, indent=2)
    return f"Jarvis can now use files in {real} without asking (secrets like .env files and keys still ask)."


def revoke_folder_access(folder: str) -> str:
    grants = _grants()
    real = resolve(folder)
    keep = [g for g in grants if os.path.normcase(g) != os.path.normcase(real)]
    if len(keep) == len(grants):
        listed = ", ".join(grants) or "none"
        return f"{real} wasn't one of the folders you granted. Granted: {listed}."
    with open(GRANTS_FILE, "w", encoding="utf-8") as f:
        json.dump(keep, f, indent=2)
    return f"Jarvis will ask again before using files in {real}."


def list_folder_access() -> str:
    granted = _grants()
    usual = [f for f in allowed_folders() if f not in granted]
    return ("Usual folders: " + ", ".join(usual) + "\nGranted by you: " + (", ".join(granted) or "none"))


def resolve(path: str) -> str:
    """Absolute real path; relative paths are taken from the user's home folder."""
    home = os.path.expanduser("~")
    path = os.path.expanduser((path or ".").strip())
    # "E:" / "E:stuff" mean the drive's root here (not Windows' per-drive current folder),
    # and "/e/stuff" is Git Bash style for E:\stuff.
    if re.match(r"^[A-Za-z]:(?![\\/])", path):
        path = path[:2] + os.sep + path[2:]
    elif re.match(r"^/[A-Za-z](/|$)", path):
        path = path[1].upper() + ":" + os.sep + path[3:]
    if not os.path.isabs(path):
        path = os.path.join(home, path)
    # Desktop, Documents, etc. are often moved into OneDrive; follow them there.
    rel = os.path.relpath(path, home) if os.path.splitdrive(path)[0].lower() == os.path.splitdrive(home)[0].lower() else ""
    top = rel.split(os.sep)[0]
    if top in _DEFAULT_FOLDERS and not os.path.isdir(os.path.join(home, top)):
        onedrive = os.path.join(home, "OneDrive", rel)
        if os.path.isdir(os.path.join(home, "OneDrive", top)):
            path = onedrive
    return os.path.realpath(path)  # follows links and "..", so they can't escape a folder


def is_sensitive(path: str) -> bool:
    return bool(ALWAYS_ASK.search(os.path.normcase(resolve(path))))


def is_allowed(path: str) -> bool:
    real = os.path.normcase(resolve(path))
    if is_sensitive(path):
        return False
    for folder in allowed_folders():
        folder = os.path.normcase(folder).rstrip(os.sep)  # "E:\" -> "E:" so the whole drive matches
        if real == folder or real.startswith(folder + os.sep):
            return True
    return False


def read_file(path: str) -> str:
    """Read and return the contents of a file."""
    path = resolve(path)
    if not os.path.exists(path):
        return f"File not found: {path}"
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read(MAX_READ_CHARS + 1)
    if len(content) > MAX_READ_CHARS:
        return content[:MAX_READ_CHARS] + f"\n[... truncated after {MAX_READ_CHARS} characters]"
    return content


def write_file(path: str, content: str) -> str:
    """Write content to a file, creating directories as needed."""
    path = resolve(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"Written {len(content)} characters to {path}"


def list_files(directory: str = ".") -> str:
    """List files and folders in a directory."""
    directory = resolve(directory)
    if not os.path.isdir(directory):
        return f"Not a directory: {directory}"

    entries = os.listdir(directory)
    if not entries:
        return "Directory is empty."

    lines = []
    for entry in sorted(entries):
        full = os.path.join(directory, entry)
        tag = "/" if os.path.isdir(full) else ""
        lines.append(f"{entry}{tag}")
    return "\n".join(lines)
