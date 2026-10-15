"""Pair a Telegram private chat and save its credentials without displaying the token."""

import argparse
import getpass
import json
import os
import re
import secrets
import tempfile
import urllib.error
import urllib.request
import warnings
from pathlib import Path

from common import ROOT


class SetupError(Exception):
    """An actionable setup failure that is safe to display."""


def telegram_api(token: str, method: str, **parameters):
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(parameters).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        message = f"Telegram {method} failed (HTTP {exc.code})."
        if exc.code == 409:
            message += " Check whether this bot already has a webhook or another update reader."
        raise SetupError(message) from None
    except (OSError, ValueError):
        raise SetupError(
            f"Cannot read Telegram {method}; check connectivity and the token."
        ) from None
    if not isinstance(payload, dict) or payload.get("ok") is not True or "result" not in payload:
        raise SetupError(f"Telegram {method} returned an invalid response.")
    return payload["result"]


def wait_for_message():
    try:
        with open("/dev/tty") as terminal:
            print("After sending the code to the bot, press Enter here: ", end="", flush=True)
            if not terminal.readline():
                raise SetupError("No terminal input received; settings were not changed.")
    except OSError:
        raise SetupError("Run this command in an interactive terminal.") from None


def paired_chat(updates: list, code: str) -> str:
    matches = set()
    for update in updates:
        message = update.get("message", {})
        chat = message.get("chat", {})
        if message.get("text") == code and chat.get("type") == "private":
            matches.add(str(chat["id"]))
    if len(matches) != 1:
        raise SetupError(
            "Setup code not found in exactly one private chat. "
            "Run again and send the new code to the displayed bot. Settings were not changed."
        )
    return matches.pop()


def save_settings(path: Path, token: str, chat_id: str):
    updates = {"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": chat_id}
    lines = path.read_text().splitlines()
    seen = set()
    for index, line in enumerate(lines):
        key = line.partition("=")[0].strip()
        if key in updates:
            lines[index] = f"{key}={updates[key]}"
            seen.add(key)
    lines.extend(f"{key}={value}" for key, value in updates.items() if key not in seen)
    fd, staged = tempfile.mkstemp(prefix=".env.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write("\n".join(lines) + "\n")
        os.replace(staged, path)
    finally:
        Path(staged).unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--send-test", action="store_true", help="Send a confirmation to the paired private chat"
    )
    args = parser.parse_args(argv)
    path = ROOT / ".env"
    if not path.is_file():
        raise SetupError("Create .env with scripts/configure.py before configuring Telegram.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            token = getpass.getpass("Telegram bot token (hidden): ").strip()
    except (getpass.GetPassWarning, EOFError):
        raise SetupError("Run in an interactive terminal so the token stays hidden.") from None
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token):
        raise SetupError("Invalid bot token format; settings were not changed.")

    bot = telegram_api(token, "getMe")
    if not isinstance(bot, dict) or not re.fullmatch(r"[A-Za-z0-9_]+", bot.get("username", "")):
        raise SetupError("Telegram did not return a valid bot username; settings were not changed.")
    code = "triage-" + secrets.token_hex(6)
    print(f"\nOpen your bot: https://t.me/{bot['username']}")
    print("Press Start if needed, then send this exact message in your private chat:")
    print(f"\n{code}\n", flush=True)
    wait_for_message()
    updates = telegram_api(token, "getUpdates")
    if not isinstance(updates, list):
        raise SetupError("Telegram returned invalid updates; settings were not changed.")
    chat_id = paired_chat(updates, code)
    if args.send_test:
        telegram_api(
            token,
            "sendMessage",
            chat_id=chat_id,
            text="CI Triage: Telegram connected. Trigger a new CI run to test notifications.",
        )
    save_settings(path, token, chat_id)
    print(f"Saved Telegram settings for @{bot['username']}, private chat {chat_id}.")
    if args.send_test:
        print("Telegram accepted the connection confirmation.")
    print("Recreate the triage container, then trigger a new CI run.")


if __name__ == "__main__":
    try:
        main()
    except SetupError as exc:
        raise SystemExit(str(exc)) from None
