import os


def read_file(path: str) -> str:
    """Read and return the contents of a file."""
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        return f"File not found: {path}"
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def write_file(path: str, content: str) -> str:
    """Write content to a file, creating directories as needed."""
    path = os.path.expanduser(path)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"Written {len(content)} characters to {path}"


def list_files(directory: str = ".") -> str:
    """List files and folders in a directory."""
    directory = os.path.expanduser(directory)
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
