import os
import re
import sys

from dotenv import load_dotenv

load_dotenv()

import anthropic

from brain import BackendError, think, warm_up
from memory.conversation import add_turn, load_history, save_history
from tools import reminders
from tools.system import set_announcer

EXIT_PHRASES = ("quit", "exit", "goodbye", "shut down jarvis")
SLEEP_PHRASES = ("go to sleep", "that's all", "that's it", "that will be all", "thanks jarvis", "thank you jarvis")
CANCEL_PHRASES = ("stop", "cancel", "never mind", "nevermind", "shut up", "quiet", "nothing")
# Whisper tends to "hear" these in background noise; on their own they're not requests.
NOISE_TRANSCRIPTS = {"you", "thank you", "thanks for watching", "thank you for watching", "bye", "uh", "um", "hmm"}
# After "hey Jarvis", how long to keep listening for the next request without it.
# 0 = say "hey Jarvis" every time; -1 = stay in the conversation until told "that's all".
FOLLOWUP_SECONDS = float(os.environ.get("JARVIS_FOLLOWUP_SECONDS", "10"))
YES_WORDS = {"yes", "yeah", "yep", "yup", "sure", "confirm", "confirmed", "affirmative", "okay", "ok", "absolutely"}
YES_PHRASES = ("do it", "go ahead", "go for it")
NO_WORDS = {"no", "nope", "nah", "not", "don't", "dont", "cancel", "stop", "never", "wait", "negative"}
MAX_SPOKEN_QUESTION = 90
MAX_SPOKEN_MESSAGE = 400  # messages to send are read out in full so you know what you're approving


def clean_transcript(text: str) -> str:
    """Drop a leading "hey Jarvis" (people say it out of habit) and noise-only transcripts."""
    text = re.sub(r"^\s*(hey|hi|okay|ok)?[\s,]*jarvis[\s,.!?]*", "", text, flags=re.IGNORECASE).strip()
    return "" if text.lower().strip(" .!?,") in NOISE_TRANSCRIPTS else text


def is_yes(answer: str) -> bool:
    """True only for a clear yes; any negative word anywhere wins ("yes, wait, no" is a no)."""
    answer = answer.lower()
    words = set(re.findall(r"[a-z']+", answer))
    if words & NO_WORDS:
        return False
    return bool(words & YES_WORDS) or any(p in answer for p in YES_PHRASES)


def respond(history: list[dict], user_text: str, confirm, say) -> tuple[list[dict], str]:
    """Run one turn. On failure the user's turn is dropped so the history stays valid.

    `say(text)` delivers the instant "Yes sir, opening Spotify." when the first tool starts. The returned
    response is only what's left to say after that, and may be empty.
    """
    history = add_turn(history, "user", user_text)
    acknowledged: list[str] = []

    def acknowledge(phrase: str) -> None:
        if not acknowledged:  # once per request, even if several tools run
            acknowledged.append(f"Yes sir, {phrase}.")
            say(acknowledged[0])

    try:
        response = think(history, confirm, acknowledge)
        response = response.replace("[done]", "").strip()
        if not response and not acknowledged:
            response = "Done, sir."
    except BackendError as e:
        history.pop()
        return history, str(e)
    except anthropic.AuthenticationError:
        history.pop()
        return history, "My API key was rejected, sir. Please check the ANTHROPIC_API_KEY in the .env file."
    except anthropic.RateLimitError:
        history.pop()
        return history, "I'm being rate limited, sir. Give me a moment and try again."
    except anthropic.APIConnectionError:
        history.pop()
        return history, "I can't reach the server, sir. Please check the internet connection."
    except anthropic.APIError as e:
        history.pop()
        print(f"[error] {e}")
        return history, "The server returned an error, sir. Please try again."
    history = add_turn(history, "assistant", " ".join(acknowledged + [response]).strip())
    save_history(history)
    return history, response


def confirm_text(question: str) -> bool:
    return input(f"Jarvis: {question} (y/n) ").strip().lower() in ("y", "yes")


def run_text() -> None:
    warm_up()
    reminders.start()
    print("Jarvis online (text mode). Type 'quit' to exit.\n")
    history = load_history()
    try:
        while True:
            user_input = input("You: ").strip()
            if not user_input:
                continue
            if user_input.lower().strip(" .!") in EXIT_PHRASES:
                print("Jarvis: Goodbye, sir.")
                break
            history, response = respond(history, user_input, confirm_text,
                                        lambda text: print(f"Jarvis: {text}"))
            if response:
                print(f"Jarvis: {response}")
            print()
    except KeyboardInterrupt:
        print("\nJarvis: Shutting down, sir.")


def run_voice() -> None:
    import threading
    import winsound

    from voice import mic, select_microphone, stt, wake_word
    from voice.stt import record_until_silence, transcribe
    from voice import tts
    from voice.tts import speak
    from voice.wake_word import listen_for_wake_word

    def acknowledge_aloud(text: str) -> None:
        # In the background, so the command runs while Jarvis is saying "Yes sir, ...".
        print(f"Jarvis: {text}")
        threading.Thread(target=speak, args=(text,), daemon=True).start()

    def speak_until_woken(text: str) -> bool:
        """Speak, but stop the moment someone says 'hey Jarvis'. True if that happened."""
        done = threading.Event()
        woken = threading.Event()

        def watch():
            if listen_for_wake_word(stop=done):
                woken.set()
                tts.stop()

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        speak(text)
        done.set()
        watcher.join()
        return woken.is_set()

    def confirm_voice(question: str) -> bool:
        print(f"Jarvis: {question}")
        # Long questions (e.g. a full PowerShell command) are shown, not read out.
        limit = MAX_SPOKEN_MESSAGE if question.startswith("Send ") else MAX_SPOKEN_QUESTION
        if len(question) > limit:
            question = question.split(":")[0].rstrip("?") + "? The details are on screen."
        speak(question)
        mic.flush()  # don't mistake Jarvis's own voice for the answer
        answer = transcribe(record_until_silence(no_speech_timeout_s=5))
        print(f"You: {answer or '(no answer)'}")
        approved = is_yes(answer)
        if not approved:
            speak("Cancelled.")
        return approved

    set_announcer(lambda msg: (print(f"\nJarvis: {msg}"), speak(msg)))
    history = load_history()
    microphone = select_microphone()
    print("Starting up...")
    warm_up()
    wake_word._get_model()
    stt._get_model()
    print(f"Jarvis online (voice mode), listening on: {microphone}")
    print("Say 'hey Jarvis'. Ctrl+C to quit.\n")
    speak("Jarvis online.")
    reminders.start()
    def beep(freq: int) -> None:
        # In the background so recording starts immediately.
        threading.Thread(target=winsound.Beep, args=(freq, 120), daemon=True).start()

    def end_conversation() -> None:
        nonlocal in_conversation, misfires
        in_conversation, misfires = False, 0
        print("[conversation over; say 'hey Jarvis' to start again]")
        beep(440)

    woken = False            # woken by interrupting Jarvis mid-sentence
    in_conversation = False  # after "hey Jarvis", keep listening without it until things go quiet
    misfires = 0             # recordings in a row that turned out to be just noise
    try:
        while True:
            if woken:
                woken = False
                beep(880)
                audio = record_until_silence()
            elif in_conversation:
                print("[listening...]")
                audio = record_until_silence(no_speech_timeout_s=FOLLOWUP_SECONDS if FOLLOWUP_SECONDS > 0 else 20)
                if not audio.size:
                    if FOLLOWUP_SECONDS > 0:
                        end_conversation()
                    continue
            else:
                listen_for_wake_word()
                print("[awake] listening...")
                beep(880)
                audio = record_until_silence()
            in_conversation = FOLLOWUP_SECONDS != 0

            heard = clean_transcript(transcribe(audio))
            if not heard:
                print("[nothing heard]")
                misfires += 1
                if in_conversation and misfires >= 2 and FOLLOWUP_SECONDS > 0:
                    end_conversation()
                continue
            misfires = 0
            print(f"You: {heard}")

            said = heard.lower().strip(" .!?,")
            if said in EXIT_PHRASES:
                speak("Goodbye, sir.")
                break
            if said in SLEEP_PHRASES:
                speak("Very good, sir.")
                end_conversation()
                continue
            if said in CANCEL_PHRASES:
                continue  # e.g. "hey Jarvis... stop" just to cut it off

            history, response = respond(history, heard, confirm_voice, acknowledge_aloud)
            if response:
                print(f"Jarvis: {response}\n")
                woken = speak_until_woken(response)
                if woken:
                    print("[interrupted]")
    except KeyboardInterrupt:
        print("\nJarvis: Shutting down, sir.")


if __name__ == "__main__":
    if "--text" in sys.argv:
        run_text()
    elif "--tray" in sys.argv:
        import tray

        tray.run(run_voice)
    else:
        import tray

        if tray.claim_single_instance():
            run_voice()
        else:
            print("Jarvis is already running (look for its icon by the clock).")
