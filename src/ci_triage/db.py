import contextlib
import hashlib
import json
import threading
import time
import uuid
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.engine import make_url

metadata = sa.MetaData()
runs = sa.Table(
    "triage_runs",
    metadata,
    sa.Column("key", sa.String(32), primary_key=True),
    sa.Column("repo", sa.String(250), nullable=False),
    sa.Column("run_id", sa.BigInteger, nullable=False),
    sa.Column("attempt", sa.Integer, nullable=False),
    sa.Column("run_number", sa.Integer, nullable=False),
    sa.Column("workflow_id", sa.BigInteger, nullable=False),
    sa.Column("workflow_name", sa.String(250), nullable=False),
    sa.Column("branch", sa.String(250), nullable=False),
    sa.Column("head_sha", sa.String(40), nullable=False),
    sa.Column("conclusion", sa.String(30), nullable=False),
    sa.Column("run_url", sa.Text, nullable=False),
    sa.Column("status", sa.String(20), nullable=False),
    sa.Column("processing_attempts", sa.Integer, nullable=False, default=0),
    sa.Column("lease_token", sa.String(64)),
    sa.Column("lease_until", sa.Float),
    sa.Column("last_error", sa.Text),
    sa.Column("report", sa.Text),
    sa.Column("published", sa.Boolean, nullable=False, default=False),
    sa.Column("notified", sa.Boolean, nullable=False, default=False),
    sa.Column("created_at", sa.Float, nullable=False),
    sa.Column("updated_at", sa.Float, nullable=False),
    sa.UniqueConstraint("repo", "run_id", "attempt"),
    sa.Index("triage_queue_status", "status", "lease_until", "created_at"),
)
deliveries = sa.Table(
    "triage_deliveries",
    metadata,
    sa.Column("id", sa.String(100), primary_key=True),
    sa.Column("body_hash", sa.String(64), nullable=False),
    sa.Column("run_key", sa.String(32), sa.ForeignKey("triage_runs.key"), nullable=False),
)
incidents = sa.Table(
    "triage_incidents",
    metadata,
    sa.Column("fingerprint", sa.String(32), primary_key=True),
    sa.Column("repo", sa.String(250), nullable=False),
    sa.Column("workflow_id", sa.BigInteger, nullable=False),
    sa.Column("branch", sa.String(250), nullable=False),
    sa.Column("job", sa.String(250), nullable=False),
    sa.Column("issue_number", sa.Integer),
    sa.Column("issue_url", sa.Text),
    sa.Column("state", sa.String(20), nullable=False),
    sa.Column("run_number", sa.Integer, nullable=False),
    sa.Column("attempt", sa.Integer, nullable=False),
    sa.Column("occurrences", sa.Integer, nullable=False),
)
effects = sa.Table(
    "triage_effects",
    metadata,
    sa.Column("key", sa.String(150), primary_key=True),
    sa.Column("result", sa.Text, nullable=False),
)


class Conflict(Exception):
    pass


class Database:
    def __init__(self, url: str):
        parsed = make_url(url)
        if parsed.drivername == "sqlite" and parsed.database not in (None, ":memory:"):
            Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
        self.engine = sa.create_engine(url, pool_pre_ping=True)
        self.sqlite = self.engine.dialect.name == "sqlite"
        self._lock = threading.RLock()

    def initialize(self):
        metadata.create_all(self.engine)

    @contextlib.contextmanager
    def transaction(self, lock_key: str | None = None):
        with (
            self._lock if self.sqlite else contextlib.nullcontext(),
            self.engine.begin() as conn,
        ):
            if lock_key and not self.sqlite:
                key = int.from_bytes(
                    hashlib.sha256(lock_key.encode()).digest()[:8], "big", signed=True
                )
                conn.execute(sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
            yield conn

    def ingest(self, event, delivery: str, body_hash: str) -> tuple[str, bool]:
        wr, repo = event.workflow_run, event.repository.full_name
        with self.transaction("ingest:" + repo) as conn:
            prior = (
                conn.execute(sa.select(deliveries).where(deliveries.c.id == delivery))
                .mappings()
                .first()
            )
            if prior:
                if prior["body_hash"] != body_hash:
                    raise Conflict("A delivery ID cannot be reused with a different body")
                return prior["run_key"], False
            prior_run = conn.execute(
                sa.select(runs.c.key).where(
                    runs.c.repo == repo, runs.c.run_id == wr.id, runs.c.attempt == wr.run_attempt
                )
            ).scalar_one_or_none()
            key, now = prior_run or uuid.uuid4().hex, time.time()
            if not prior_run:
                conn.execute(
                    runs.insert().values(
                        key=key,
                        repo=repo,
                        run_id=wr.id,
                        attempt=wr.run_attempt,
                        run_number=wr.run_number,
                        workflow_id=wr.workflow_id,
                        workflow_name=wr.name,
                        branch=wr.head_branch,
                        head_sha=wr.head_sha,
                        conclusion=wr.conclusion,
                        run_url=f"https://github.com/{repo}/actions/runs/{wr.id}/attempts/{wr.run_attempt}",
                        status="pending",
                        processing_attempts=0,
                        created_at=now,
                        updated_at=now,
                        published=False,
                        notified=False,
                    )
                )
            conn.execute(deliveries.insert().values(id=delivery, body_hash=body_hash, run_key=key))
            return key, not bool(prior_run)

    def claim(self, lease_seconds: int, max_attempts: int) -> dict:
        now = time.time()
        with self.transaction("claim") as conn:
            eligible = sa.or_(
                runs.c.status == "pending",
                sa.and_(runs.c.status == "processing", runs.c.lease_until < now),
            )
            conn.execute(
                runs.update()
                .where(eligible, runs.c.processing_attempts >= max_attempts)
                .values(status="dead", lease_token=None, updated_at=now)
            )
            query = sa.select(runs).where(eligible, runs.c.processing_attempts < max_attempts)
            row = (
                conn.execute(
                    query.order_by(runs.c.created_at).limit(1).with_for_update(skip_locked=True)
                )
                .mappings()
                .first()
            )
            if not row:
                return {"has_work": False}
            token = uuid.uuid4().hex
            conn.execute(
                runs.update()
                .where(runs.c.key == row["key"])
                .values(
                    status="processing",
                    lease_token=token,
                    lease_until=now + lease_seconds,
                    processing_attempts=row["processing_attempts"] + 1,
                    updated_at=now,
                )
            )
            return {"has_work": True, "run_key": row["key"], "lease_token": token}

    @staticmethod
    def leased_run(conn, claim) -> dict:
        row = (
            conn.execute(sa.select(runs).where(runs.c.key == claim.run_key).with_for_update())
            .mappings()
            .first()
        )
        if not row or row["status"] != "processing" or row["lease_token"] != claim.lease_token:
            raise Conflict("This worker no longer owns the run")
        if row["lease_until"] < time.time():
            raise Conflict("This lease expired; wait for the queue to reclaim the run")
        return dict(row)

    @staticmethod
    def update(conn, key: str, **values):
        conn.execute(
            runs.update().where(runs.c.key == key).values(updated_at=time.time(), **values)
        )

    @staticmethod
    def effect(conn, key: str):
        value = conn.execute(
            sa.select(effects.c.result).where(effects.c.key == key)
        ).scalar_one_or_none()
        return json.loads(value) if value else None

    @staticmethod
    def save_effect(conn, key: str, result: dict):
        conn.execute(effects.insert().values(key=key, result=json.dumps(result)))

    def list_runs(self) -> list[dict]:
        with self.transaction() as conn:
            result = conn.execute(
                sa.select(runs).order_by(runs.c.created_at.desc()).limit(100)
            ).mappings()
            return [{k: v for k, v in row.items() if k != "lease_token"} for row in result]

    def retry(self, key: str):
        with self.transaction("run:" + key) as conn:
            result = conn.execute(
                runs.update()
                .where(runs.c.key == key, runs.c.status == "dead")
                .values(
                    status="pending",
                    processing_attempts=0,
                    lease_token=None,
                    lease_until=None,
                    updated_at=time.time(),
                )
            )
            if not result.rowcount:
                raise Conflict("Only dead-lettered runs can be replayed")
