import copy
import hashlib
import hmac
import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ci_triage.api import create_app
from ci_triage.config import Settings
from ci_triage.db import Database
from ci_triage.github import DemoGitHub, UpstreamError

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "src/ci_triage/fixtures/scenarios.json").read_text())


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        webhook_secret="w" * 64,
        internal_api_key="i" * 64,
        database_url=f"sqlite:///{tmp_path}/triage.sqlite3",
        n8n_dispatch_url="",
    )


@pytest.fixture
def database(settings):
    db = Database(settings.database_url)
    db.initialize()
    yield db
    db.engine.dispose()


@pytest.fixture
def client(settings, database):
    with TestClient(create_app(settings, database)) as client:
        yield client


@pytest.fixture
def auth(settings):
    return {"X-Triage-Key": settings.internal_api_key.get_secret_value()}


def payload(scenario="dependency"):
    return copy.deepcopy(CASES[scenario]["payload"])


def send(client, settings, body=None, delivery=None, **headers):
    raw = json.dumps(body or payload()).encode()
    signature = (
        "sha256="
        + hmac.new(
            settings.webhook_secret.get_secret_value().encode(), raw, hashlib.sha256
        ).hexdigest()
    )
    return client.post(
        "/webhooks/github",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "workflow_run",
            "X-GitHub-Delivery": delivery or str(uuid.uuid4()),
            "X-Hub-Signature-256": signature,
            **headers,
        },
    )


def process(client, auth):
    response = client.post("/internal/claim", headers=auth)
    assert response.status_code == 200, response.text
    claim = response.json()
    assert claim["has_work"]
    context = {key: claim[key] for key in ("run_key", "lease_token")}
    report = None
    for stage in ("analyze", "enrich", "publish", "notify", "complete"):
        response = client.post("/internal/" + stage, json=context, headers=auth)
        assert response.status_code == 200, response.text
        if "report" in response.json():
            report = response.json()["report"]
    return report, context


class FakeGitHub(DemoGitHub):
    def __init__(self):
        super().__init__()
        self.issues = {}
        self.comments = {}
        self.fail_after_create = False

    def find_issue(self, repo, marker):
        return next((issue for issue in self.issues.values() if marker in issue["body"]), None)

    def create_issue(self, repo, title, body):
        number = len(self.issues) + 1
        issue = {
            "number": number,
            "html_url": f"https://github.com/{repo}/issues/{number}",
            "title": title,
            "body": body,
            "state": "open",
        }
        self.issues[number] = issue
        self.comments[number] = []
        if self.fail_after_create:
            self.fail_after_create = False
            raise UpstreamError("GitHub request failed after accepting an issue")
        return issue

    def get_issue(self, repo, number):
        return self.issues[number]

    def comment_once(self, repo, number, body, marker):
        if marker not in self.issues[number]["body"] and not any(
            marker in comment for comment in self.comments[number]
        ):
            self.comments[number].append(body)

    def set_issue_state(self, repo, number, state):
        self.issues[number]["state"] = state
