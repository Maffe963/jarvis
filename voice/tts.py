import json
import os
import re
import tempfile
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import sounddevice as sd

_lock = threading.Lock()  # timers and reminders speak from background threads
_interrupted = threading.Event()
speaking = threading.Event()  # set while audio is playing, so recording can ignore Jarvis's own voice

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory", "voice.json")
DEFAULT_RATE = 185          # words per minute for the Windows voice
DEFAULT_ELEVENLABS_VOICE = "onwK4e9ZLuTAKqWW03F9"  # Daniel: British, calm


def settings() -> dict:
    """Voice choices made by voice ("use Zira", "talk faster"), kept across restarts."""
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_settings(changes: dict) -> None:
    merged = {**settings(), **changes}
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in merged.items() if v is not None}, f, indent=2)


def _elevenlabs_enabled() -> bool:
    key = os.environ.get("ELEVENLABS_API_KEY", "")
    return bool(key) and not key.startswith("your_") and settings().get("engine") != "windows"


def windows_voices() -> list[tuple[str, str]]:
    """(name, id) of the Windows voices pyttsx3 can use."""
    import pyttsx3

    engine = pyttsx3.init()
    voices = [(v.name, v.id) for v in engine.getProperty("voices")]
    engine.stop()
    return voices


def speak(text: str) -> bool:
    """Speak text aloud; returns False if stop() cut it off.

    Uses ElevenLabs if a key is configured, otherwise Windows' built-in voice. The text is said a sentence or
    so at a time: the next part is turned into audio while the current one plays, so the first words come
    quickly and there are no gaps. Calls play in the order they were made (each one is prepared while the
    one before it is still playing), and playback can be stopped part-way.
    """
    global _serving, _issued, _playback
    if not text:
        return True
    parts = _parts(text)
    upcoming = _renderer.submit(_render, parts[0])
    with _turn:  # from here on nothing can fail before this call's turn ends (see the finally)
        ticket, _issued = _issued, _issued + 1
    try:
        with _turn:
            _turn.wait_for(lambda: _serving == ticket)
        with _lock:
            _interrupted.clear()
            # One timeline for the whole reply, so barge-in compares the mic with what was really playing,
            # including the echo of the previous sentence's last word.
            start, env = None, np.zeros(0, dtype=np.float32)
            speaking.set()
            try:
                for n in range(len(parts)):
                    audio, rate = upcoming.result()
                    if n + 1 < len(parts):
                        upcoming = _renderer.submit(_render, parts[n + 1])
                    if _interrupted.is_set():
                        return False
                    now = time.monotonic()
                    start = now if start is None else start
                    gap = max(0, int((now - start) / ENVELOPE_STEP_S) - len(env))
                    env = np.concatenate([env, np.zeros(gap, dtype=np.float32), _envelope(audio, rate)])
                    _playback = (start, env)
                    sd.play(audio, samplerate=rate)
                    sd.wait()  # returns early if stop() is called
                    if _interrupted.is_set():
                        return False
                return True
            finally:
                speaking.clear()
                _playback = None
    finally:
        with _turn:
            _serving = max(_serving, ticket + 1)
            _turn.notify_all()


_turn = threading.Condition()     # whose turn it is to play
_issued = 0                       # tickets handed out, in call order
_serving = 0                      # the ticket whose turn it is
_renderer = ThreadPoolExecutor(max_workers=3, thread_name_prefix="tts")
_local_lock = threading.Lock()    # one Windows voice engine at a time
FIRST_PART_MIN = 20               # characters: the first part is one sentence, said as soon as it's ready
PART_MAX = 260                    # later parts group sentences up to this length


def _parts(text: str) -> list[str]:
    """The text in speakable pieces: the first sentence on its own, then a few sentences at a time."""
    parts: list[str] = []
    for sentence in (s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s):
        if not parts:
            parts.append(sentence)
        elif len(parts) == 1 and len(parts[0]) < FIRST_PART_MIN:  # "Yes sir." alone would sound clipped
            parts[0] += " " + sentence
        elif len(parts) > 1 and len(parts[-1]) + len(sentence) < PART_MAX:
            parts[-1] += " " + sentence
        else:
            parts.append(sentence)
    return parts or [text]


def wait_until_quiet() -> None:
    """Wait until everything asked to be said so far has been said (or stopped)."""
    with _turn:
        _turn.wait_for(lambda: _serving >= _issued)


def _render(text: str) -> tuple[np.ndarray, int]:
    if _elevenlabs_enabled():
        try:
            return _render_elevenlabs(text)
        except Exception as e:
            print(f"[tts] ElevenLabs failed ({e}); falling back to local voice")
    with _local_lock:
        return _render_local(text)


ENVELOPE_STEP_S = 0.02
_playback: tuple[float, np.ndarray] | None = None  # (start time, loudness every 20 ms) of what's playing


def _envelope(audio: np.ndarray, rate: int) -> np.ndarray:
    samples = audio.reshape(len(audio), -1)[:, 0].astype(np.float32) / 32768.0
    step = max(1, int(rate * ENVELOPE_STEP_S))
    frames = samples[: len(samples) // step * step].reshape(-1, step)
    return np.sqrt(np.mean(frames ** 2, axis=1)) if len(frames) else np.zeros(1, dtype=np.float32)


def playback_started() -> float | None:
    """When the current audio started playing (time.monotonic), or None if nothing is playing."""
    current = _playback
    return current[0] if current else None


def playback_level(t0: float, t1: float, how: str = "mean") -> float:
    """How loud Jarvis's own audio was between two moments (time.monotonic); 0 outside playback.

    Used to tell the user's voice apart from Jarvis's echo through the speakers.
    """
    current = _playback
    if current is None:
        return 0.0
    start, env = current
    begin = max(0, int((t0 - start) / ENVELOPE_STEP_S))
    end = min(len(env), max(begin + 1, int((t1 - start) / ENVELOPE_STEP_S)))
    window = env[begin:end]
    if not len(window):
        return 0.0
    return float(window.max() if how == "max" else np.sqrt(np.mean(window ** 2)))


def stop() -> None:
    """Stop whatever is being said right now (safe to call from any thread)."""
    _interrupted.set()
    sd.stop()


def _render_local(text: str) -> tuple[np.ndarray, int]:
    import pyttsx3

    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        # A fresh engine per call avoids pyttsx3's known hang on repeated runAndWait().
        engine = pyttsx3.init()
        chosen = settings()
        engine.setProperty("rate", chosen.get("rate", DEFAULT_RATE))
        if chosen.get("windows_voice_id"):
            engine.setProperty("voice", chosen["windows_voice_id"])
        engine.save_to_file(text, path)
        engine.runAndWait()
        engine.stop()
        with wave.open(path) as w:
            rate, channels = w.getframerate(), w.getnchannels()
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    finally:
        os.remove(path)
    return pcm.reshape(-1, channels), rate


_eleven = None  # one client, created once: importing and connecting the first time takes seconds


def warm_up() -> None:
    """Load the ElevenLabs client at startup so the first reply isn't slow."""
    if _elevenlabs_enabled():
        try:
            _client()
        except Exception as e:
            print(f"[tts] ElevenLabs not ready ({e})")


def _client():
    global _eleven
    if _eleven is None:
        from elevenlabs import ElevenLabs

        _eleven = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])
    return _eleven


def _render_elevenlabs(text: str) -> tuple[np.ndarray, int]:
    from elevenlabs import VoiceSettings

    voice_id = settings().get("elevenlabs_voice_id") or os.environ.get("ELEVENLABS_VOICE_ID", "")
    if not voice_id or voice_id.startswith("your_"):
        voice_id = DEFAULT_ELEVENLABS_VOICE

    # Streaming with the "flash" model: the whole sentence arrives in about 0.3s (vs. seconds for convert()).
    stream = _client().text_to_speech.stream(
        voice_id=voice_id,
        text=text,
        model_id=os.environ.get("JARVIS_ELEVENLABS_MODEL", "eleven_flash_v2_5"),
        voice_settings=VoiceSettings(stability=0.5, similarity_boost=0.75, style=0.0, use_speaker_boost=True),
        output_format="pcm_16000",
    )
    return np.frombuffer(b"".join(chunk for chunk in stream if chunk), dtype=np.int16), 16000
