import os

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


def listen_for_wake_word(threshold: float | None = None) -> None:
    """Block until someone says 'hey Jarvis'. Runs fully offline, no API key needed."""
    if threshold is None:
        threshold = float(os.environ.get("JARVIS_WAKE_THRESHOLD", "0.5"))
    model = _get_model()
    model.reset()
    mic.flush()
    print("Listening for 'hey Jarvis'...")
    while True:
        audio = (np.clip(mic.read(), -1, 1) * 32767).astype(np.int16)
        scores = model.predict(audio)
        if any(score >= threshold for score in scores.values()):
            return
