import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from ci_triage.db import Database, deliveries, metadata, runs
from ci_triage.github import DemoGitHub
from ci_triage.llm import Explainer
from ci_triage.models import Claim, WorkflowEvent
from ci_triage.service import TriageService

from .conftest import payload


@pytest.fixture
def postgres():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL integration tests")
    if "test" not in (sa.engine.make_url(url).database or "").lower():
        pytest.fail("TEST_DATABASE_URL must use a disposable database with 'test' in its name")
    db = Database(url)
    assert not db.sqlite
    metadata.drop_all(db.engine)
    db.initialize()
    yield db
    metadata.drop_all(db.engine)
    db.engine.dispose()


def test_postgres_concurrent_redelivery_and_claims(postgres):
    event = WorkflowEvent.model_validate(payload())
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(lambda n: postgres.ingest(event, f"delivery-{n}", "hash"), range(8))
        )
    assert len({key for key, _ in results}) == 1
    assert sum(created for _, created in results) == 1
    with postgres.transaction() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(runs)).scalar_one() == 1
        assert conn.execute(sa.select(sa.func.count()).select_from(deliveries)).scalar_one() == 8
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: postgres.claim(600, 5), range(8)))
    assert sum(claim["has_work"] for claim in claims) == 1


def test_postgres_concurrent_enrichment_reuses_committed_model_result(postgres, settings):
    settings.llm_enabled = True
    settings.llm_api_key = SecretStr("simulated-key-not-a-live-credential")
    calls = []

    def handler(request):
        calls.append(request)
        time.sleep(0.1)
        advice = {
            "summary": "A Python import failed.",
            "findings": [
                {
                    "finding_index": 0,
                    "explanation": "The module was not importable.",
                    "likely_causes": ["The installed environment may differ from the manifest."],
                    "next_steps": ["Compare the CI install step with the lockfile."],
                }
            ],
        }
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(advice)}}]
            },
        )

    explainer = Explainer(settings, httpx.Client(transport=httpx.MockTransport(handler)))
    service = TriageService(settings, postgres, DemoGitHub(), explainer)
    postgres.ingest(WorkflowEvent.model_validate(payload()), "enrichment-run", "hash")
    claim = Claim.model_validate(postgres.claim(600, 5))
    try:
        service.analyze(claim)
        with ThreadPoolExecutor(max_workers=4) as pool:
            reports = list(pool.map(lambda _: service.enrich(claim)["report"], range(4)))
        assert len(calls) == 1
        assert all(report["llm"]["status"] == "completed" for report in reports)
        assert all(report == reports[0] for report in reports)
        assert reports[0]["findings"][0]["markdown"].count("AI-assisted investigation") == 1
    finally:
        explainer.close()
