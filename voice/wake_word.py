import os
import threading

import numpy as np

from voice import mic

_model = None


def _get_model():
    global _model
    if _model is None:
        import openwakeword
        from openwakeword.model import Model

        openwakeword.utils.download_models(["hey_jarvis"])
        _model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
    return _model


def listen_for_wake_word(threshold: float | None = None, stop: threading.Event | None = None) -> bool:
    """Block until someone says 'hey Jarvis' (True) or `stop` is set (False). Fully offline."""
    if threshold is None:
        threshold = float(os.environ.get("JARVIS_WAKE_THRESHOLD", "0.5"))
    model = _get_model()
    model.reset()
    mic.flush()
    if stop is None:
        print("Listening for 'hey Jarvis'...")
    while stop is None or not stop.is_set():
        block = mic.read(timeout=0.2)
        if block is None:
            continue
        scores = model.predict((np.clip(block, -1, 1) * 32767).astype(np.int16))
        if any(score >= threshold for score in scores.values()):
            return True
    return False
