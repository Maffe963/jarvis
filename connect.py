"""Connect Jarvis to an online account (one-time browser sign-in).

    .venv/Scripts/python.exe connect.py spotify
    .venv/Scripts/python.exe connect.py google      # Google Calendar
    .venv/Scripts/python.exe connect.py status
"""
import os
import sys

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from tools import gcal, oauth, spotify  # noqa: E402  (after .env is loaded)

SERVICES = {
    "spotify": (spotify.connect, lambda: bool(spotify._client_id()), "SPOTIFY_CLIENT_ID",
                spotify.spotify_now_playing),
    "google": (gcal.connect, lambda: all(gcal._creds()), "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET",
               lambda: gcal.calendar_events(days=1)),
}


def main() -> None:
    which = sys.argv[1].lower() if len(sys.argv) > 1 else ""
    if which == "status":
        for name, (_, configured, _, check) in SERVICES.items():
            connected = os.path.exists(oauth._path(name))
            print(f"{name}: {'connected' if connected else 'not connected'}"
                  + ("" if configured() else " (missing keys in .env)"))
        return
    if which not in SERVICES:
        print(__doc__)
        return
    connect, configured, keys, check = SERVICES[which]
    if not configured():
        print(f"Add {keys} to .env first.")
        return
    connect()
    print("Connected! Quick check:", check())


if __name__ == "__main__":
    main()
