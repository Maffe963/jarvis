import os


def select_microphone() -> str:
    """Apply JARVIS_MIC (a device number or part of its name) and return the mic in use."""
    import sounddevice as sd

    wanted = os.environ.get("JARVIS_MIC", "").strip()
    if wanted:
        if wanted.isdigit():
            device = int(wanted)
        else:
            matches = [i for i, d in enumerate(sd.query_devices())
                       if d["max_input_channels"] > 0 and wanted.lower() in d["name"].lower()]
            if not matches:
                raise ValueError(f"No microphone matching JARVIS_MIC={wanted!r}. Run mic_test.py to list them.")
            device = matches[0]
        sd.default.device = (device, sd.default.device[1])
    return sd.query_devices(kind="input")["name"]
