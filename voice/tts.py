import os
import tempfile
import threading
import wave

import numpy as np
import sounddevice as sd

_lock = threading.Lock()  # timers and reminders speak from background threads
_interrupted = threading.Event()


def _elevenlabs_enabled() -> bool:
    key = os.environ.get("ELEVENLABS_API_KEY", "")
    return bool(key) and not key.startswith("your_")


def speak(text: str) -> bool:
    """Speak text aloud; returns False if stop() cut it off.

    Uses ElevenLabs if a key is configured, otherwise Windows' built-in voice. Either way the speech is
    rendered to audio first and then played, so playback can be stopped part-way.
    """
    if not text:
        return True
    with _lock:
        _interrupted.clear()
        audio, rate = None, 0
        if _elevenlabs_enabled():
            try:
                audio, rate = _render_elevenlabs(text)
            except Exception as e:
                print(f"[tts] ElevenLabs failed ({e}); falling back to local voice")
        if audio is None:
            audio, rate = _render_local(text)
        if _interrupted.is_set():
            return False
        sd.play(audio, samplerate=rate)
        sd.wait()  # returns early if stop() is called
        return not _interrupted.is_set()


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
        engine.setProperty("rate", 185)
        engine.save_to_file(text, path)
        engine.runAndWait()
        engine.stop()
        with wave.open(path) as w:
            rate, channels = w.getframerate(), w.getnchannels()
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    finally:
        os.remove(path)
    return pcm.reshape(-1, channels), rate


def _render_elevenlabs(text: str) -> tuple[np.ndarray, int]:
    from elevenlabs import ElevenLabs, VoiceSettings

    client = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])
    voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "")
    if not voice_id or voice_id.startswith("your_"):
        voice_id = "onwK4e9ZLuTAKqWW03F9"  # Daniel

    stream = client.text_to_speech.convert(
        voice_id=voice_id,
        text=text,
        model_id="eleven_turbo_v2",
        voice_settings=VoiceSettings(stability=0.5, similarity_boost=0.75, style=0.0, use_speaker_boost=True),
        output_format="pcm_16000",
    )
    return np.frombuffer(b"".join(stream), dtype=np.int16), 16000
