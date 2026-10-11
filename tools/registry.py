"""Every tool Jarvis can use, shared by both backends (direct API and Claude Code subscription)."""
from tools.files import list_files, read_file, write_file
from tools.search import web_search
from tools.system import SYSTEM_TOOL_MAP, SYSTEM_TOOLS, needs_confirmation

TOOLS = [
    {
        "name": "web_search",
        "description": "Search the web for current information on any topic.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "read_file",
        "description": "Read the contents of a file from the filesystem.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The file path to read"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "write_file",
        "description": "Write content to a file on the filesystem.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The file path to write"},
                "content": {"type": "string", "description": "The content to write"}
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "list_files",
        "description": "List files in a directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "directory": {"type": "string", "description": "Directory path to list (defaults to current directory)"}
            },
            "required": []
        }
    }
] + SYSTEM_TOOLS

TOOL_MAP = {
    "web_search": web_search,
    "read_file": read_file,
    "write_file": write_file,
    "list_files": list_files,
    **SYSTEM_TOOL_MAP,
}


def run_tool(name: str, inputs: dict, confirm=None) -> str:
    func = TOOL_MAP.get(name)
    if not func:
        return f"Unknown tool: {name}"
    question = needs_confirmation(name, inputs)
    if question and not (confirm and confirm(question)):
        return "The user declined this action. Do not retry it."
    try:
        result = func(**inputs)
        return str(result)
    except Exception as e:
        return f"Tool error: {e}"
