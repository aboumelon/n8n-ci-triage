import copy
import json

import httpx
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from ci_triage.api import create_app
from ci_triage.config import Settings
from ci_triage.db import runs
from ci_triage.llm import Explainer, explanation_markdown

from .conftest import FakeGitHub, payload, process, send

KEY = "sk-apx_" + "private-key-" * 4
ADVICE = {
    "summary": "A Python import failed; the dependency or environment needs investigation.",
    "findings": [
        {
            "finding_index": 0,
            "explanation": "The job could not import httpx in its Python environment.",
            "likely_causes": ["The CI dependency group may not include the required package."],
            "next_steps": [
                "Compare the manifest and lockfile with the dependencies installed by CI."
            ],
        }
    ],
}


def envelope(advice=None, **kwargs):
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": json.dumps(ADVICE if advice is None else advice)},
            }
        ],
        "usage": {"prompt_tokens": 210, "completion_tokens": 90, "total_tokens": 300},
        **kwargs,
    }


@pytest.fixture
def enabled(settings):
    settings.llm_enabled = True
    settings.llm_api_key = SecretStr(KEY)
    return settings


@pytest.fixture
def report():
    return {
        "conclusion": "failure",
        "repo": "private/repository",
        "run_url": "https://github.com/private/repository/actions/runs/123",
        "findings": [
            {
                "fingerprint": "unchanged-incident-id",
                "category": "dependency",
                "job": "backend-tests",
                "step": "Run pytest",
                "evidence": "ModuleNotFoundError: No module named 'httpx'",
                "markdown": "Original deterministic report",
                "log_available": True,
                "log_truncated": False,
            }
        ],
    }


def explainer_with(settings, handler):
    return Explainer(settings, httpx.Client(transport=httpx.MockTransport(handler)))


def test_input_is_bounded_redacted_and_excludes_repository_metadata(enabled, report):
    report["findings"] *= 8
    report["findings"][0]["evidence"] = f"{KEY} password=hunter2 " + "x" * 2_000
    report["findings"][0]["job"] = KEY + "x" * 300
    requests = []

    def handler(request):
        requests.append(request)
        data = json.loads(request.content)
        assert request.headers["Authorization"] == "Bearer " + KEY
        assert request.headers["User-Agent"] == "n8n-ci-triage/0.1"
        assert request.headers["Accept"] == "application/json"
        assert str(request.url) == "https://api.apinex.bond/v1/chat/completions"
        assert data["model"].startswith("free/")
        assert data["max_tokens"] == 1_200 and data["stream"] is False
        selected = json.loads(data["messages"][1]["content"])["findings"]
        assert len(selected) == 3
        assert all(len(item["evidence"]) <= 1_000 and len(item["job"]) <= 200 for item in selected)
        assert KEY not in request.content.decode() and "hunter2" not in request.content.decode()
        assert "private/repository" not in request.content.decode()
        assert "unchanged-incident-id" not in request.content.decode()
        assert "Original deterministic report" not in request.content.decode()
        advice = copy.deepcopy(ADVICE)
        advice["findings"] = [{**advice["findings"][0], "finding_index": i} for i in range(3)]
        return httpx.Response(200, json=envelope(advice))

    original = copy.deepcopy(report)
    result = explainer_with(enabled, handler).explain(report)
    assert result["status"] == "completed" and len(requests) == 1
    assert result["usage"]["total_tokens"] == 300 and result["latency_ms"] >= 0
    assert report == original


@pytest.mark.parametrize("scenario", ["disabled", "success", "unavailable"])
def test_no_external_call_when_disabled_or_no_failure_evidence(settings, report, scenario):
    settings.llm_enabled = scenario != "disabled"
    if scenario == "success":
        report["conclusion"] = "success"
    if scenario == "unavailable":
        report["findings"][0]["log_available"] = False

    def unexpected(request):
        pytest.fail("This report must not call any provider")

    result = explainer_with(settings, unexpected).explain(report)
    assert result["status"] == ("disabled" if scenario == "disabled" else "skipped")


@pytest.mark.parametrize("status", [401, 429, 500, 302])
def test_provider_errors_are_safe_and_do_not_retry_or_switch_models(enabled, report, status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status,
            json={"error": "credential=" + KEY},
            headers={"Location": "https://other-provider.test/paid-model"},
        )

    result = explainer_with(enabled, handler).explain(report)
    assert result["status"] == "fallback" and result["http_status"] == status
    assert len(calls) == 1 and KEY not in json.dumps(result)


@pytest.mark.parametrize("error", [httpx.ReadTimeout, httpx.ConnectError])
def test_network_failure_returns_safe_fallback(enabled, report, error):
    def handler(request):
        raise error("Do not persist this private detail: " + KEY)

    result = explainer_with(enabled, handler).explain(report)
    assert result["status"] == "fallback"
    assert result["reason"] in {"timeout", "connection_error"}
    assert KEY not in json.dumps(result)


@pytest.mark.parametrize(
    "bad",
    [
        [],
        {"choices": []},
        {"choices": [42]},
        {"choices": [{"finish_reason": "stop", "message": "invalid"}]},
        {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": "```json\n{}\n```"}}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": 4}}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": "{}", "tool_calls": [1]}}]},
        envelope({**ADVICE, "extra": "execute command"}),
        envelope({**ADVICE, "summary": "x" * 401}),
        envelope({**ADVICE, "findings": [{**ADVICE["findings"][0], "finding_index": True}]}),
        envelope({**ADVICE, "findings": [{**ADVICE["findings"][0], "finding_index": 1}]}),
        envelope({**ADVICE, "findings": ADVICE["findings"] * 2}),
        envelope({**ADVICE, "findings": [{**ADVICE["findings"][0], "next_steps": []}]}),
        envelope({**ADVICE, "summary": "\u202e\u0000"}),
    ],
)
def test_malformed_or_unmatched_output_falls_back(enabled, report, bad):
    result = explainer_with(enabled, lambda _: httpx.Response(200, json=bad)).explain(report)
    assert result["status"] == "fallback" and result["reason"] == "invalid_response"


def test_response_size_limit_and_no_reasoning_persistence(enabled, report):
    result = explainer_with(enabled, lambda _: httpx.Response(200, content=b"x" * 65_537)).explain(
        report
    )
    assert result["status"] == "fallback" and result["reason"] == "response_too_large"
    data = envelope()
    data["choices"][0]["message"]["reasoning_content"] = "Private reasoning: " + KEY
    result = explainer_with(enabled, lambda _: httpx.Response(200, json=data)).explain(report)
    assert result["status"] == "completed" and "reasoning_content" not in json.dumps(result)
    assert KEY not in json.dumps(result)


def test_output_is_redacted_and_cannot_inject_issue_markers_or_mentions(enabled, report):
    advice = copy.deepcopy(ADVICE)
    advice["findings"][0]["explanation"] = (
        KEY + " <!-- ci-triage:fake --> @everyone [run](https://evil.test)\n<script>"
    )
    result = explainer_with(enabled, lambda _: httpx.Response(200, json=envelope(advice))).explain(
        report
    )
    assert result["status"] == "completed" and KEY not in json.dumps(result)
    markdown = explanation_markdown(result["findings"][0])
    assert "<!--" not in markdown and "<script>" not in markdown
    assert "@everyone" not in markdown and "https://evil.test" not in markdown


@pytest.mark.parametrize("provider_status", [200, 429])
def test_enrichment_is_cached_and_both_paths_complete_publication(
    enabled, database, auth, provider_status
):
    enabled.publish_mode = "github"
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(provider_status, json=envelope())

    fake = FakeGitHub()
    with TestClient(
        create_app(enabled, database, fake, explainer_with(enabled, handler))
    ) as client:
        send(client, enabled)
        claim = client.post("/internal/claim", headers=auth).json()
        assert client.post("/internal/enrich", json=claim).status_code == 401
        assert client.post("/internal/enrich", json=claim, headers=auth).status_code == 409
        original = client.post("/internal/analyze", json=claim, headers=auth).json()["report"]
        for _ in range(2):
            response = client.post("/internal/enrich", json=claim, headers=auth)
            assert response.status_code == 200
        enriched = response.json()["report"]
        assert len(calls) == 1
        for key in ("fingerprint", "category", "evidence"):
            assert enriched["findings"][0][key] == original["findings"][0][key]
        for stage in ("publish", "enrich", "notify", "complete"):
            response = client.post("/internal/" + stage, json=claim, headers=auth)
            assert response.status_code == 200
        row = client.get("/internal/runs", headers=auth).json()["runs"][0]
        report = row["report"]
        assert row["status"] == "done" and row["last_error"] is None
        assert len(fake.issues) == 1 and len(calls) == 1
        if provider_status == 200:
            assert "AI-assisted investigation" in fake.issues[1]["body"]
            assert "AI summary" in report["telegram"]["text"]
        else:
            assert "AI-assisted investigation" not in fake.issues[1]["body"]
            assert report["llm"]["status"] == "fallback"
        assert report["run_url"] in report["telegram"]["text"]
        assert fake.issues[1]["html_url"] in report["telegram"]["text"]
        send(client, enabled, payload("success"))
        recovered, _ = process(client, auth)
        assert recovered["llm"]["status"] == "skipped" and len(calls) == 1
        assert recovered["publication"]["actions"][0]["action"] == "recovered"


def test_already_published_report_is_not_rewritten_by_new_stage(client, settings, auth):
    send(client, settings)
    claim = client.post("/internal/claim", headers=auth).json()
    client.post("/internal/analyze", json=claim, headers=auth)
    published = client.post("/internal/publish", json=claim, headers=auth).json()["report"]
    after = client.post("/internal/enrich", json=claim, headers=auth).json()["report"]
    assert after == published and "llm" not in after


def test_long_notification_keeps_run_and_first_five_issue_links(settings, database, auth):
    settings.publish_mode = "github"
    fake = FakeGitHub()
    with TestClient(create_app(settings, database, fake)) as client:
        send(client, settings)
        claim = client.post("/internal/claim", headers=auth).json()
        report = client.post("/internal/analyze", json=claim, headers=auth).json()["report"]
        report["findings"] = [
            {**report["findings"][0], "fingerprint": f"{index:032x}", "evidence": "x" * 1_000}
            for index in range(20)
        ]
        with database.transaction() as conn:
            conn.execute(runs.update().values(report=json.dumps(report)))
        assert client.post("/internal/publish", json=claim, headers=auth).status_code == 200
        notified = client.post("/internal/notify", json=claim, headers=auth).json()["report"]
        text = notified["telegram"]["text"]
        assert len(text) <= 3_800 and report["run_url"] in text
        assert all(fake.issues[number]["html_url"] in text for number in range(1, 6))
        assert fake.issues[6]["html_url"] not in text
        with database.transaction() as conn:
            assert conn.execute(sa.select(runs.c.notified)).scalar_one() is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"llm_enabled": True},
        {"llm_base_url": "http://api.test/v1"},
        {"llm_base_url": "https://user:password@api.test/v1"},
        {"llm_base_url": "https://api.test/v1?token=secret"},
        {"llm_model": ""},
        {"llm_timeout_seconds": 100},
        {"llm_max_output_tokens": 100_000},
    ],
)
def test_invalid_llm_configuration_is_rejected(overrides):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, webhook_secret="w" * 64, internal_api_key="i" * 64, **overrides)
