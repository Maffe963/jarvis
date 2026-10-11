import os

MAX_READ_CHARS = 20000
_DEFAULT_FOLDERS = ("Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos")


def allowed_folders() -> list[str]:
    """Folders Jarvis may use without asking: JARVIS_FILE_FOLDERS (separated by ';'), else the usual user folders."""
    configured = os.environ.get("JARVIS_FILE_FOLDERS", "").strip()
    if configured:
        folders = [os.path.expanduser(p.strip()) for p in configured.split(";") if p.strip()]
    else:
        home = os.path.expanduser("~")
        folders = [os.path.join(base, name) for base in (home, os.path.join(home, "OneDrive"))
                   for name in _DEFAULT_FOLDERS]
    return [os.path.realpath(f) for f in folders if os.path.isdir(f)]


def resolve(path: str) -> str:
    """Absolute real path; relative paths are taken from the user's home folder."""
    home = os.path.expanduser("~")
    path = os.path.expanduser(path or ".")
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


def is_allowed(path: str) -> bool:
    real = os.path.normcase(resolve(path))
    for folder in allowed_folders():
        folder = os.path.normcase(folder)
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
