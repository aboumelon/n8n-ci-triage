import httpx
import pytest

from ci_triage.github import GitHub, UpstreamError


def make_github(settings, handler, storage_handler=None):
    api = httpx.Client(
        base_url="https://api.github.com",
        headers={"Authorization": "Bearer private"},
        transport=httpx.MockTransport(handler),
    )
    storage = httpx.Client(transport=httpx.MockTransport(storage_handler or handler))
    return GitHub(settings, api, storage)


def test_attempt_specific_jobs_are_paginated(settings):
    seen = []

    def handler(request):
        seen.append(request)
        assert request.url.path == "/repos/acme/app/actions/runs/5/attempts/2/jobs"
        count = 100 if request.url.params["page"] == "1" else 1
        return httpx.Response(200, json={"jobs": [{"id": n} for n in range(count)]})

    github = make_github(settings, handler)
    assert len(github.jobs({"repo": "acme/app", "run_id": 5, "attempt": 2})) == 101
    assert len(seen) == 2


def test_log_redirect_does_not_forward_github_credentials_and_retains_bounded_tail(settings):
    settings.max_log_bytes = 20

    def api(request):
        assert request.headers["authorization"] == "Bearer private"
        return httpx.Response(
            302, headers={"Location": "https://logs.blob.core.windows.net/signed-log?sig=private"}
        )

    def storage(request):
        assert "authorization" not in request.headers
        return httpx.Response(200, content=b"a" * 100 + b"AssertionError: fail")

    github = make_github(settings, api, storage)
    log, truncated, available = github.log("acme/app", 12)
    assert truncated and available and len(log.encode()) == 20
    assert log.endswith("AssertionError: fail")


def test_unexpected_redirect_is_rejected(settings):
    github = make_github(
        settings,
        lambda request: httpx.Response(302, headers={"Location": "http://127.0.0.1/private"}),
    )
    with pytest.raises(UpstreamError, match="unexpected"):
        github.log("acme/app", 12)


def test_missing_logs_are_explicitly_unavailable(settings):
    github = make_github(settings, lambda request: httpx.Response(410))
    _, truncated, available = github.log("acme/app", 12)
    assert not truncated and not available


def test_rate_limit_failure_does_not_leak_headers_or_tokens(settings):
    github = make_github(settings, lambda request: httpx.Response(403, text="secret upstream body"))
    with pytest.raises(UpstreamError) as exc:
        github.jobs({"repo": "acme/app", "run_id": 5, "attempt": 1})
    assert str(exc.value) == "GitHub returned HTTP 403"
