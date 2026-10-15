import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from ci_triage.api import create_app
from ci_triage.db import incidents

from .conftest import FakeGitHub, payload, process, send


def live_app(settings, database):
    settings.triage_mode = "live"
    settings.publish_mode = "github"
    github = FakeGitHub()
    return create_app(settings, database, github), github


def test_repeated_failure_updates_one_issue_and_retries_are_idempotent(settings, database, auth):
    app, github = live_app(settings, database)
    with TestClient(app) as client:
        send(client, settings)
        _, first = process(client, auth)
        assert len(github.issues) == 1
        assert github.comments[1] == []
        send(client, settings, payload("repeat-dependency"))
        _, second = process(client, auth)
        assert len(github.issues) == 1
        assert len(github.comments[1]) == 1
        assert first["run_key"] != second["run_key"]
        with database.transaction() as conn:
            assert conn.execute(sa.select(incidents.c.occurrences)).scalar_one() == 2


def test_ambiguous_issue_creation_timeout_is_reconciled(settings, database, auth):
    app, github = live_app(settings, database)
    github.fail_after_create = True
    with TestClient(app) as client:
        send(client, settings)
        claim = client.post("/internal/claim", headers=auth).json()
        assert client.post("/internal/analyze", headers=auth, json=claim).status_code == 200
        assert client.post("/internal/publish", headers=auth, json=claim).status_code == 502
        assert len(github.issues) == 1
        assert client.post("/internal/publish", headers=auth, json=claim).status_code == 200
        assert client.post("/internal/publish", headers=auth, json=claim).status_code == 200
        assert len(github.issues) == 1
        assert github.comments[1] == []
        with database.transaction() as conn:
            assert conn.execute(sa.select(incidents.c.occurrences)).scalar_one() == 1


def test_configured_closure_and_stale_failure_does_not_reopen(
    settings,
    database,
    auth,
):
    app, github = live_app(settings, database)
    settings.auto_close_on_success = True
    with TestClient(app) as client:
        send(client, settings)
        process(client, auth)
        send(client, settings, payload("success"))
        process(client, auth)
        assert github.issues[1]["state"] == "closed"
        assert len(github.comments[1]) == 1
        send(client, settings, payload("repeat-dependency"))
        report, _ = process(client, auth)
        assert report["publication"]["actions"][0]["action"] == "ignored_stale_failure"
        assert github.issues[1]["state"] == "closed"


def test_older_success_does_not_recover_a_newer_failure(settings, database, auth):
    app, github = live_app(settings, database)
    with TestClient(app) as client:
        send(client, settings)
        process(client, auth)
        # A genuine run with an older run number is processed after the failure.
        github.cases["success"]["payload"]["workflow_run"]["run_number"] = 1
        body = payload("success")
        body["workflow_run"]["run_number"] = 1
        send(client, settings, body)
        report, _ = process(client, auth)
        assert report["publication"]["actions"] == []
        assert github.comments[1] == []


@pytest.mark.parametrize(("field", "value"), [("head_branch", "release"), ("workflow_id", 99)])
def test_success_does_not_recover_another_scope(settings, database, auth, field, value):
    app, github = live_app(settings, database)
    with TestClient(app) as client:
        send(client, settings)
        process(client, auth)
        github.cases["success"]["payload"]["workflow_run"][field] = value
        body = payload("success")
        body["workflow_run"][field] = value
        send(client, settings, body)
        report, _ = process(client, auth)
        assert report["publication"]["actions"] == []
        assert github.comments[1] == []


def test_default_recovery_comments_without_closing_the_issue(settings, database, auth):
    app, github = live_app(settings, database)
    with TestClient(app) as client:
        send(client, settings)
        process(client, auth)
        send(client, settings, payload("success"))
        report, _ = process(client, auth)
        assert report["publication"]["actions"][0]["action"] == "recovered"
        assert github.issues[1]["state"] == "open"
        assert len(github.comments[1]) == 1
