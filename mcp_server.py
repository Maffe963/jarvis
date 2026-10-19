"""Minimal MCP stdio server that Claude Code launches in subscription mode.

It holds no tools itself: it lists and forwards everything to the ToolBridge running inside Jarvis
(see tools/bridge.py). Standard library only, so it starts fast on every `claude -p` call.
"""
import json
import os
import sys
import urllib.request

BRIDGE_URL = os.environ["JARVIS_BRIDGE_URL"]
TOKEN = os.environ["JARVIS_BRIDGE_TOKEN"]
TIMEOUT = float(os.environ.get("JARVIS_TOOL_TIMEOUT", "10800"))


def bridge(path: str, body: dict | None = None):
    req = urllib.request.Request(
        BRIDGE_URL + path,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"X-Jarvis-Token": TOKEN, "Content-Type": "application/json"},
    )
    # Generous timeout: a call may be waiting on the user to answer a confirmation, running tests or rendering a video.
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read())


def to_mcp_content(result) -> list[dict]:
    """Tools return text, or Anthropic-style content blocks when there's an image."""
    if isinstance(result, str):
        return [{"type": "text", "text": result}]
    content = []
    for block in result:
        if block["type"] == "image":
            content.append({"type": "image", "data": block["source"]["data"],
                            "mimeType": block["source"]["media_type"]})
        else:
            content.append({"type": "text", "text": block.get("text", "")})
    return content


def handle(method: str, params: dict):
    if method == "initialize":
        return {
            "protocolVersion": params.get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "jarvis", "version": "1.0"},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        tools = bridge("/tools")
        return {"tools": [
            {"name": t["name"], "description": t["description"], "inputSchema": t["input_schema"]}
            for t in tools
        ]}
    if method == "tools/call":
        try:
            result = bridge("/call", {"name": params["name"], "input": params.get("arguments")})["result"]
            return {"content": to_mcp_content(result), "isError": False}
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Tool bridge error: {e}"}], "isError": True}
    raise LookupError(method)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    for line in sys.stdin.buffer:
        if not line.strip():
            continue
        msg = json.loads(line.decode("utf-8"))
        if "id" not in msg:
            continue  # notifications need no reply
        try:
            reply = {"jsonrpc": "2.0", "id": msg["id"], "result": handle(msg.get("method", ""), msg.get("params") or {})}
        except LookupError:
            reply = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "Method not found"}}
        except Exception as e:
            reply = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32603, "message": str(e)}}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
