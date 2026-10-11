"""Let the user interrupt Jarvis mid-sentence just by talking (or by saying "hey Jarvis").

The hard part is that the mic also hears Jarvis's own voice from the speakers. Jarvis knows how loud its
own audio is at every moment (tts.playback_level), so during the first second of each reply it learns
how strongly, and how late, that audio comes back through the mic. After that, sound only counts as the
user when it is clearly louder than the echo expected at that moment, for long enough to be speech
rather than a click or a cough.
"""
import os
import threading
import time
from collections import deque

import numpy as np

from voice import mic, tts
from voice.mic import BLOCK, SAMPLE_RATE

BLOCK_S = BLOCK / SAMPLE_RATE
CALIBRATION_S = 1.0                    # playback used to learn the echo
MIN_CALIBRATION_ECHO = 0.01            # ...and it must contain actual speech, not just a pause
DELAYS_S = np.arange(0.0, 0.42, 0.02)  # speaker-to-mic delays tried when learning the echo
SLACK_S = 0.08                         # timing jitter allowed around the learned delay
ECHO_MARGIN = 1.6                      # the user must be this much louder than the expected echo
                                       # (measured: Jarvis's own echo peaks at ~1.1x the expected level)
TRIGGER_BLOCKS = 3                     # ~240 ms of that, in a row
PRE_ROLL_BLOCKS = 8                    # keep ~640 ms from before the trigger, so first words aren't lost


def mode() -> str:
    """'speech': any speech interrupts. 'wake': only "hey Jarvis" does. 'off': nothing does."""
    value = os.environ.get("JARVIS_INTERRUPT", "speech").strip().lower()
    return value if value in ("speech", "wake", "off") else "speech"


class EchoGate:
    """Decides, block by block, whether the mic is hearing the user over Jarvis's own echo."""

    def __init__(self, floor: float, playback_level=tts.playback_level) -> None:
        self.floor = floor                  # never trigger below normal speech level
        self.playback_level = playback_level
        self.gain = self.delay = self.noise = None
        self._quiet: list[float] = []       # mic levels before playback started: the room's noise
        self._calibration: list[tuple[float, float]] = []
        self._run = 0

    def feed(self, level: float, now: float, started: float | None) -> bool:
        """`level`: mic loudness of the block that ended at `now`. `started`: when playback began."""
        if started is None or now - BLOCK_S < started:
            self._quiet.append(level)
            return False
        if self.gain is None:
            self._calibration.append((now, level))
            if now - started >= CALIBRATION_S:
                self._calibrate()
            return False
        echo = self.playback_level(now - BLOCK_S - self.delay - SLACK_S, now - self.delay + SLACK_S, "max")
        expected = float(np.hypot(self.gain * echo, self.noise))
        self._run = self._run + 1 if level > max(self.floor, ECHO_MARGIN * expected) else 0
        return self._run >= TRIGGER_BLOCKS

    def _calibrate(self) -> None:
        times = np.array([t for t, _ in self._calibration])
        levels = np.array([lvl for _, lvl in self._calibration])
        self.noise = float(np.median(self._quiet)) if self._quiet else float(levels.min())
        heard = np.clip(levels ** 2 - self.noise ** 2, 0, None)   # echo power the mic picked up
        best = None
        for delay in DELAYS_S:
            played = np.array([self.playback_level(t - BLOCK_S - delay, t - delay) for t in times]) ** 2
            if played.max() < MIN_CALIBRATION_ECHO ** 2:
                continue
            gain2 = float((heard * played).sum() / (played ** 2).sum())
            error = float(((heard - gain2 * played) ** 2).sum())
            if best is None or error < best[0]:
                best = (error, delay, gain2)
        if best is None:
            return  # Jarvis was nearly silent so far; keep learning
        _, self.delay, gain2 = best
        self.gain = float(np.sqrt(max(gain2, 0.0)))


def watch(done: threading.Event, speech_floor: float) -> tuple[str, list[np.ndarray]] | None:
    """Listen while Jarvis talks. On an interruption, stop the speech and return (reason, audio so far)."""
    current = mode()
    if current == "off":
        done.wait()
        return None
    from voice import wake_word

    model = wake_word._get_model()
    model.reset()
    threshold = float(os.environ.get("JARVIS_WAKE_THRESHOLD", "0.5"))
    gate = EchoGate(floor=speech_floor)
    recent: deque[np.ndarray] = deque(maxlen=PRE_ROLL_BLOCKS)
    mic.flush()
    while not done.is_set():
        block = mic.read(timeout=0.2)
        if block is None:
            continue
        now = time.monotonic()
        recent.append(block)
        scores = model.predict((np.clip(block, -1, 1) * 32767).astype(np.int16))
        if any(score >= threshold for score in scores.values()):
            reason = "wake word"
        elif current == "speech" and gate.feed(float(np.sqrt(np.mean(block ** 2))), now, tts.playback_started()):
            reason = "speech"
        else:
            continue
        tts.stop()
        return reason, list(recent)
    return None
