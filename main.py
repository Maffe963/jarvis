import re
import sys

from dotenv import load_dotenv

load_dotenv()

import anthropic

from brain import BackendError, think, warm_up
from memory.conversation import add_turn, load_history, save_history
from tools import reminders
from tools.system import set_announcer

EXIT_PHRASES = ("quit", "exit", "goodbye", "go to sleep")
CANCEL_PHRASES = ("stop", "cancel", "never mind", "nevermind", "shut up", "quiet", "that's all", "nothing")
YES_WORDS = {"yes", "yeah", "yep", "yup", "sure", "confirm", "confirmed", "affirmative", "okay", "ok", "absolutely"}
YES_PHRASES = ("do it", "go ahead", "go for it")
NO_WORDS = {"no", "nope", "nah", "not", "don't", "dont", "cancel", "stop", "never", "wait", "negative"}
MAX_SPOKEN_QUESTION = 90


def is_yes(answer: str) -> bool:
    """True only for a clear yes; any negative word anywhere wins ("yes, wait, no" is a no)."""
    answer = answer.lower()
    words = set(re.findall(r"[a-z']+", answer))
    if words & NO_WORDS:
        return False
    return bool(words & YES_WORDS) or any(p in answer for p in YES_PHRASES)


def respond(history: list[dict], user_text: str, confirm) -> tuple[list[dict], str]:
    """Run one turn. On failure the user's turn is dropped so the history stays valid."""
    history = add_turn(history, "user", user_text)
    try:
        response = think(history, confirm)
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
    history = add_turn(history, "assistant", response)
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
            history, response = respond(history, user_input, confirm_text)
            print(f"Jarvis: {response}\n")
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
        if len(question) > MAX_SPOKEN_QUESTION:
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
    woken = False  # already woken by interrupting Jarvis mid-sentence
    try:
        while True:
            if not woken:
                listen_for_wake_word()
            woken = False
            print("[awake] listening...")
            # Beep in the background so recording starts immediately.
            threading.Thread(target=winsound.Beep, args=(880, 120), daemon=True).start()
            heard = transcribe(record_until_silence())
            if not heard:
                print("[nothing heard]")
                continue
            print(f"You: {heard}")

            said = heard.lower().strip(" .!?,")
            if said in EXIT_PHRASES:
                speak("Goodbye, sir.")
                break
            if said in CANCEL_PHRASES:
                continue  # e.g. "hey Jarvis... stop" just to cut it off

            history, response = respond(history, heard, confirm_voice)
            print(f"Jarvis: {response}\n")
            woken = speak_until_woken(response)
            if woken:
                print("[interrupted]")
    except KeyboardInterrupt:
        print("\nJarvis: Shutting down, sir.")


if __name__ == "__main__":
    if "--text" in sys.argv:
        run_text()
    else:
        run_voice()
