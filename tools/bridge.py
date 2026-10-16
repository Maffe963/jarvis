"""Lets Claude Code (in subscription mode) call Jarvis's tools while they keep running inside Jarvis.

Claude Code launches mcp_server.py, which forwards each tool call here over localhost. Running the
tools in this process means confirmations use Jarvis's own voice/text prompt and timers outlive the
short-lived `claude -p` process.
"""
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tools.registry import TOOLS, run_tool


class ToolBridge:
    def __init__(self, allowed: set[str] | None = None, cancelled: threading.Event | None = None,
                 log: list[str] | None = None) -> None:
        """`allowed`: tool names this session may use (None = all). `cancelled` / `log`: see run_tool."""
        self.token = secrets.token_urlsafe(32)
        self.allowed, self.cancelled, self.log = allowed, cancelled, log
        tools = [t for t in TOOLS if allowed is None or t["name"] in allowed]
        self.confirm = None      # set by brain.py before each turn
        self.acknowledge = None  # likewise
        self._lock = threading.Lock()  # one tool (and one confirmation prompt) at a time
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/tools" and bridge._authorized(self):
                    self._reply(tools)
                else:
                    self.send_error(404)

            def do_POST(self):
                if self.path != "/call" or not bridge._authorized(self):
                    self.send_error(404)
                    return
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                if bridge.allowed is not None and body["name"] not in bridge.allowed:
                    self._reply({"result": f"{body['name']} isn't available here."})
                    return
                with bridge._lock:
                    result = run_tool(body["name"], body.get("input") or {}, bridge.confirm, bridge.acknowledge,
                                      bridge.cancelled, bridge.log)
                self._reply({"result": result})

            def _reply(self, data) -> None:
                payload = json.dumps(data).encode("utf-8")
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except ConnectionError:
                    pass  # Claude Code was stopped (the user interrupted) before the tool finished

            def log_message(self, *args) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def _authorized(self, handler: BaseHTTPRequestHandler) -> bool:
        return secrets.compare_digest(handler.headers.get("X-Jarvis-Token", ""), self.token)
