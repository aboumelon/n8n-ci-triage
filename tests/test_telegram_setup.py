import importlib
import io
import json
import stat
import urllib.error
from pathlib import Path

import pytest


@pytest.fixture
def telegram_setup(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("configure_telegram")


def message(chat_id, text, chat_type="private"):
    return {"message": {"text": text, "chat": {"id": chat_id, "type": chat_type}}}


def test_pairing_uses_the_code_and_private_destination(telegram_setup):
    updates = [
        message(111, "/start"),
        message(-222, "triage-proof", "group"),
        message(333, "triage-proof"),
        message(333, "triage-proof"),
    ]
    assert telegram_setup.paired_chat(updates, "triage-proof") == "333"
    with pytest.raises(telegram_setup.SetupError, match="exactly one private chat"):
        telegram_setup.paired_chat([*updates, message(444, "triage-proof")], "triage-proof")


def test_setup_saves_verified_destination_and_preserves_other_settings(
    telegram_setup, tmp_path, monkeypatch, capsys
):
    path = tmp_path / ".env"
    path.write_text("GITHUB_TOKEN=keep-github\nWEBHOOK_SECRET=keep-secret\nTELEGRAM_BOT_TOKEN=\n")
    token = "12345:synthetic_token_123456789"
    calls = []

    def api(received_token, method, **parameters):
        assert received_token == token
        calls.append((method, parameters))
        return {
            "getMe": {"username": "ci_test_bot"},
            "getUpdates": [message(111, "/start"), message(333, "triage-proof")],
            "sendMessage": {"message_id": 1},
        }[method]

    monkeypatch.setattr(telegram_setup, "ROOT", tmp_path)
    monkeypatch.setattr(telegram_setup.getpass, "getpass", lambda prompt: token)
    monkeypatch.setattr(telegram_setup.secrets, "token_hex", lambda size: "proof")
    monkeypatch.setattr(telegram_setup, "wait_for_message", lambda: None)
    monkeypatch.setattr(telegram_setup, "telegram_api", api)
    telegram_setup.main(["--send-test"])

    values = dict(line.split("=", 1) for line in path.read_text().splitlines())
    assert values == {
        "GITHUB_TOKEN": "keep-github",
        "WEBHOOK_SECRET": "keep-secret",
        "TELEGRAM_BOT_TOKEN": token,
        "TELEGRAM_CHAT_ID": "333",
    }
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert calls[-1][0] == "sendMessage" and calls[-1][1]["chat_id"] == "333"
    assert token not in capsys.readouterr().out
    assert not list(tmp_path.glob(".env.*"))


@pytest.mark.parametrize("failure", ["missing-code", "rejected-test"])
def test_failed_setup_preserves_existing_configuration(
    telegram_setup, tmp_path, monkeypatch, failure
):
    path = tmp_path / ".env"
    original = "GITHUB_TOKEN=keep-github\nTELEGRAM_BOT_TOKEN=old\nTELEGRAM_CHAT_ID=111\n"
    path.write_text(original)
    calls = []

    def api(token, method, **parameters):
        calls.append(method)
        if method == "getMe":
            return {"username": "ci_test_bot"}
        if method == "getUpdates":
            return [] if failure == "missing-code" else [message(333, "triage-proof")]
        raise telegram_setup.SetupError("Telegram sendMessage failed (HTTP 403).")

    monkeypatch.setattr(telegram_setup, "ROOT", tmp_path)
    monkeypatch.setattr(telegram_setup.getpass, "getpass", lambda prompt: "12345:synthetic_token")
    monkeypatch.setattr(telegram_setup.secrets, "token_hex", lambda size: "proof")
    monkeypatch.setattr(telegram_setup, "wait_for_message", lambda: None)
    monkeypatch.setattr(telegram_setup, "telegram_api", api)
    with pytest.raises(telegram_setup.SetupError):
        telegram_setup.main(["--send-test"])
    assert path.read_text() == original
    if failure == "missing-code":
        assert "sendMessage" not in calls


@pytest.mark.parametrize("failure", ["http", "network", "invalid-json"])
def test_api_errors_do_not_display_token(telegram_setup, monkeypatch, failure):
    token = "12345:synthetic_token"
    url = f"https://api.telegram.org/bot{token}/getUpdates"

    def fail(*args, **kwargs):
        if failure == "http":
            raise urllib.error.HTTPError(url, 409, "Conflict", {}, None)
        if failure == "network":
            raise urllib.error.URLError(url)
        return io.BytesIO(b"not json")

    monkeypatch.setattr(telegram_setup.urllib.request, "urlopen", fail)
    with pytest.raises(telegram_setup.SetupError) as error:
        telegram_setup.telegram_api(token, "getUpdates")
    assert token not in str(error.value)


def test_api_sends_json_and_reads_telegram_response(telegram_setup, monkeypatch):
    def respond(request, timeout):
        assert request.get_method() == "POST" and timeout == 20
        assert json.loads(request.data) == {"chat_id": "333", "text": "Connection check"}
        return io.BytesIO(b'{"ok": true, "result": {"message_id": 1}}')

    monkeypatch.setattr(telegram_setup.urllib.request, "urlopen", respond)
    result = telegram_setup.telegram_api(
        "12345:synthetic_token", "sendMessage", chat_id="333", text="Connection check"
    )
    assert result["message_id"] == 1
