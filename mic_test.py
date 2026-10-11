"""Find which microphone hears you. Run it and say "hey Jarvis" when each mic is being tested.

    .venv/Scripts/python.exe mic_test.py
"""
import time

import numpy as np
import sounddevice as sd

from voice.mic import BLOCK as FRAME, SAMPLE_RATE
from voice.wake_word import _get_model

SECONDS = 4


def test(device: int, name: str, model) -> None:
    model.reset()
    print(f"\n[{device}] {name}")
    for n in (3, 2, 1):
        print(f"   say 'hey Jarvis' in {n}...", end="\r")
        time.sleep(1)
    print("   >>> SPEAK NOW: 'hey Jarvis'         ")
    loudest, best = 0.0, 0.0
    try:
        with sd.InputStream(device=device, samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=FRAME) as s:
            for _ in range(int(SECONDS * SAMPLE_RATE / FRAME)):
                audio, _ = s.read(FRAME)
                audio = np.squeeze(audio)
                loudest = max(loudest, float(np.sqrt(np.mean((audio / 32768.0) ** 2))))
                best = max(best, max(model.predict(audio).values()))
    except Exception as e:
        print(f"   could not open: {e}")
        return
    level = "silent" if loudest < 0.005 else "quiet" if loudest < 0.015 else "good"
    print(f"   volume: {loudest:.3f} ({level})   wake word score: {best:.2f} (needs 0.50)")


def main() -> None:
    model = _get_model()
    default = sd.default.device[0]
    mics = [(i, d["name"]) for i, d in enumerate(sd.query_devices())
            if d["max_input_channels"] > 0 and sd.query_hostapis(d["hostapi"])["name"] == "MME"
            and "Sound Mapper" not in d["name"]]
    print(f"Testing {len(mics)} microphones, {SECONDS} seconds each. Default is [{default}].")
    for device, name in mics:
        test(device, name, model)
    print("\nPut the number of the mic that worked in .env as JARVIS_MIC=<number> "
          "(or set it as the Windows default).")


if __name__ == "__main__":
    main()
