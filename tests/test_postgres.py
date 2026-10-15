import os
from concurrent.futures import ThreadPoolExecutor

import pytest
import sqlalchemy as sa

from ci_triage.db import Database, deliveries, metadata, runs
from ci_triage.models import WorkflowEvent

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
