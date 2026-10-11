import os
import threading

import numpy as np
import sounddevice as sd

_engine = None
_lock = threading.Lock()  # timers speak from background threads


def _elevenlabs_enabled() -> bool:
    key = os.environ.get("ELEVENLABS_API_KEY", "")
    return bool(key) and not key.startswith("your_")


def speak(text: str) -> None:
    """Speak text aloud. Uses ElevenLabs if a key is configured, otherwise Windows' built-in voice."""
    if not text:
        return
    with _lock:
        if _elevenlabs_enabled():
            try:
                _speak_elevenlabs(text)
                return
            except Exception as e:
                print(f"[tts] ElevenLabs failed ({e}); falling back to local voice")
        _speak_local(text)


def _speak_local(text: str) -> None:
    global _engine
    import pyttsx3

    # A fresh engine per call avoids pyttsx3's known hang on repeated runAndWait().
    _engine = pyttsx3.init()
    _engine.setProperty("rate", 185)
    _engine.say(text)
    _engine.runAndWait()
    _engine.stop()


def _speak_elevenlabs(text: str) -> None:
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
    pcm = np.frombuffer(b"".join(stream), dtype=np.int16)
    sd.play(pcm, samplerate=16000)
    sd.wait()
