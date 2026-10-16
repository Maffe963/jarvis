"""Teach Jarvis your voice, so it ignores the TV, other people and videos.

    .venv/Scripts/python.exe enroll_voice.py

Read each sentence aloud when asked (about 30 seconds in all), in your normal voice from where you usually sit.
The voice print stays on this PC (memory/voice_print.json). Turn the check off with JARVIS_VOICE_LOCK=off in
.env, or delete that file to stop it.
"""
import os
import sys

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from voice import select_microphone, speaker, stt  # noqa: E402  (after .env is loaded)

SENTENCES = [
    "Hey Jarvis, what's the weather going to be like tomorrow afternoon?",
    "Open Spotify on my left screen and play something relaxing.",
    "Remind me at nine tomorrow morning to send that email.",
    "How many unread emails do I have, and is anything important?",
    "Find my resume and send the newest version to my phone.",
    "What's on my calendar for the rest of the week?",
]


def main() -> None:
    if not speaker.available():
        sys.exit("The voice model is missing (memory/models/voxceleb_resnet34_LM.onnx).")
    print(f"Microphone: {select_microphone()}")
    print("Read each sentence out loud after the prompt. Press Enter to start.")
    input()
    clips = []
    for i, sentence in enumerate(SENTENCES, 1):
        for attempt in range(2):
            print(f"\n{i}/{len(SENTENCES)}  Say:  \"{sentence}\"")
            audio = stt.record_until_silence(no_speech_timeout_s=8)
            if len(audio) >= 1.5 * stt.SAMPLE_RATE:
                clips.append(audio)
                print(f"   got it ({len(audio) / stt.SAMPLE_RATE:.1f}s)")
                break
            print("   didn't catch that, once more")
    try:
        result = speaker.enroll(clips)
    except ValueError as e:
        sys.exit(f"Couldn't make a voice print: {e} Try again somewhere quieter, or speak up a little.")
    print(f"\nDone. Your clips matched each other at {min(result['self_scores']):.2f}-{max(result['self_scores']):.2f}; "
          f"Jarvis will ignore longer speech that scores below {result['threshold']:.2f}. Restart Jarvis to use it.")


if __name__ == "__main__":
    main()
