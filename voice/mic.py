"""One microphone stream shared by the wake word and speech recording.

Keeping a single stream open means nothing said right after "hey Jarvis" is lost while a new
stream starts up.
"""
import queue

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
BLOCK = 1280  # 80 ms, what openWakeWord expects

_blocks: queue.Queue = queue.Queue()
_stream = None


def _on_audio(indata, frames, time_info, status) -> None:
    _blocks.put(indata[:, 0].copy())


def read() -> np.ndarray:
    """Next 80 ms of float32 mono audio, opening the mic on first use."""
    global _stream
    if _stream is None:
        _stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                 blocksize=BLOCK, callback=_on_audio)
        _stream.start()
    return _blocks.get()


def flush() -> None:
    """Drop buffered audio, e.g. Jarvis's own voice picked up while it was speaking."""
    while True:
        try:
            _blocks.get_nowait()
        except queue.Empty:
            return
