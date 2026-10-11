"""Change Jarvis's voice and speaking speed by asking ("use a female voice", "talk faster")."""
from voice import tts


def _short(name: str) -> str:
    """"Microsoft Zira Desktop - English (United States)" -> "Zira"."""
    return name.replace("Microsoft ", "").split(" - ")[0].replace(" Desktop", "")


def _elevenlabs_voices() -> list[tuple[str, str]]:
    """(name, id) of the voices on the user's ElevenLabs account, if a key is set."""
    import os

    key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not key or key.startswith("your_"):
        return []
    from elevenlabs import ElevenLabs

    response = ElevenLabs(api_key=key).voices.get_all()
    return [(v.name, v.voice_id) for v in response.voices]


def list_voices() -> str:
    current = tts.settings()
    lines = ["Windows voices (free, built in): " + ", ".join(
        _short(name) for name, _ in tts.windows_voices())]
    try:
        eleven = _elevenlabs_voices()
    except Exception as e:
        eleven, lines = [], lines + [f"(Couldn't list ElevenLabs voices: {e})"]
    if eleven:
        lines.append("ElevenLabs voices (natural sounding): " + ", ".join(name for name, _ in eleven))
    else:
        lines.append("ElevenLabs (much more natural voices) isn't set up: it needs ELEVENLABS_API_KEY in .env.")
    lines.append(f"Speed: {current.get('rate', tts.DEFAULT_RATE)} words per minute (Windows voices).")
    return "\n".join(lines)


def set_voice(name: str = "", rate: int | None = None) -> str:
    changes, said = {}, []
    if name.strip():
        wanted = name.lower().replace("microsoft", "").strip()
        if wanted in ("windows", "built in", "builtin", "local"):
            changes["engine"] = "windows"
            said.append("using the Windows voice")
        else:
            try:
                eleven = _elevenlabs_voices()
            except Exception:
                eleven = []
            match = next(((n, i) for n, i in eleven if wanted in n.lower()), None)
            if match:
                changes.update(engine="elevenlabs", elevenlabs_voice_id=match[1])
                said.append(f"voice set to {match[0]} (ElevenLabs)")
            else:
                match = next(((n, i) for n, i in tts.windows_voices() if wanted in n.lower()), None)
                if not match:
                    return f"No voice called '{name}'. " + list_voices()
                changes.update(engine="windows", windows_voice_id=match[1])
                said.append(f"voice set to {_short(match[0])}")
    if rate is not None:
        changes["rate"] = max(100, min(int(rate), 300))
        said.append(f"speed {changes['rate']} words per minute")
    if not changes:
        return list_voices()
    tts.save_settings(changes)
    return "Done: " + ", ".join(said) + ". The next thing you say will use it."


TOOLS = [
    {
        "name": "list_voices",
        "description": "List the voices Jarvis can speak with and the current speed.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "set_voice",
        "description": "Change Jarvis's voice and/or speaking speed. Windows voices: David (male), Zira (female); "
                       "ElevenLabs voices if set up. name='windows' switches back to the built-in voice. Speed is "
                       "words per minute (default 185; 'a bit faster' is about +25).",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Voice name, e.g. 'Zira', 'David', 'Daniel'"},
                "rate": {"type": "integer", "description": "Words per minute, 100-300"}
            },
            "required": []
        }
    },
]

TOOL_MAP = {"list_voices": list_voices, "set_voice": set_voice}
