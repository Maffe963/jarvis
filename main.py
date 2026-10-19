import os
import re
import sys
import threading
import time

from dotenv import load_dotenv

load_dotenv()

import anthropic

from brain import NEW_MESSAGE, BackendError, TaskInterrupted, cancel_task, standby_backend, think, warm_up
from memory.conversation import HISTORY_FILE, add_turn, load_history, save_history
from tools import caveman, reminders
from tools import offline, quick
from tools.registry import activity, run_tool, task_cancelled, wait_for_running_tool
from tools.system import quit_requested, set_announcer, standby_requested

# Close Jarvis itself (never the computer). Bare "shut down" is left to Claude, which asks which one you mean.
# In voice mode these put Jarvis on standby (only the wake word keeps running); in text mode they quit.
EXIT_PHRASES = {
    "quit", "exit", "goodbye", "bye", "goodbye jarvis", "bye jarvis", "quit jarvis", "exit jarvis",
    "close jarvis", "close yourself", "quit yourself", "exit yourself",
    "standby", "go on standby", "go into standby",
}
# These turn Jarvis off entirely (nothing keeps listening), until it's started again.
FULL_QUIT_PHRASES = {
    "quit completely", "exit completely", "quit jarvis completely", "exit jarvis completely",
    "close jarvis completely", "shut down jarvis completely", "turn off jarvis completely",
    "fully quit", "fully quit jarvis", "fully close jarvis", "quit for real", "close completely",
    "shut down jarvis", "shutdown jarvis", "turn off jarvis", "turn jarvis off", "jarvis off",
    "shut yourself down", "turn yourself off", "switch off", "switch yourself off", "power off jarvis",
}


def _normalized(text: str) -> str:
    words = re.sub(r"[^a-z' ]", " ", text.lower()).split()
    while words and words[0] in ("please", "ok", "okay", "now"):
        words.pop(0)
    while words and words[-1] in ("please", "now", "sir"):
        words.pop()
    return " ".join(words)


def is_exit(text: str) -> bool:
    return _normalized(text) in EXIT_PHRASES or is_full_quit(text)


def is_full_quit(text: str) -> bool:
    return _normalized(text) in FULL_QUIT_PHRASES
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


STOP_WORDS = {"no", "nope", "stop", "wait", "cancel", "hold", "on", "never", "mind", "nevermind", "that", "it",
               "please", "don't", "dont", "jarvis", "hey", "enough", "actually", "sorry", "abort", "halt", "up"}


def is_just_stop(text: str) -> bool:
    """'No, stop!' / 'wait wait' / 'hold on Jarvis': stop, with no correction to act on."""
    words = re.findall(r"[a-z']+", text.lower())
    return bool(words) and all(w in STOP_WORDS for w in words)


def is_yes(answer: str) -> bool:
    """True only for a clear yes; any negative word anywhere wins ("yes, wait, no" is a no)."""
    answer = answer.lower()
    words = set(re.findall(r"[a-z']+", answer))
    if words & NO_WORDS:
        return False
    return bool(words & YES_WORDS) or any(p in answer for p in YES_PHRASES)


_respond_lock = threading.Lock()  # voice and phone share one Claude: one request at a time
offline_note_given = threading.Event()  # say 'using my offline brain' once, not every time


def respond(history: list[dict], user_text: str, confirm, say, history_file: str = HISTORY_FILE,
            actions_out: list[str] | None = None, on_text=None, via: str = "") -> tuple[list[dict], str | None]:
    """Run one turn. On failure the user's turn is dropped so the history stays valid.

    `say(text)` delivers the instant "Yes sir, opening Spotify." when the first tool starts. The returned
    response is only what's left to say after that, and may be empty; it is None if the user stopped
    the task part-way (cancel_task), in which case the history notes what had already been done.
    `actions_out`, if given, receives what each tool actually reported, to show the user as proof.
    `on_text`, if given, receives the reply as Claude writes it (see FirstSentence).
    `via` ("voice", "telegram"): where the request came from; the dashboard's feed shows the conversation from there
    too (the dashboard reports its own requests itself, so it passes nothing).
    """
    show = _feed(via) if via else (lambda direction, text: None)
    show("in", user_text.rsplit("]\n", 1)[-1])  # without the [note] phone messages start with

    def said(text: str) -> None:
        show("out", text)
        say(text)

    with _respond_lock:
        result = None
        try:
            result = _respond(history, user_text, confirm, said, history_file, on_text)
            return result
        except Exception as e:
            show("out", f"Something went wrong: {e}")
            raise
        finally:
            if result is not None:
                show("out", "Stopped." if result[1] is None else result[1])
            if actions_out is not None:
                actions_out[:] = list(activity)


def _feed(via: str):
    from team import events

    def show(direction: str, text: str) -> None:
        if text and text.strip():
            events.emit("jarvis", direction=direction, text=text.strip(), via=via)
    return show


class FirstSentence:
    """Starts saying the first sentence of a reply as soon as Claude has written it, instead of waiting for
    the whole reply. The rest is said afterwards (where it can be interrupted as usual)."""

    END = re.compile(r"[.!?](?=\s)")  # a sentence end followed by more text (so not '3.5' or a cut-off)

    def __init__(self, say) -> None:
        self.say, self.text, self.spoken = say, "", ""

    def __call__(self, piece) -> None:
        if piece is NEW_MESSAGE:
            self.text = ""  # e.g. text written before a tool call; start over
            return
        if self.spoken:
            return
        self.text += piece
        draft = self.text.lstrip()
        if draft.startswith("["):  # maybe [done], which is never spoken
            return
        end = self.END.search(draft)
        if end and end.end() >= 12:
            self.spoken = draft[:end.end()].strip()
            self.say(self.spoken)

    def rest(self, response: str) -> str:
        """What's left to say of the final reply."""
        if self.spoken and self.spoken in response:
            return response.split(self.spoken, 1)[1].strip()
        return response


def busy() -> bool:
    """True while a request (voice or phone) is being worked on."""
    return _respond_lock.locked()


def exit_when_asked() -> None:
    """Quit once Jarvis has been asked to (e.g. 'update yourself' or 'turn off' from the phone) and the request
    is finished, even if the voice loop is just sitting there listening."""
    def watch():
        while not (quit_requested.is_set() and not busy()):
            time.sleep(0.5)
        time.sleep(3)  # let the voice loop exit by itself, and the phone reply go out
        import tray

        tray._shutdown()

    threading.Thread(target=watch, daemon=True).start()


def _respond(history: list[dict], user_text: str, confirm, say, history_file: str,
             on_text=None) -> tuple[list[dict], str | None]:
    history = add_turn(history, "user", user_text)
    acknowledged: list[str] = []
    task_cancelled.clear()
    activity.clear()

    def acknowledge(phrase: str) -> None:
        if not acknowledged:  # once per request, even if several tools run
            acknowledged.append(f"Yes sir, {phrase}.")
            say(acknowledged[0])

    # Common simple commands ("pause", "volume up", "what time is it") are done instantly, without Claude.
    instant = quick.match(user_text.rsplit("]\n", 1)[-1])  # (phone messages start with a [note])
    if instant:
        tool, value = instant
        if tool is None:
            response = value
        else:
            result = str(run_tool(tool, value, confirm, acknowledge))
            failed = any(w in result.lower() for w in ("error", "isn't", "refused", "no active", "unknown", "couldn't"))
            response = result if failed or not acknowledged else ""
        history = add_turn(history, "assistant", " ".join(acknowledged + [response]).strip())
        save_history(history, history_file)
        return history, response

    try:
        response = think(history, confirm, acknowledge, on_text)
        response = response.replace("[done]", "").strip()
        if not response and not acknowledged:
            response = "Done, sir."
    except TaskInterrupted:
        wait_for_running_tool()
        done = "; ".join(activity) or "nothing yet"
        history = add_turn(history, "assistant", f"[The user interrupted me before I finished. Already done: {done}. "
                                                 "If what they say next corrects a lasting preference, remember it.]")
        save_history(history, history_file)
        return history, None
    except (BackendError, anthropic.AuthenticationError, anthropic.RateLimitError, anthropic.APIConnectionError,
            anthropic.APIError) as e:
        problem = {
            anthropic.AuthenticationError: "My API key was rejected, sir. Please check the ANTHROPIC_API_KEY in the .env file.",
            anthropic.RateLimitError: "I'm being rate limited, sir. Give me a moment and try again.",
            anthropic.APIConnectionError: "I can't reach the server, sir. Please check the internet connection.",
        }.get(type(e), str(e) if isinstance(e, BackendError) else "The server returned an error, sir. Please try again.")
        print(f"[claude unavailable] {problem}")
        # Claude can't be reached: let the small model on this PC handle it if it can.
        offline_reply = None if acknowledged else offline.answer(user_text.rsplit("]\n", 1)[-1], confirm, acknowledge)
        if offline_reply is None:
            history.pop()
            return history, problem
        response = offline_reply if not acknowledged or "error" in offline_reply.lower() else ""
        if not offline_note_given.is_set():
            offline_note_given.set()
            say("Claude isn't reachable right now, sir, so I'm using my offline brain for simple things.")
    history = add_turn(history, "assistant", " ".join(acknowledged + [response]).strip())
    save_history(history, history_file)
    return history, response


def confirm_text(question: str) -> bool:
    return input(f"Jarvis: {question} (y/n) ").strip().lower() in ("y", "yes")


def start_phone():
    """Phone control through Telegram, if it's set up in .env (see telegram_bot.py). Returns the bot or None."""
    import telegram_bot
    import tray

    return telegram_bot.start(respond, load_history, cancel_task, tray.turn_off)


def start_dashboard() -> None:
    """The live agent dashboard (http://127.0.0.1:8765, opened with a secret link: say 'open the dashboard')."""
    from dashboard import server

    server.start(respond)


def start_team(say) -> None:
    """The agent team runs in its own process (team_service.py) so it carries on while Jarvis is off. Start it if
    it isn't running, and pass on what it does: `say` tells the user about announcements and missed messages."""
    from team import client

    client.follow(say)


def run_remote() -> None:
    """Phone control only: no microphone or speech, just the Telegram bot (and timers/reminders to the phone)."""
    warm_up()
    threading.Thread(target=quick.load_apps, daemon=True).start()  # so "open Spotify" is instant
    phone = start_phone()
    if phone is None:
        print("Phone control isn't set up: add TELEGRAM_BOT_TOKEN to .env (see telegram_bot.py).")
        return
    set_announcer(lambda msg: (print(f"Jarvis: {msg}"), phone.notify(msg)))
    start_dashboard()
    start_team(lambda msg: print(f"Jarvis: {msg}"))  # the team service messages the phone itself
    reminders.start()
    caveman.start()  # sends the Caveman test's results when it ends
    exit_when_asked()
    print("Jarvis online (phone only). Message your bot on Telegram. Ctrl+C to quit.")
    try:
        while not quit_requested.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def run_text() -> None:
    warm_up()
    threading.Thread(target=quick.load_apps, daemon=True).start()  # so "open Spotify" is instant
    reminders.start()
    caveman.start()  # sends the Caveman test's results when it ends
    start_team(lambda msg: print(f"\nJarvis: {msg}"))
    print("Jarvis online (text mode). Type 'quit' to exit.\n")
    history = load_history()
    try:
        while True:
            user_input = input("You: ").strip()
            if not user_input:
                continue
            if is_exit(user_input):
                print("Jarvis: Goodbye, sir.")
                break
            # Run the request in the background so Ctrl+C can stop it part-way instead of quitting.
            result: list = []
            worker = threading.Thread(target=lambda: result.append(respond(
                history, user_input, confirm_text, lambda text: print(f"Jarvis: {text}"))), daemon=True)
            worker.start()
            while worker.is_alive():
                try:
                    worker.join(0.2)
                except KeyboardInterrupt:
                    cancel_task()
                    print("\n[stopping...]")
            history, response = result[0]
            if response is None:
                print("Jarvis: Stopped, sir. What would you like instead?")
            elif response:
                print(f"Jarvis: {response}")
            print()
            if quit_requested.is_set() or standby_requested.is_set():
                break  # no wake word in text mode, so standby just quits
    except KeyboardInterrupt:
        print("\nJarvis: Shutting down, sir.")


def run_voice() -> None:
    import winsound

    from voice import barge_in, mic, select_microphone, speaker, stt, wake_word
    from voice.stt import record_until_silence, transcribe
    from voice import tts
    from voice.tts import speak
    from voice.wake_word import listen_for_wake_word

    def acknowledge_aloud(text: str) -> None:
        # In the background, so the command runs while Jarvis is saying "Yes sir, ...".
        print(f"Jarvis: {text}")
        threading.Thread(target=speak, args=(text,), daemon=True).start()

    def speak_interruptibly(text: str) -> list | None:
        """Speak, but stop as soon as the user talks over Jarvis (or says 'hey Jarvis').

        Returns the audio heard so far if interrupted, so the recording carries on from it.
        """
        done = threading.Event()
        result: list = []
        watcher = threading.Thread(
            target=lambda: result.append(barge_in.watch(done, stt.speech_threshold())), daemon=True)
        watcher.start()
        speak(text)
        done.set()
        watcher.join()
        if not result or result[0] is None:
            return None
        reason, heard = result[0]
        print(f"[interrupted by {reason}]")
        return heard

    confirming = threading.Event()  # a yes/no answer isn't an interruption of the task

    def confirm_voice(question: str) -> bool:
        confirming.set()
        try:
            return ask_yes_no(question)
        finally:
            confirming.clear()

    def run_task(history: list[dict], text: str) -> tuple[list[dict], str | None, str | None]:
        """respond(), while listening for the user speaking up. Returns (history, response, what they said)."""
        done = threading.Event()
        said: list = []

        def listen():
            while not done.is_set():
                text = clean_transcript(stt.listen_during_task(done, confirming) or "")
                if text:
                    said.append(text)
                    print(f"You (during the task): {text}")
                    cancel_task()
                    return

        watcher = threading.Thread(target=listen, daemon=True)
        if barge_in.mode() != "off":
            watcher.start()
        def say_early(sentence: str) -> None:
            print(f"Jarvis: {sentence}")
            threading.Thread(target=speak, args=(sentence,), daemon=True).start()

        first = FirstSentence(say_early)
        history, response = respond(history, text, confirm_voice, acknowledge_aloud, on_text=first, via="voice")
        done.set()
        if watcher.is_alive():
            watcher.join(timeout=2)
        if response:
            response = first.rest(response)  # the first sentence is already being said
        return history, response, (said[0] if said else None)

    def ask_yes_no(question: str) -> bool:
        print(f"Jarvis: {question}")
        # Long questions (e.g. a full PowerShell command) are shown, not read out.
        limit = MAX_SPOKEN_MESSAGE if question.startswith("Send ") else MAX_SPOKEN_QUESTION
        if len(question) > limit:
            question = question.split(": ")[0].rstrip("?") + "? The details are on screen."
        answer = ""
        for prompt in (question, "Sorry sir, I didn't catch that. Yes or no?"):
            speak(prompt)
            mic.flush()  # don't mistake Jarvis's own voice for the answer
            # A one-word "yes" is short and can be quiet, so any sound at all starts the recording.
            answer = transcribe(record_until_silence(no_speech_timeout_s=7, speech_start_blocks=1))
            print(f"You: {answer or '(no answer)'}")
            if answer:
                break
        approved = is_yes(answer)
        if not approved:
            speak("Cancelled.")
        return approved

    phone = start_phone()
    start_dashboard()
    set_announcer(lambda msg: (print(f"\nJarvis: {msg}"), speak(msg), phone and phone.notify(msg)))
    history = load_history()
    microphone = select_microphone()
    print("Starting up...")
    warm_up()
    threading.Thread(target=quick.load_apps, daemon=True).start()  # so "open Spotify" is instant
    wake_word._get_model()
    stt._get_model()
    stt.warm_up()
    tts.warm_up()
    if speaker.enrolled() and speaker.available():
        speaker.embed(stt.np.zeros(32000, dtype=stt.np.float32))  # load the voice model now, not on first use
        print("Voice lock on: only your voice is answered (JARVIS_VOICE_LOCK=off to disable).")
    elif speaker.available():
        print("Tip: run enroll_voice.py once so Jarvis only answers your voice.")
    print(f"Jarvis online (voice mode), listening on: {microphone}")
    print("Say 'hey Jarvis'. Ctrl+C to quit.\n")
    speak("Jarvis online.")
    reminders.start()
    caveman.start()  # sends the Caveman test's results when it ends
    start_team(lambda msg: (print(f"\nJarvis: {msg}"), speak(msg)))
    exit_when_asked()

    def beep(freq: int) -> None:
        # In the background so recording starts immediately.
        threading.Thread(target=winsound.Beep, args=(freq, 120), daemon=True).start()

    def end_conversation() -> None:
        nonlocal in_conversation, misfires
        in_conversation, misfires = False, 0
        print("[conversation over; say 'hey Jarvis' to start again]")
        beep(440)

    def go_on_standby(already_announced: bool = False) -> None:
        """Free Claude Code and the speech model; only the small wake-word detector keeps running."""
        nonlocal in_conversation, misfires, on_standby
        speak("Say hey Jarvis when you need me." if already_announced
              else "Going on standby, sir. Say hey Jarvis when you need me.")
        standby_backend()
        stt.unload_model()
        in_conversation, misfires, on_standby = False, 0, True
        print("[standby] say 'hey Jarvis' to wake me; timers and reminders still work")
        beep(440)

    def wait_for_speech_to_finish() -> None:
        time.sleep(0.3)
        tts.wait_until_quiet()  # e.g. "Yes sir, closing Jarvis." is still being said in the background

    early = stt.Early(speaker.is_user)  # transcribes at the first pause, so the text is ready when you finish
    interrupted = None       # audio of the user talking over Jarvis, to carry on recording from
    pending = None           # a correction the user spoke while Jarvis was working: the next request
    in_conversation = False  # after "hey Jarvis", keep listening without it until things go quiet
    on_standby = False       # Claude Code and the speech model are unloaded until the wake word
    misfires = 0             # recordings in a row that turned out to be just noise
    try:
        while True:
            early.started = None  # nothing guessed yet for this recording
            if pending is not None:
                heard, pending = pending, None
            else:
                if interrupted is not None:
                    audio = record_until_silence(already_heard=interrupted, early=early)
                    interrupted = None
                elif in_conversation:
                    print("[listening...]")
                    audio = record_until_silence(
                        no_speech_timeout_s=FOLLOWUP_SECONDS if FOLLOWUP_SECONDS > 0 else 20, early=early)
                    if not audio.size:
                        if FOLLOWUP_SECONDS > 0:
                            end_conversation()
                        continue
                else:
                    listen_for_wake_word()
                    print("[awake] listening...")
                    beep(880)
                    if on_standby:
                        # Reload while you're still talking, so it's ready by the time you finish.
                        on_standby = False
                        threading.Thread(target=warm_up, daemon=True).start()
                        threading.Thread(target=stt.warm_up, daemon=True).start()
                    audio = record_until_silence(early=early)
                guess = early.result(audio, stt.speech_threshold()) if audio.size else None
                (mine, score), text = guess or ((speaker.is_user(audio) if audio.size else (True, None)), None)
                if not mine:  # the TV, someone else, a video...
                    print(f"[ignored: not your voice (match {score:.2f})]")
                    heard = ""
                else:
                    heard = clean_transcript(transcribe(audio) if text is None else text)
            in_conversation = FOLLOWUP_SECONDS != 0
            if not heard:
                print("[nothing heard]")
                misfires += 1
                if in_conversation and misfires >= 2 and FOLLOWUP_SECONDS > 0:
                    end_conversation()
                continue
            misfires = 0
            print(f"You: {heard}")

            said = heard.lower().strip(" .!?,")
            if is_full_quit(heard):
                speak("Jarvis offline.")
                break
            if is_exit(heard):
                go_on_standby()
                continue
            if said in SLEEP_PHRASES:
                speak("Very good, sir.")
                end_conversation()
                continue
            if said in CANCEL_PHRASES:
                continue  # e.g. "hey Jarvis... stop" just to cut it off

            history, response, spoke_up = run_task(history, heard)
            if spoke_up is not None:
                # The user cut in while Jarvis was working: "stop" just stops, anything else is a correction.
                if is_just_stop(spoke_up):
                    speak("Stopped, sir.")
                else:
                    pending = spoke_up
                continue
            if response:
                print(f"Jarvis: {response}\n")
                interrupted = speak_interruptibly(response)
            if quit_requested.is_set():
                wait_for_speech_to_finish()
                break
            if standby_requested.is_set():
                standby_requested.clear()
                wait_for_speech_to_finish()
                go_on_standby(already_announced=True)  # "Yes sir, going on standby." was just said
    except KeyboardInterrupt:
        print("\nJarvis: Shutting down, sir.")


if __name__ == "__main__":
    if "--text" in sys.argv:
        run_text()
    elif "--tray" in sys.argv:
        import tray

        tray.run(run_remote if "--remote" in sys.argv else run_voice)
    else:
        import tray

        if tray.claim_single_instance():
            tray.listen_for_off_switch()
            (run_remote if "--remote" in sys.argv else run_voice)()
        else:
            tray.turn_off_running_jarvis()
            print("Jarvis was already running, so I've turned it off. Run this again to turn it back on.")
