import hashlib
import hmac
import json
import logging
import re
from contextlib import asynccontextmanager

import httpx
import sqlalchemy as sa
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .config import Settings
from .db import Conflict, Database, runs
from .github import DemoGitHub, GitHub, UpstreamError
from .models import Claim, WorkflowEvent
from .service import TriageService

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, database=None, github=None) -> FastAPI:
    settings = settings or Settings()
    db = database or Database(settings.database_url)
    github = github or (DemoGitHub() if settings.triage_mode == "demo" else GitHub(settings))
    service = TriageService(settings, db, github)

    @asynccontextmanager
    async def lifespan(app):
        db.initialize()
        yield
        github.close()
        db.engine.dispose()

    app = FastAPI(
        title="CI Triage",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.database, app.state.service = db, service

    def authorize(x_triage_key: str = Header(default="")):
        if not hmac.compare_digest(
            x_triage_key.encode(), settings.internal_api_key.get_secret_value().encode()
        ):
            raise HTTPException(401, "Invalid internal API key")

    @app.exception_handler(Conflict)
    async def handle_conflict(request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    def wake_n8n():
        if not settings.n8n_dispatch_url:
            return
        try:
            response = httpx.post(
                settings.n8n_dispatch_url,
                json={"event": "queue-ready"},
                headers={"X-Triage-Key": settings.internal_api_key.get_secret_value()},
                timeout=3,
                trust_env=False,
            )
            if response.status_code >= 300:
                logger.warning(
                    "n8n wake-up failed (HTTP %s); the run remains queued", response.status_code
                )
        except httpx.HTTPError:
            logger.warning("n8n is unreachable; the run remains queued")

    @app.get("/healthz")
    def health():
        with db.transaction() as conn:
            conn.execute(sa.text("SELECT 1"))
        return {"status": "ok"}

    @app.post("/webhooks/github", status_code=202)
    async def webhook(request: Request, background: BackgroundTasks):
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > settings.max_webhook_bytes:
                raise HTTPException(413, "Webhook body is too large")
            chunks.append(chunk)
        raw = b"".join(chunks)
        expected = (
            "sha256="
            + hmac.new(
                settings.webhook_secret.get_secret_value().encode(), raw, hashlib.sha256
            ).hexdigest()
        )
        if not hmac.compare_digest(
            request.headers.get("x-hub-signature-256", "").encode(), expected.encode()
        ):
            raise HTTPException(401, "Invalid GitHub webhook signature")
        event_type = request.headers.get("x-github-event", "")
        if event_type == "ping":
            return {"status": "pong"}
        if event_type != "workflow_run":
            return {"status": "ignored", "reason": "Unsupported event"}
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(422, "Expected a JSON object") from exc
        if payload.get("action") != "completed":
            return {"status": "ignored", "reason": "Run is not completed"}
        try:
            event = WorkflowEvent.model_validate(payload)
        except ValidationError as exc:
            # Validation details may echo untrusted values. Do not return the raw body.
            raise HTTPException(422, "Invalid workflow_run payload") from exc
        if event.repository.full_name not in settings.repository_allowlist:
            raise HTTPException(403, "Repository is not allowlisted")
        if event.workflow_run.status != "completed" or event.workflow_run.conclusion not in (
            "failure",
            "timed_out",
            "success",
        ):
            return {"status": "ignored", "reason": "Unsupported run conclusion"}
        delivery = request.headers.get("x-github-delivery", "")
        if not re.fullmatch(r"[A-Za-z0-9-]{1,100}", delivery):
            raise HTTPException(422, "Missing or invalid X-GitHub-Delivery")
        key, created = db.ingest(event, delivery, hashlib.sha256(raw).hexdigest())
        background.add_task(wake_n8n)
        return {"status": "queued" if created else "duplicate", "run_key": key}

    internal = [Depends(authorize)]

    @app.post("/internal/claim", dependencies=internal)
    def claim():
        return db.claim(settings.lease_seconds, settings.max_processing_attempts)

    def execute_stage(name: str, claim: Claim):
        try:
            return getattr(service, name)(claim)
        except UpstreamError as exc:
            with db.transaction("run:" + claim.run_key) as conn:
                conn.execute(
                    runs.update()
                    .where(
                        runs.c.key == claim.run_key,
                        runs.c.lease_token == claim.lease_token,
                        runs.c.status == "processing",
                    )
                    .values(last_error=str(exc))
                )
            raise HTTPException(502, str(exc)) from exc

    @app.post("/internal/analyze", dependencies=internal)
    def analyze(claim: Claim):
        return execute_stage("analyze", claim)

    @app.post("/internal/publish", dependencies=internal)
    def publish(claim: Claim):
        return execute_stage("publish", claim)

    @app.post("/internal/notify", dependencies=internal)
    def notify(claim: Claim):
        return execute_stage("notify", claim)

    @app.post("/internal/complete", dependencies=internal)
    def complete(claim: Claim):
        return service.complete(claim)

    @app.get("/internal/runs", dependencies=internal)
    def history():
        result = db.list_runs()
        for row in result:
            row["report"] = json.loads(row["report"]) if row["report"] else None
        return {"runs": result}

    @app.post("/internal/runs/{run_key}/retry", dependencies=internal)
    def retry(run_key: str, background: BackgroundTasks):
        db.retry(run_key)
        background.add_task(wake_n8n)
        return {"status": "queued", "run_key": run_key}

    return app
