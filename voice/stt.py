import gc
import os
import threading
from collections import deque

import numpy as np
from faster_whisper import WhisperModel

from voice import mic, tts
from voice.mic import BLOCK, SAMPLE_RATE

SPEECH_START_BLOCKS = 3  # 240 ms of sound before it counts as speech
PRE_ROLL_BLOCKS = 6      # keep 480 ms from before speech was detected

_model = None
_model_lock = threading.Lock()  # loaded in the background when waking from standby


def _get_model() -> WhisperModel:
    global _model
    with _model_lock:
        if _model is None:
            _model = WhisperModel("base.en", device="cpu", compute_type="int8")
        return _model


def unload_model() -> None:
    """Free the speech model's memory (standby). It reloads on next use."""
    global _model
    with _model_lock:
        _model = None
    gc.collect()


def speech_threshold() -> float:
    return float(os.environ.get("JARVIS_SPEECH_THRESHOLD", "0.015"))


def record_until_silence(
    silence_threshold: float | None = None,
    max_silence_s: float = 1.2,
    max_record_s: float = 20.0,
    no_speech_timeout_s: float = 6.0,
    speech_start_blocks: int = SPEECH_START_BLOCKS,
    already_heard: list[np.ndarray] | None = None,
) -> np.ndarray:
    """Record from the mic until the speaker stops talking. Returns float32 mono audio.

    Speech counts as started only after ~240 ms of sustained sound, so a single click or bump
    doesn't start (and then end) a recording. The moments just before that are kept so the
    first syllable isn't clipped. `already_heard` continues a recording that began elsewhere
    (the user talking over Jarvis), so it counts as speech already started.
    """
    if silence_threshold is None:
        silence_threshold = speech_threshold()
    blocks_per_s = SAMPLE_RATE / BLOCK
    max_silence_blocks = int(max_silence_s * blocks_per_s)
    max_blocks = int(max_record_s * blocks_per_s)
    max_idle_blocks = int(no_speech_timeout_s * blocks_per_s)
    pre_roll: deque[np.ndarray] = deque(maxlen=PRE_ROLL_BLOCKS)

    frames: list[np.ndarray] = list(already_heard or [])
    loud_run = 0
    silence_blocks = 0
    loudest = 0.0
    i = 0
    while i < max_blocks:
        block = mic.read()
        if tts.speaking.is_set():
            # A timer or reminder is talking: that's Jarvis, not the user. Start over once it's done.
            frames.clear()
            pre_roll.clear()
            loud_run = silence_blocks = 0
            continue
        i += 1
        level = float(np.sqrt(np.mean(block ** 2)))
        loudest = max(loudest, level)
        loud = level > silence_threshold

        if frames:
            frames.append(block)
            silence_blocks = 0 if loud else silence_blocks + 1
            if silence_blocks > max_silence_blocks:
                break
            continue

        pre_roll.append(block)
        loud_run = loud_run + 1 if loud else 0
        if loud_run >= speech_start_blocks:
            frames.extend(pre_roll)
        elif i >= max_idle_blocks:
            break

    seconds = sum(len(f) for f in frames) / SAMPLE_RATE
    print(f"[mic] recorded {seconds:.1f}s, loudest {loudest:.3f} (speech threshold {silence_threshold})")
    if not frames:
        return np.zeros(0, dtype="float32")
    return np.concatenate(frames)


def transcribe(audio: np.ndarray) -> str:
    """Transcribe float32 16 kHz mono audio to text."""
    if audio.size < SAMPLE_RATE * 0.3:
        return ""
    segments, _ = _get_model().transcribe(audio, beam_size=5, language="en", vad_filter=True)
    return " ".join(seg.text for seg in segments).strip()
