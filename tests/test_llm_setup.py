import importlib
import io
import json
import stat
import urllib.error
from pathlib import Path

import pytest


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("configure_llm")


def test_atomic_settings_update_preserves_other_credentials(setup, tmp_path):
    path = tmp_path / ".env"
    path.write_text("# Keep this\nGITHUB_TOKEN=unchanged\nLLM_ENABLED=false\nLLM_API_KEY=old\n")
    setup.save_settings(path, setup.DEFAULT_BASE_URL, setup.DEFAULT_MODEL, "new-private-key")
    text = path.read_text()
    assert "GITHUB_TOKEN=unchanged" in text and "# Keep this" in text
    assert "LLM_API_KEY=new-private-key" in text and "LLM_API_KEY=old" not in text
    assert "LLM_ENABLED=true" in text and text.count("LLM_ENABLED=") == 1
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    ("base_url", "model"),
    [
        ("http://api.test/v1", "free/model"),
        ("https://user:secret@api.test/v1", "free/model"),
        ("https://api.test/v1?token=secret", "free/model"),
        ("https://api.test/v1\nINJECT=true", "free/model"),
        ("https://api.test/v1", "paid/model"),
        ("https://api.test/v1", "free/model\nINJECT=true"),
    ],
)
def test_setup_rejects_unsafe_urls_and_nonfree_aliases(setup, base_url, model):
    with pytest.raises(setup.SetupError):
        setup.validate_options(base_url, model)


def test_catalog_requires_exact_selected_model_and_does_not_follow_redirects(setup, monkeypatch):
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            return io.BytesIO(json.dumps({"data": [{"id": "free/another-model"}]}).encode())

    def build_opener(redirect_handler):
        assert (
            redirect_handler.redirect_request(None, None, 302, "", {}, "https://evil.test") is None
        )
        return Opener()

    monkeypatch.setattr(setup.urllib.request, "build_opener", build_opener)
    with pytest.raises(setup.SetupError, match="not in your authenticated catalog"):
        setup.verify_catalog(setup.DEFAULT_BASE_URL, setup.DEFAULT_MODEL, "private-token")
    assert len(calls) == 1 and calls[0].get_header("Authorization") == "Bearer private-token"
    assert calls[0].get_header("User-agent") == "n8n-ci-triage/0.1"
    assert calls[0].get_header("Accept") == "application/json"
    assert calls[0].full_url.endswith("/v1/models")


def test_failed_verification_does_not_change_env_or_echo_key(setup, monkeypatch, tmp_path, capsys):
    path = tmp_path / ".env"
    original = "GITHUB_TOKEN=unchanged\nLLM_ENABLED=false\n"
    path.write_text(original)
    monkeypatch.setattr(setup, "ROOT", tmp_path)
    monkeypatch.setattr(setup.getpass, "getpass", lambda _: "sk-apx_private-token-value")

    class Opener:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(request.full_url, 401, "private-token-value", {}, None)

    monkeypatch.setattr(setup.urllib.request, "build_opener", lambda _: Opener())
    with pytest.raises(setup.SetupError, match="HTTP 401") as error:
        setup.main([])
    assert path.read_text() == original
    assert "private-token-value" not in str(error.value) + capsys.readouterr().out


@pytest.mark.parametrize(
    ("content_type", "body", "expected"),
    [
        ("text/plain; charset=UTF-8", b"error code: 1010", "web access denial"),
        ("text/html", b"Forbidden", "web access denial"),
        (
            "application/json",
            b'{"error":{"type":"authentication_error"}}',
            "account/model permissions",
        ),
        (
            "application/json",
            b'{"type":"https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1010/"}',
            "web access denial",
        ),
    ],
)
def test_forbidden_diagnostics_distinguish_web_denial_from_api_errors(
    setup, monkeypatch, content_type, body, expected
):
    class Opener:
        def open(self, request, timeout):
            error = urllib.error.HTTPError(
                request.full_url,
                403,
                "Forbidden private-token",
                {"Content-Type": content_type},
                io.BytesIO(body + b"private-token and untrusted response details"),
            )
            raise error

    monkeypatch.setattr(setup.urllib.request, "build_opener", lambda _: Opener())
    with pytest.raises(setup.SetupError, match=expected) as error:
        setup.verify_catalog(setup.DEFAULT_BASE_URL, setup.DEFAULT_MODEL, "private-token")
    assert "private-token" not in str(error.value)
