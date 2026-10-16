"""Only answer the user's voice: compare what Jarvis hears with a voice print recorded once.

Uses the WeSpeaker ResNet34 model (memory/models/voxceleb_resnet34_LM.onnx, CC-BY-4.0) to turn speech into a
256-number "voice print". The user's print is made once with `enroll_voice.py` and stays on this PC
(memory/voice_print.json). Speech whose print isn't close enough to it is ignored, so the TV, other people or a
video can't give Jarvis commands.
"""
import json
import os

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = os.path.join(ROOT, "memory", "models", "voxceleb_resnet34_LM.onnx")
PRINT_FILE = os.path.join(ROOT, "memory", "voice_print.json")
SAMPLE_RATE = 16000
MIN_SECONDS = 1.5  # shorter clips ("next song") give unreliable voice prints, so they aren't judged

_session = None
_print: dict | None = None


def _mel_filters(n_fft: int = 512, n_mels: int = 80, low: float = 20.0, high: float = 8000.0) -> np.ndarray:
    """Kaldi-style triangular mel filterbank (what the model was trained with)."""
    mel = lambda f: 1127.0 * np.log(1.0 + f / 700.0)
    lo, hi = mel(low), mel(high)
    centers = np.linspace(lo, hi, n_mels + 2)
    bins = mel(np.arange(n_fft // 2 + 1) * SAMPLE_RATE / n_fft)
    filters = np.zeros((n_mels, n_fft // 2 + 1))
    for m in range(n_mels):
        left, center, right = centers[m:m + 3]
        up = (bins - left) / (center - left)
        down = (right - bins) / (right - center)
        filters[m] = np.clip(np.minimum(up, down), 0, None)
    return filters


_FILTERS = _mel_filters()


def fbank(audio: np.ndarray) -> np.ndarray:
    """80-dim log-mel features, 25 ms frames every 10 ms, mean-normalised (as WeSpeaker expects)."""
    x = audio.astype(np.float64) * 32768.0  # the model was trained on 16-bit-scale samples
    frame, hop = 400, 160
    if len(x) < frame:
        return np.zeros((0, 80), dtype=np.float32)
    count = 1 + (len(x) - frame) // hop
    frames = np.lib.stride_tricks.sliding_window_view(x, frame)[::hop][:count].copy()
    frames -= frames.mean(axis=1, keepdims=True)                                  # remove DC
    frames[:, 1:] -= 0.97 * frames[:, :-1]                                        # pre-emphasis
    frames[:, 0] -= 0.97 * frames[:, 0]
    frames *= np.power(np.hanning(frame + 2)[1:-1], 0.85)                         # "povey" window
    power = np.abs(np.fft.rfft(frames, n=512)) ** 2
    feats = np.log(np.maximum(power @ _FILTERS.T, np.finfo(np.float64).eps))
    return (feats - feats.mean(axis=0)).astype(np.float32)


def embed(audio: np.ndarray) -> np.ndarray | None:
    """A unit-length voice print for some speech, or None if there's too little of it."""
    global _session
    if len(audio) < MIN_SECONDS * SAMPLE_RATE:
        return None
    if _session is None:
        import onnxruntime as ort

        _session = ort.InferenceSession(MODEL, providers=["CPUExecutionProvider"])
    vec = _session.run(None, {"feats": fbank(audio)[None]})[0][0]
    return vec / (np.linalg.norm(vec) + 1e-9)


def available() -> bool:
    return os.path.exists(MODEL)


def enrolled() -> dict | None:
    global _print
    if _print is None:
        try:
            with open(PRINT_FILE, "r", encoding="utf-8") as f:
                _print = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return None
    return _print


def enroll(clips: list[np.ndarray]) -> dict:
    """Make the user's voice print from several clips of them talking; save it."""
    global _print
    vectors = [v for v in (embed(c) for c in clips) if v is not None]
    if len(vectors) < 3:
        raise ValueError("Need at least three clips of speech, each over a second long.")
    mean = np.mean(vectors, axis=0)
    mean /= np.linalg.norm(mean)
    self_scores = [float(v @ mean) for v in vectors]
    # Accept anything about as close as the user's own clips, with room for a cold or a quiet voice. (Tested:
    # the right voice scored 0.74-0.83; the most similar other voice, another British man, at most 0.58.)
    threshold = float(max(0.25, min(self_scores) - 0.2))
    _print = {"vector": mean.tolist(), "threshold": threshold, "self_scores": self_scores}
    with open(PRINT_FILE, "w", encoding="utf-8") as f:
        json.dump(_print, f)
    return _print


def is_user(audio: np.ndarray) -> tuple[bool, float | None]:
    """(is it the user?, similarity). Unsure (too short, or no voice print yet) counts as the user."""
    print_ = enrolled()
    if not print_ or os.environ.get("JARVIS_VOICE_LOCK", "on").lower() in ("off", "0", "false", "no"):
        return True, None
    vec = embed(audio)
    if vec is None:
        return True, None
    score = float(vec @ np.array(print_["vector"]))
    return score >= print_["threshold"], score
