"""Backup brain: a small AI model running on this PC (Phi-3.5-mini, MIT license) for when Claude can't be
reached (no internet, a Claude Code problem, or the usage limit). It handles everyday commands and short
questions; anything bigger waits for Claude. Loaded only when needed (memory/models/phi-3.5-mini).
"""
import json
import os
import threading
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_DIR = os.path.join(ROOT, "memory", "models", "phi-3.5-mini")
MAX_TOKENS = 160

# The everyday actions the small model may use (each still goes through run_tool's confirmations).
TOOLS = {
    "open_app": "open_app(name): launch an app",
    "close_app": "close_app(name): close an app",
    "volume": "volume(action: up|down|mute|unmute|set|get, level): system volume",
    "media_control": "media_control(action: play_pause|next|previous|stop): music/video playback",
    "set_timer": "set_timer(seconds, label): countdown timer",
    "set_reminder": "set_reminder(when: ISO datetime like 2026-10-16T15:00, message): reminder at a time",
    "open_url": "open_url(url): open a website",
    "focus_window": "focus_window(title): bring a window to the front",
    "show_desktop": "show_desktop(): minimise everything",
    "system_info": "system_info(): CPU, memory, battery",
}

SYSTEM = ("You are Jarvis, a voice assistant on the user's Windows PC, running in offline mode. Reply with ONE "
          'line of JSON only: {"tool": NAME, "args": {...}} to do something, or {"reply": "..."} to answer in one '
          "short spoken sentence. Tools:\n" + "\n".join(TOOLS.values()))

_state: dict = {}  # model, tokenizer, and a generator that has already read SYSTEM
_lock = threading.Lock()


def available() -> bool:
    return os.path.exists(os.path.join(MODEL_DIR, "genai_config.json"))


def _primed() -> dict:
    """The model with the (unchanging) instructions already read. Reading them takes ~12 s on this CPU, so it's
    done once and each request rewinds to that point (~2 s per request after that)."""
    if "generator" not in _state:
        import onnxruntime_genai as og

        model = og.Model(MODEL_DIR)
        tokenizer = og.Tokenizer(model)
        params = og.GeneratorParams(model)
        params.set_search_options(max_length=2048, do_sample=False)
        generator = og.Generator(model, params)
        instructions = tokenizer.encode(f"<|system|>\n{SYSTEM}<|end|>\n")
        generator.append_tokens(instructions)
        _state.update(model=model, tokenizer=tokenizer, generator=generator, base=len(instructions))
    return _state


def unload() -> None:
    """Free the ~3 GB the model uses (on standby)."""
    with _lock:
        _state.clear()


def _generate(user: str) -> str:
    state = _primed()
    generator, tokenizer = state["generator"], state["tokenizer"]
    generator.rewind_to(state["base"])
    generator.append_tokens(tokenizer.encode(f"<|user|>\n{user}<|end|>\n<|assistant|>\n"))
    stream, out = tokenizer.create_stream(), ""
    for _ in range(MAX_TOKENS):
        if generator.is_done():
            break
        generator.generate_next_token()
        out += stream.decode(generator.get_next_tokens()[0])
        if "{" in out and out.count("{") == out.count("}"):
            break  # one complete JSON object is all we need
    return out


def _parse(text: str) -> tuple[str | None, dict | str]:
    """(tool, args) or (None, reply). Lenient: the small model sometimes writes {"set_timer": {...}}."""
    try:
        data = json.loads(text[text.index("{"):text.rindex("}") + 1])
    except ValueError:
        return None, text.strip()
    if isinstance(data, dict):
        if data.get("tool") in TOOLS:
            return data["tool"], data.get("args") or {}
        if "reply" in data:
            return None, str(data["reply"])
        if len(data) == 1:
            name, args = next(iter(data.items()))
            if name in TOOLS and isinstance(args, dict):
                return name, args
    return None, text.strip()


def answer(text: str, confirm, acknowledge) -> str | None:
    """Handle a request with the local model; None if it isn't installed."""
    if not available():
        return None
    from tools.registry import run_tool

    with _lock:  # the time goes in the request, so the instructions stay the same and can be reused
        tool, value = _parse(_generate(f"[Current time: {datetime.now():%A %Y-%m-%d %H:%M}]\n{text}"))
    if tool is None:
        return value or "Sorry sir, I couldn't work that out offline."
    return str(run_tool(tool, value, confirm, acknowledge))
