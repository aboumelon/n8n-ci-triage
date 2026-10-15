import json
import time
from concurrent.futures import ThreadPoolExecutor

from ci_triage.db import runs

from .conftest import payload, process, send


def test_unsigned_and_modified_webhooks_are_rejected(client, settings):
    assert client.post("/webhooks/github", json=payload()).status_code == 401
    assert send(client, settings, **{"X-Hub-Signature-256": "sha256=bad"}).status_code == 401
    assert client.get("/internal/runs").status_code == 401


def test_repo_allowlist_and_payload_validation(client, settings):
    body = payload()
    body["repository"]["full_name"] = "other/repo"
    assert send(client, settings, body).status_code == 403
    body["repository"]["full_name"] = "../../admin"
    assert send(client, settings, body).status_code == 422
    body = payload()
    del body["workflow_run"]["head_sha"]
    assert send(client, settings, body).status_code == 422


def test_oversized_payload_is_bounded(client, settings):
    response = client.post("/webhooks/github", content=b"x" * (settings.max_webhook_bytes + 1))
    assert response.status_code == 413


def test_incomplete_and_cancelled_runs_do_not_enter_the_queue(client, settings, auth):
    body = payload()
    body["action"] = "requested"
    assert send(client, settings, body).json()["status"] == "ignored"
    body["action"] = "completed"
    body["workflow_run"]["conclusion"] = "cancelled"
    assert send(client, settings, body).json()["status"] == "ignored"
    assert client.post("/internal/claim", headers=auth).json() == {"has_work": False}


def test_deduplication_for_delivery_and_run_attempt(client, settings, auth):
    first = send(client, settings, delivery="fixed-delivery")
    assert first.status_code == 202
    assert first.json()["status"] == "queued"
    assert send(client, settings, delivery="fixed-delivery").json()["status"] == "duplicate"
    assert send(client, settings).json()["status"] == "duplicate"
    changed = payload()
    changed["workflow_run"]["run_attempt"] = 2
    assert send(client, settings, changed, delivery="fixed-delivery").status_code == 409
    assert send(client, settings, changed).json()["status"] == "queued"
    assert len(client.get("/internal/runs", headers=auth).json()["runs"]) == 2


def test_full_demo_pipeline_and_stage_order(client, settings, auth):
    send(client, settings)
    claim = client.post("/internal/claim", headers=auth).json()
    assert client.post("/internal/publish", headers=auth, json=claim).status_code == 409
    assert client.post("/internal/complete", headers=auth, json=claim).status_code == 409
    for stage in ("analyze", "enrich", "publish", "notify", "complete"):
        response = client.post("/internal/" + stage, headers=auth, json=claim)
        assert response.status_code == 200, response.text
    assert client.post("/internal/complete", headers=auth, json=claim).status_code == 200
    row = client.get("/internal/runs", headers=auth).json()["runs"][0]
    assert row["status"] == "done"
    assert "lease_token" not in row
    assert row["report"]["findings"][0]["category"] == "dependency"
    assert row["report"]["publication"]["mode"] == "draft"
    assert row["report"]["telegram"]["mode"] == "draft"
    assert client.post("/internal/claim", headers=auth).json() == {"has_work": False}


def test_success_recovery_does_not_require_a_failed_log(client, settings, auth):
    send(client, settings)
    process(client, auth)
    send(client, settings, payload("success"))
    report, _ = process(client, auth)
    assert report["findings"] == []
    assert report["publication"]["actions"][0]["action"] == "recovered"


def test_expired_worker_cannot_acknowledge_reclaimed_work(client, settings, database, auth):
    send(client, settings)
    old = client.post("/internal/claim", headers=auth).json()
    with database.transaction() as conn:
        conn.execute(runs.update().values(lease_until=time.time() - 1))
    new = client.post("/internal/claim", headers=auth).json()
    assert new["lease_token"] != old["lease_token"]
    assert client.post("/internal/analyze", headers=auth, json=old).status_code == 409
    assert client.post("/internal/analyze", headers=auth, json=new).status_code == 200


def test_exhausted_work_is_dead_lettered_and_can_be_replayed(client, settings, database, auth):
    send(client, settings)
    with database.transaction() as conn:
        conn.execute(runs.update().values(processing_attempts=settings.max_processing_attempts))
    assert client.post("/internal/claim", headers=auth).json()["has_work"] is False
    row = client.get("/internal/runs", headers=auth).json()["runs"][0]
    assert row["status"] == "dead"
    assert client.post(f"/internal/runs/{row['key']}/retry", headers=auth).status_code == 200
    report, _ = process(client, auth)
    assert report["findings"][0]["category"] == "dependency"


def test_unknown_demo_id_keeps_failed_processing_durable(client, settings, auth):
    body = payload()
    body["workflow_run"]["id"] = 99999
    send(client, settings, body)
    claim = client.post("/internal/claim", headers=auth).json()
    response = client.post("/internal/analyze", headers=auth, json=claim)
    assert response.status_code == 502
    row = client.get("/internal/runs", headers=auth).json()["runs"][0]
    assert row["status"] == "processing"
    assert "Unknown demo run ID" in row["last_error"]


def test_concurrent_claimers_cannot_take_the_same_run(database, settings):
    from ci_triage.models import WorkflowEvent

    database.ingest(WorkflowEvent.model_validate(payload()), "one", "hash")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: database.claim(600, 5), range(8)))
    assert sum(result["has_work"] for result in results) == 1


def test_reports_never_contain_raw_log_payload(client, settings, auth):
    send(client, settings)
    report, _ = process(client, auth)
    assert 'log"' not in json.dumps(report)
    assert "evidence" in json.dumps(report)
