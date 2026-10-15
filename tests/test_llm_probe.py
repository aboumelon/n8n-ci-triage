import importlib
import io
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest


@pytest.fixture
def probe(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("check_llm")


@pytest.fixture
def env():
    return {
        "LLM_BASE_URL": "https://api.apinex.bond/v1",
        "LLM_MODEL": "free/deepseek-v4.1-flash",
        "LLM_API_KEY": "sk-apx_private-check-key",
    }


def completion(content='{"ok":true}', finish_reason="stop", **message):
    return json.dumps(
        {"choices": [{"message": {"content": content, **message}, "finish_reason": finish_reason}]}
    ).encode()


@pytest.mark.parametrize("status", [200, 402])
def test_probe_uses_one_small_request_without_logs_and_preserves_settings(
    probe, env, monkeypatch, status
):
    calls = []
    original = env.copy()

    class Response(io.BytesIO):
        status = 200

    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            assert timeout == 20
            data = json.loads(request.data)
            assert data["model"] == original["LLM_MODEL"]
            assert data["max_tokens"] == 32 and data["stream"] is False
            assert data["response_format"] == {"type": "json_object"}
            assert data["messages"] == [
                {"role": "user", "content": 'Return only the JSON object {"ok":true}.'}
            ]
            assert request.get_header("Authorization") == "Bearer " + original["LLM_API_KEY"]
            if status == 402:
                error = {
                    "error": {"code": "insufficient_balance", "message": original["LLM_API_KEY"]}
                }
                raise urllib.error.HTTPError(
                    request.full_url,
                    status,
                    original["LLM_API_KEY"],
                    {},
                    io.BytesIO(json.dumps(error).encode()),
                )
            return Response(completion())

    monkeypatch.setattr(urllib.request, "build_opener", lambda _: Opener())
    result = probe.probe(env)
    assert len(calls) == 1 and env == original
    assert result["http_status"] == status
    assert result["status"] == ("accepted" if status == 200 else "rejected")
    assert original["LLM_API_KEY"] not in json.dumps(result)
    if status == 402:
        assert result["provider_code"] == "insufficient_balance"


def test_probe_never_prints_unknown_error_codes_or_raw_messages(probe, env, monkeypatch):
    class Opener:
        def open(self, request, timeout):
            error = {
                "error": {"code": env["LLM_API_KEY"], "type": "untrusted", "message": "private"}
            }
            raise urllib.error.HTTPError(
                request.full_url, 402, "private", {}, io.BytesIO(json.dumps(error).encode())
            )

    monkeypatch.setattr(urllib.request, "build_opener", lambda _: Opener())
    result = probe.probe(env)
    assert "provider_code" not in result and "provider_type" not in result
    assert "private" not in json.dumps(result)


def test_probe_does_not_call_paid_aliases(probe, env, monkeypatch):
    env["LLM_MODEL"] = "deepseek-v4.1-flash"
    monkeypatch.setattr(urllib.request, "build_opener", lambda _: pytest.fail("Paid request"))
    with pytest.raises(probe.SetupError, match="free/"):
        probe.probe(env)


@pytest.mark.parametrize("free", [True, False])
def test_other_amd_models_still_require_free_catalog_before_any_completion(
    probe, env, monkeypatch, free
):
    env.update(LLM_BASE_URL="https://developer.amd.com.cn/radeon/api/v1", LLM_MODEL="Other-Model")
    calls = []

    class Response(io.BytesIO):
        status = 200

    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            if request.full_url.endswith("/models"):
                return Response(
                    json.dumps(
                        {"data": [{"id": "Other-Model", "free": free, "json_output": True}]}
                    ).encode()
                )
            assert free is True and len(calls) == 2
            assert timeout == 30
            return Response(completion())

    monkeypatch.setattr(urllib.request, "build_opener", lambda _: Opener())
    if free:
        assert probe.probe(env)["status"] == "accepted"
        assert len(calls) == 2
    else:
        with pytest.raises(probe.SetupError, match="free=true"):
            probe.probe(env)
        assert len(calls) == 1


def test_nested_amd_rate_limit_error_is_reported_without_raw_details(probe, env, monkeypatch):
    class Opener:
        def open(self, request, timeout):
            error = {
                "detail": {
                    "error": {
                        "code": "token_concurrency_rate_limit_exceeded",
                        "message": env["LLM_API_KEY"],
                    }
                }
            }
            raise urllib.error.HTTPError(
                request.full_url,
                429,
                "Too many requests",
                {},
                io.BytesIO(json.dumps(error).encode()),
            )

    monkeypatch.setattr(urllib.request, "build_opener", lambda _: Opener())
    result = probe.probe(env)
    assert result["provider_code"] == "token_concurrency_rate_limit_exceeded"
    assert env["LLM_API_KEY"] not in json.dumps(result)


@pytest.mark.parametrize(
    ("content", "finish_reason", "extra"),
    [
        ("", "stop", {}),
        ("provider-private-output", "stop", {}),
        ('{"ok":1}', "stop", {}),
        ('{"ok":true,"unexpected":1}', "stop", {}),
        ('{"ok":true}', "length", {}),
        ('{"ok":true}', "stop", {"tool_calls": [{"id": "private-tool"}]}),
    ],
)
def test_json_probe_rejects_incomplete_or_unstructured_output_without_echoing_content(
    probe, env, monkeypatch, content, finish_reason, extra
):
    class Response(io.BytesIO):
        status = 200

    class Opener:
        def open(self, request, timeout):
            return Response(completion(content, finish_reason, **extra))

    monkeypatch.setattr(urllib.request, "build_opener", lambda _: Opener())
    result = probe.probe(env)
    assert result["status"] == "invalid_response" and result["http_status"] == 200
    assert "provider-private-output" not in json.dumps(result)
    assert "private-tool" not in json.dumps(result)
